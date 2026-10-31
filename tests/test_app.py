"""The three ports over real HTTP, with a fake store and a fake members."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from conftest import cookie
from dataservice import access, app
from dataservice.kinds import roboheard
from dataservice.store import Releases

MEMBER = {"preferred_username": "ann", "github_user_id": "7", "gateway_roles": ["data"]}
GRANTED = {"7": ["roboheard-2027"], "8": []}


class Ports:
    def __init__(self, service):
        self.servers = {}
        for name, handler in (("web", app.Web), ("internal", app.Internal), ("api", app.Api)):
            server = app.serve(handler, service, 0)
            self.servers[name] = server
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(self, port, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.servers[port].server_address[1], timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def close(self):
        for server in self.servers.values():
            server.shutdown()
            server.server_close()


@pytest.fixture
def service(store, competition, tmp_path):
    def ask(github):
        if github == "999":
            raise access.Unavailable("members")
        return GRANTED.get(github, [])

    return app.Service(
        {competition.id: competition},
        Releases(store, {"roboheard": roboheard.View}),
        access.Grants(ask=ask),
        access.Keys(str(tmp_path / "keys.sqlite3")),
    )


@pytest.fixture
def ports(service):
    found = Ports(service)
    yield found
    found.close()


def web(ports, path, claims=MEMBER, method="GET", headers=None, body=None):
    return ports.call("web", method, path, {"Cookie": cookie(claims), **(headers or {})}, body)


def action(ports, path, payload, claims=MEMBER, **headers):
    body = json.dumps(payload).encode()
    sent = {
        "Origin": app.ORIGIN,
        "Content-Type": "application/json",
        "Sec-Fetch-Site": "same-origin",
        **headers,
    }
    return web(ports, path, claims, "POST", sent, body)


def test_internal_catalog_and_health(ports):
    status, _, body = ports.call("internal", "GET", "/catalog")
    assert status == 200
    assert json.loads(body) == {
        "competitions": [{"id": "roboheard-2027", "title": "RoboHEARD 2027 训练集", "kind": "roboheard"}]
    }
    assert ports.call("internal", "GET", "/healthz")[0] == 200


def test_web_needs_a_token_and_the_role(ports):
    assert ports.call("web", "GET", "/")[0] == 401
    status, _, body = web(ports, "/", {**MEMBER, "gateway_roles": ["wallet"]})
    assert status == 403 and "没有开通数据集" in body.decode()


def test_web_lists_granted_competitions(ports):
    status, headers, body = web(ports, "/")
    page = body.decode()
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "media-src blob: 'self';" in headers["Content-Security-Policy"]
    assert 'href="/data/c/roboheard-2027/"' in page and "已发布" in page

    status, _, body = web(ports, "/", {**MEMBER, "github_user_id": "8"})
    assert status == 200 and "roboheard-2027" not in body.decode()


def test_row_fragment(ports):
    status, _, body = web(ports, "/row")
    assert status == 200 and body.decode().startswith('<tr><th scope="row"><a href="/data/">数据集</a>')
    assert "1 个比赛" in body.decode()
    assert 'pill muted">无<' in web(ports, "/row", {**MEMBER, "github_user_id": "8"})[2].decode()


def test_members_down_is_503(ports):
    down = {**MEMBER, "github_user_id": "999"}
    assert web(ports, "/", down)[0] == 503
    assert web(ports, "/api/roboheard-2027/overview", down)[0] == 503


def test_viewer_shell_and_json(ports):
    status, _, body = web(ports, "/c/roboheard-2027/")
    page = body.decode()
    assert status == 200
    assert 'data-api="/data/api/roboheard-2027"' in page and "/data/static/roboheard.js" in page
    assert "<script>" not in page  # no inline script: CSP script-src 'self'

    assert web(ports, "/c/roboheard-2027")[0] == 302
    status, _, body = web(ports, "/api/roboheard-2027/overview")
    assert status == 200 and json.loads(body)["queries"][0]["name"] == "overlap"
    assert web(ports, "/api/roboheard-2027/query/overlap?limit=999")[0] == 400
    assert web(ports, "/api/roboheard-2027/session/S404")[0] == 404


def test_ungranted_competition_is_not_found(ports):
    other = {**MEMBER, "github_user_id": "8"}
    assert web(ports, "/api/roboheard-2027/overview", other)[0] == 404
    assert web(ports, "/c/roboheard-2027/", other)[0] == 302
    assert web(ports, "/api/elsewhere/overview")[0] == 404


def test_static_files(ports):
    status, headers, _ = ports.call("web", "GET", "/static/roboheard.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    assert ports.call("web", "GET", "/static/../app.py")[0] == 404
    assert ports.call("web", "GET", "/static/catalog.json")[0] == 404


def test_keys_actions_and_api(ports):
    assert action(ports, "/actions/key", {"label": "g18"}, Origin="https://evil.example")[0] == 403
    assert action(ports, "/actions/key", {"label": "g18"}, **{"Sec-Fetch-Site": "cross-site"})[0] == 403
    status, _, body = action(ports, "/actions/key", {"label": "g18"})
    assert status == 200
    issued = json.loads(body)
    page = web(ports, "/keys")[2].decode()
    assert issued["id"] in page and issued["key"] not in page

    bearer = {"Authorization": f"Bearer {issued['key']}"}
    assert ports.call("api", "GET", "/v1/")[0] == 401
    status, _, body = ports.call("api", "GET", "/v1/", bearer)
    assert status == 200 and json.loads(body)["competitions"][0]["published"] is True
    status, _, body = ports.call("api", "GET", "/v1/roboheard-2027/files", bearer)
    assert "Audio/S001.wav" in {f["key"] for f in json.loads(body)["files"]}
    status, _, body = ports.call("api", "GET", "/v1/roboheard-2027/presign?key=Audio/S001.wav", bearer)
    url = json.loads(body)["url"]
    assert status == 200 and url.startswith(
        "https://data.api.soundadam.com/v1/roboheard-2027/raw/Audio/S001.wav?"
    )
    link = url.removeprefix("https://data.api.soundadam.com")
    status, headers, body = ports.call("api", "GET", link)  # no key: the link is the credential
    assert status == 200 and body == b"RIFF0123456789" and headers["Content-Type"] == "audio/wav"
    assert ports.call("api", "GET", "/v1/roboheard-2027/presign?key=../x", bearer)[0] == 404
    status, _, body = ports.call("api", "GET", "/v1/roboheard-2027/query/overlap", bearer)
    assert status == 200 and json.loads(body)["total"] == 2

    other = {**MEMBER, "github_user_id": "8"}
    assert action(ports, "/actions/revoke", {"id": issued["id"]}, other)[0] == 400
    assert action(ports, "/actions/revoke", {"id": issued["id"]})[0] == 200
    assert ports.call("api", "GET", "/v1/", bearer)[0] == 401


def test_api_follows_grants(ports, service):
    key_id, key = service.keys.issue("8", "nothing granted")
    bearer = {"Authorization": f"Bearer {key}"}
    status, _, body = ports.call("api", "GET", "/v1/", bearer)
    assert status == 200 and json.loads(body) == {"competitions": []}
    assert ports.call("api", "GET", "/v1/roboheard-2027/files", bearer)[0] == 404
    assert ports.call("api", "GET", "/v1/roboheard-2027/overview", bearer)[0] == 404


def test_media_streams_through_signed_links_with_ranges(ports):
    status, _, body = web(ports, "/api/roboheard-2027/session/S001")
    assert status == 200
    link = json.loads(body)["media"]["audio"]
    path = "/api/roboheard-2027/" + link

    status, headers, body = web(ports, path, headers={"Range": "bytes=4-7"})
    assert status == 206 and body == b"0123" and headers["Content-Range"] == "bytes 4-7/14"
    status, _, body = web(ports, path, headers={"Range": "bytes=-2"})
    assert status == 206 and body == b"89"
    assert web(ports, path, headers={"Range": "bytes=99-"})[0] == 416
    status, _, body = web(ports, path, headers={"Range": "bytes=0-1,4-5"})  # several ranges: the whole file
    assert status == 200 and body == b"RIFF0123456789"

    assert web(ports, path.replace("S001.wav", "S002.wav"))[0] == 403
    assert web(ports, path.replace("sig=", "sig=x"))[0] == 403
    assert web(ports, "/api/roboheard-2027/raw/Audio/S001.wav")[0] == 403
    assert ports.call("api", "GET", "/v1/roboheard-2027/" + link)[0] == 200
    assert ports.call("api", "GET", "/v1/other/" + link)[0] == 403
