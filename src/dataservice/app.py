"""The data service: three ports, one process.

- WEB_PORT (8080), my.soundadam.com/data/ behind SecurityPolicy `my`, which
  strips the /data prefix: the competitions a member holds, their personal
  keys, and each competition's viewer (static pages under /static/ calling
  the JSON under /api/<competition>/).
- INTERNAL_PORT (8081), for members only (NetworkPolicy): GET /catalog.
- API_PORT (8082), data.api with no edge policy: a personal key in
  `Authorization: Bearer`, then the same JSON under /v1/, plus a file
  listing and presigned GETs for downloads.

Every answer is filtered by the competitions members grants the asker
(access.Grants). Bytes never pass through here: audio, video and downloads
are presigned URLs on s3.api. Neither keys nor URLs are logged.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from dataservice import access, catalog, page
from dataservice.kinds import BadRequest, NotFound, roboheard
from dataservice.store import PUBLIC_ENDPOINT, NotPublished, Releases, Store

WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
INTERNAL_PORT = int(os.environ.get("INTERNAL_PORT", "8081"))
API_PORT = int(os.environ.get("API_PORT", "8082"))
ORIGIN = os.environ.get("PORTAL_ORIGIN", "https://my.soundadam.com")
STATE = Path(os.environ.get("STATE_DIR", "/var/lib/data"))
API_HOST = "https://data.api.soundadam.com"
MAX_BODY = 1024
VIEWS = {"roboheard": roboheard.View}
STATIC = resources.files(__package__).joinpath("static")
STATIC_NAME = re.compile(r"[a-z0-9-]+\.(?:js|css)")
S3_ORIGIN = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(PUBLIC_ENDPOINT))

SECURITY_HEADERS = {
    # Media and wav ranges come straight from s3.api; everything else is this origin.
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
        f"script-src 'self'; connect-src 'self' {S3_ORIGIN}; media-src blob: {S3_ORIGIN}; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


class Service:
    """What the three ports share."""

    def __init__(
        self,
        competitions: dict[str, catalog.Competition],
        releases: Releases,
        grants: access.Grants,
        keys: access.Keys,
    ):
        self.competitions = competitions
        self.releases = releases
        self.grants = grants
        self.keys = keys

    def granted(self, github: str) -> list[catalog.Competition]:
        allowed = self.grants.of(github)
        return [c for c in self.competitions.values() if c.id in allowed]

    def published(self, competition: catalog.Competition) -> bool:
        try:
            self.releases.get(competition)
        except NotPublished:
            return False
        return True

    def view(self, github: str, competition_id: str, path: list[str], query: dict) -> dict:
        """A kind's JSON answer, for a competition the asker holds."""
        competition = self.competitions.get(competition_id)
        if competition is None or competition.id not in self.grants.of(github):
            raise NotFound(competition_id)
        return self.releases.get(competition).view.answer(path, query)


class Base(BaseHTTPRequestHandler):
    server_version = "data"
    sys_version = ""
    service: Service

    def reply(self, status: int, content_type: str, body: bytes, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def answer(self, status: int, payload: dict) -> None:
        self.reply(status, "application/json", json.dumps(payload, ensure_ascii=False).encode())

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def json_view(self, github: str, competition_id: str, rest: list[str], query: dict) -> None:
        try:
            self.answer(200, self.service.view(github, competition_id, rest, query))
        except (NotFound, NotPublished):
            self.answer(404, {"error": "not found"})
        except BadRequest as error:
            self.answer(400, {"error": f"bad parameter {error}"})
        except access.Unavailable:
            self.answer(503, {"error": "grants unavailable"})
        except Exception as error:  # noqa: BLE001 - SeaweedFS or a release that does not load
            self.log_message("github:%s %s failed: %s", github, competition_id, type(error).__name__)
            self.answer(502, {"error": "storage unavailable"})

    def log_request(self, code="-", size="-") -> None:
        pass

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write((format % args) + "\n")


class Internal(Base):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.reply(200, "text/plain; charset=utf-8", b"ok\n")
        elif self.path == "/catalog":
            self.answer(200, {"competitions": [c.public() for c in self.service.competitions.values()]})
        else:
            self.answer(404, {"error": "not found"})


class Api(Base):
    """data.api: a personal key, then the asker's competitions."""

    def do_GET(self) -> None:  # noqa: N802
        split = urllib.parse.urlsplit(self.path)
        if split.path == "/healthz":
            self.reply(200, "text/plain; charset=utf-8", b"ok\n")
            return
        if not split.path.startswith("/v1/"):
            self.answer(404, {"error": "not found"})
            return
        try:
            github = self.service.keys.owner(self.headers.get("Authorization", ""))
        except access.Unauthenticated:
            self.answer(401, {"error": "a personal key from my.soundadam.com/data/keys is required"})
            return
        parts = [urllib.parse.unquote(p) for p in split.path.removeprefix("/v1/").split("/") if p]
        query = urllib.parse.parse_qs(split.query)
        self.log_message("github:%s api %s", github, "/".join(parts[:2]))
        try:
            if not parts:
                self.answer(
                    200,
                    {
                        "competitions": [
                            {**c.public(), "published": self.service.published(c)}
                            for c in self.service.granted(github)
                        ]
                    },
                )
                return
            if len(parts) == 2 and parts[1] in ("files", "presign"):
                self.files(github, parts[0], parts[1], query)
                return
        except access.Unavailable:
            self.answer(503, {"error": "grants unavailable"})
            return
        self.json_view(github, parts[0], parts[1:], query)

    def files(self, github: str, competition_id: str, what: str, query: dict) -> None:
        competition = self.service.competitions.get(competition_id)
        if competition is None or competition.id not in self.service.grants.of(github):
            self.answer(404, {"error": "not found"})
            return
        try:
            release = self.service.releases.get(competition)
        except NotPublished:
            self.answer(404, {"error": "not published"})
            return
        if what == "files":
            self.answer(200, {"files": [{"key": k, "size": v} for k, v in sorted(release.files.items())]})
            return
        url = release.url(query.get("key", [""])[0])
        if url is None:
            self.answer(404, {"error": "no such file"})
            return
        self.answer(200, {"url": url, "expires_in": 600})


class Web(Base):
    """my.soundadam.com/data/, the prefix stripped by the route."""

    def member(self) -> tuple[dict, str] | None:
        """(claims, github id) of a signed-in holder of `data`, or None once replied."""
        try:
            claims = access.access_token_claims(self.headers.get("Cookie", ""))
            github = access.github_id(claims)
        except access.Unauthenticated as error:
            self.log_message("refused: %s", error)
            self.reply(401, "text/plain; charset=utf-8", b"unauthenticated\n")
            return None
        if access.ROLE not in access.roles(claims):
            self.log_message("github:%s lacks role %s", github, access.ROLE)
            self.html(
                403,
                claims,
                "数据集",
                "没有开通数据集",
                '<section><p class="hint lead">在<a href="/">概要</a>申请一个比赛，所有者批准后就能看。</p></section>',
            )
            return None
        return claims, github

    def html(
        self, status: int, claims: dict, title: str, heading: str, body: str, trail: str = "", **extra
    ) -> None:
        cookies = self.headers.get("Cookie", "")
        document = page.page(
            claims_name=str(claims.get("preferred_username") or ""),
            held=access.roles(claims),
            theme=page.theme_of(cookies),
            hat=page.hat_of(cookies),
            title=title,
            heading=heading,
            trail=trail,
            body=body,
            **extra,
        )
        self.reply(status, "text/html; charset=utf-8", document.encode())

    def do_GET(self) -> None:  # noqa: N802
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        if path == "/healthz":
            self.reply(200, "text/plain; charset=utf-8", b"ok\n")
            return
        if path.startswith("/static/"):
            self.static(path.removeprefix("/static/"))
            return
        found = self.member()
        if found is None:
            return
        claims, github = found
        try:
            if path == "/row":
                self.row(github)
            elif path == "/":
                self.competitions_page(claims, github)
            elif path == "/keys":
                self.keys_page(claims, github)
            elif m := re.fullmatch(r"/c/([a-z0-9-]+)/?", path):
                self.viewer(claims, github, m.group(1), path.endswith("/"))
            elif path.startswith("/api/"):
                parts = [urllib.parse.unquote(p) for p in path.removeprefix("/api/").split("/") if p]
                if not parts:
                    self.answer(404, {"error": "not found"})
                else:
                    self.json_view(github, parts[0], parts[1:], urllib.parse.parse_qs(split.query))
            else:
                self.redirect(page.BASE + "/")
        except access.Unavailable:
            self.log_message("github:%s grants unavailable", github)
            self.reply(503, "text/plain; charset=utf-8", "暂时读不到授权，稍后再试。\n".encode())

    def static(self, name: str) -> None:
        if not STATIC_NAME.fullmatch(name) or not STATIC.joinpath(name).is_file():
            self.reply(404, "text/plain; charset=utf-8", b"not found\n")
            return
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.reply(200, f"{kind}; charset=utf-8", STATIC.joinpath(name).read_bytes(), "public, max-age=300")

    def row(self, github: str) -> None:
        """The item's row on the member overview (my/home ROW_HEADS)."""
        held = self.service.granted(github)
        status = (
            f'<span class="pill ok">{len(held)} 个比赛</span>'
            if held
            else '<span class="pill muted">未批准</span>'
        )
        body = (
            f'<tr><th scope="row"><a href="{page.BASE}/">{page.LABEL}</a></th>'
            f'<td>平台</td><td>{status}</td><td class="num zero">—</td></tr>'
        )
        self.reply(200, "text/html; charset=utf-8", body.encode())

    def competitions_page(self, claims: dict, github: str) -> None:
        held = self.service.granted(github)
        if not held:
            body = (
                '<section><p class="hint lead">还没有批准的比赛，在<a href="/">概要</a>申请。</p></section>'
            )
        else:
            rows = ""
            for c in held:
                try:
                    published = self.service.published(c)
                except Exception as error:  # noqa: BLE001
                    self.log_message("%s release unreadable: %s", c.id, type(error).__name__)
                    published = None
                state = (
                    '<span class="pill ok">已发布</span>'
                    if published
                    else '<span class="pill muted">暂时读不到</span>'
                    if published is None
                    else '<span class="pill muted">未发布</span>'
                )
                name = (
                    f'<a href="{page.BASE}/c/{c.id}/">{html.escape(c.title)}</a>'
                    if published
                    else html.escape(c.title)
                )
                rows += (
                    f'<tr><th scope="row">{name}</th><td><code>{c.id}</code></td>'
                    f"<td>{html.escape(c.license)}</td><td>{state}</td></tr>"
                )
            body = (
                '<section><div class="table-wrap"><table class="data"><thead><tr><th>比赛</th><th>id</th>'
                f"<th>许可</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
            )
        self.log_message("github:%s served /", github)
        self.html(200, claims, page.LABEL, page.LABEL, page.tabs("/") + body, page.crumbs(""))

    def keys_page(self, claims: dict, github: str) -> None:
        keys = self.service.keys.of(github)
        rows = "".join(
            f'<tr><th scope="row">{html.escape(k["label"])}</th><td><code>{k["id"]}</code></td>'
            f"<td>{k['created'][:10]}</td><td>{(k['used'] or '—')[:16].replace('T', ' ')}</td>"
            f'<td><button type="button" class="button compact danger" data-revoke="{k["id"]}">作废</button></td></tr>'
            for k in keys
        )
        none = '<p class="hint">还没有。</p>'
        listing = (
            '<div class="table-wrap"><table class="data"><thead><tr><th>名称</th><th>id</th><th>签发</th>'
            f"<th>最近使用 UTC</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"
            if keys
            else ""
        )
        body = (
            page.tabs("/keys")
            + f'<section id="issue"><h2>签发</h2><div class="fields"><label class="field"><span>名称</span>'
            '<input id="label" maxlength="40" placeholder="g18 训练"></label></div>'
            '<button type="button" class="button" id="mint">签发密钥</button></section>'
            '<section id="minted" hidden><h2>新密钥</h2><p class="hint">只显示这一次。</p>'
            '<div class="fields"><label class="field wide"><span>密钥</span><input id="key" readonly></label></div>'
            '<button type="button" class="button secondary" data-copy="key">复制</button></section>'
            f"<section><h2>我的密钥</h2>{listing or none}"
            f'<pre>curl -H "Authorization: Bearer $KEY" {API_HOST}/v1/\n'
            f'curl -H "Authorization: Bearer $KEY" {API_HOST}/v1/&lt;比赛&gt;/files\n'
            f'curl -H "Authorization: Bearer $KEY" "{API_HOST}/v1/&lt;比赛&gt;/presign?key=&lt;文件&gt;"</pre>'
            "</section>"
        )
        self.log_message("github:%s served /keys", github)
        self.html(
            200, claims, f"{page.LABEL} · 密钥", page.LABEL, body, page.crumbs(""), scripts=("keys.js",)
        )

    def viewer(self, claims: dict, github: str, competition_id: str, slash: bool) -> None:
        competition = self.service.competitions.get(competition_id)
        if competition is None or competition.id not in self.service.grants.of(github):
            self.redirect(page.BASE + "/")
            return
        if not slash:
            self.redirect(f"{page.BASE}/c/{competition.id}/")
            return
        self.log_message("github:%s served viewer %s", github, competition.id)
        self.html(
            200,
            claims,
            competition.title,
            html.escape(competition.title),
            '<div id="view" aria-live="polite"><div class="pending" aria-busy="true">'
            "<span></span><span></span><span></span><span></span></div></div>",
            page.crumbs("", competition.title),
            scripts=(f"{competition.kind}.js",),
            styles=(f"{competition.kind}.css",),
            data={"api": f"{page.BASE}/api/{competition.id}", "base": f"{page.BASE}/c/{competition.id}/"},
        )

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/actions/key", "/actions/revoke"):
            self.reply(404, "text/plain; charset=utf-8", b"not found\n")
            return
        body = self.action_body()
        if body is None:
            return
        found = self.member()
        if found is None:
            return
        _, github = found
        if path == "/actions/key":
            try:
                key_id, key = self.service.keys.issue(github, str(body.get("label", "")).strip())
            except ValueError as error:
                self.reply(400, "text/plain; charset=utf-8", str(error).encode())
                return
            self.log_message("github:%s issued key %s", github, key_id)
            self.answer(200, {"id": key_id, "key": key})
        else:
            key_id = str(body.get("id", ""))
            if not self.service.keys.revoke(github, key_id):
                self.reply(400, "text/plain; charset=utf-8", b"refused\n")
                return
            self.log_message("github:%s revoked key %s", github, key_id)
            self.answer(200, {"ok": True})

    def action_body(self) -> dict | None:
        """The JSON object of a same-origin action, or None once refused.

        The session is a cookie, so a cross-site page could otherwise post in
        the member's name: a browser always sends Origin on a POST, and a
        cross-site JSON body needs a CORS preflight this service never answers.
        """
        headers = self.headers
        try:
            length = int(headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        ok = (
            headers.get("Origin") == ORIGIN
            and headers.get("Sec-Fetch-Site", "same-origin") == "same-origin"
            and (headers.get("Content-Type") or "").split(";")[0].strip() == "application/json"
            and 0 <= length <= MAX_BODY
        )
        body = None
        if ok:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = None
        if not isinstance(body, dict):
            self.log_message("refused %s", self.path)
            self.reply(403, "text/plain; charset=utf-8", b"refused\n")
            return None
        return body


def serve(handler: type[Base], service: Service, port: int) -> ThreadingHTTPServer:
    bound = type(handler.__name__, (handler,), {"service": service})
    server = ThreadingHTTPServer(("", port), bound)
    server.daemon_threads = True
    return server


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    competitions = catalog.load(kinds=frozenset(VIEWS))
    service = Service(
        competitions, Releases(Store(), VIEWS), access.Grants(), access.Keys(str(STATE / "keys.sqlite3"))
    )
    servers = [
        serve(Web, service, WEB_PORT),
        serve(Internal, service, INTERNAL_PORT),
        serve(Api, service, API_PORT),
    ]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
