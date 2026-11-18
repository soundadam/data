"""Datasets published at run time (catalog/<id>.json in bucket datasets), and kind `listen`."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from conftest import FakeStore, cookie
from dataservice import access, app, catalog
from dataservice.store import Releases

MEMBER = {"preferred_username": "ann", "github_user_id": "7", "gateway_roles": ["data"]}
KINDS = frozenset(app.VIEWS)
BUILT_IN = catalog.Competition("roboheard-2027", "RoboHEARD 2027", "roboheard", "datasets", "produced/rh/")
BUNDLE = {
    "index.html": b'<!doctype html><script src="manifest.js"></script><script src="listen.js"></script>',
    "listen.js": b"// listen",
    "listen.css": b"body{}",
    "manifest.js": b"window.LISTEN = {};",
    "design/app.css": b"",
    "audio/a.mp3": b"ID3-0123456789",
    "spec/d000/0.png": b"\x89PNG",
    "wave/d000/0.js": b"wave()",
    "notes.bin": b"\0",
    "_SUCCESS": b"",
}


def entry(**fields) -> bytes:
    return json.dumps({"title": "试听 A", "kind": "listen", "prefix": "listen/a-v1/", **fields}).encode()


def runtime(store: FakeStore, files: dict[str, bytes]) -> catalog.Catalog:
    store.put(files, bucket=catalog.RUNTIME_BUCKET, prefix=catalog.RUNTIME)
    found = catalog.Catalog({BUILT_IN.id: BUILT_IN}, store, KINDS)
    found.refresh()
    return found


def test_runtime_entries_join_the_built_in_ones():
    found = runtime(FakeStore(), {"listen-a.json": entry(license="内部")}).current()
    assert set(found) == {"roboheard-2027", "listen-a"}
    assert found["listen-a"] == catalog.Competition(
        "listen-a", "试听 A", "listen", "datasets", "listen/a-v1/", "内部"
    )


@pytest.mark.parametrize(
    "files",
    [
        {"roboheard-2027.json": entry()},  # catalog.json has it
        {"Bad_Id.json": entry()},
        {"x.json": entry(kind="nope")},
        {"x.json": entry(prefix="../up/")},
        {"x.json": entry(prefix="catalog/x/")},
        {"x.json": entry(bucket="other")},
        {"x.json": entry(title=7)},
        {"x.json": b"{"},
        {"x.json": json.dumps({"title": "t"}).encode()},
        {"x.txt": entry(), "deep/x.json": entry()},
    ],
)
def test_a_refused_entry_is_left_out(files, capsys):
    found = runtime(FakeStore(), files).current()
    assert set(found) == {"roboheard-2027"}
    assert found["roboheard-2027"] == BUILT_IN


def test_store_away_keeps_the_last_listing():
    store = FakeStore()
    found = runtime(store, {"listen-a.json": entry()})

    def away(bucket, prefix):
        raise OSError("seaweedfs")

    store.listing = away
    found.refresh()
    assert "listen-a" in found.current()


def test_withdrawn_entry_goes_on_the_next_refresh():
    store = FakeStore()
    found = runtime(store, {"listen-a.json": entry()})
    del store.objects[(catalog.RUNTIME_BUCKET, catalog.RUNTIME + "listen-a.json")]
    found.refresh()
    assert "listen-a" not in found.current()


@pytest.fixture
def web(tmp_path):
    store = FakeStore()
    store.put(BUNDLE, bucket="datasets", prefix="listen/a-v1/")
    store.put({"index.html": b"<p>half</p>", "_SUCCESS": b""}, bucket="datasets", prefix="listen/b-v1/")
    competitions = runtime(store, {"listen-a.json": entry(), "listen-b.json": entry(prefix="listen/b-v1/")})
    service = app.Service(
        competitions,
        Releases(store, app.VIEWS),
        access.Grants(ask=lambda github: ["listen-a", "listen-b"] if github == "7" else []),
        access.Keys(str(tmp_path / "keys.sqlite3")),
    )
    server = app.serve(app.Web, service, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path, claims=MEMBER, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        connection.request("GET", path, headers={"Cookie": cookie(claims), **(headers or {})})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    yield get
    server.shutdown()
    server.server_close()


def test_listen_bundle_is_its_own_page(web):
    status, headers, body = web("/c/listen-a/")
    assert status == 200 and body == BUNDLE["index.html"]
    assert headers["Content-Type"].startswith("text/html")
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    for name, kind in [
        ("listen.js", "text/javascript"),
        ("wave/d000/0.js", "text/javascript"),
        ("spec/d000/0.png", "image/png"),
        ("design/app.css", "text/css"),
    ]:
        status, headers, body = web(f"/c/listen-a/{name}")
        assert status == 200 and headers["Content-Type"].startswith(kind) and body == BUNDLE[name]


def test_listen_audio_takes_a_range(web):
    status, headers, body = web("/c/listen-a/audio/a.mp3", headers={"Range": "bytes=3-"})
    assert status == 206 and body == b"-0123456789" and headers["Content-Type"] == "audio/mpeg"


@pytest.mark.parametrize(
    "path",
    [
        "/c/listen-a/nope.js",
        "/c/listen-a/notes.bin",
        "/c/listen-a/_SUCCESS",
        "/c/listen-a/../listen-b/index.html",
        "/c/roboheard-2027/index.html",
    ],
)
def test_only_the_bundles_files(web, path):
    assert web(path)[0] in (302, 404)


def test_listen_follows_grants(web):
    other = {**MEMBER, "github_user_id": "8"}
    assert web("/c/listen-a/", other)[0] == 302
    assert web("/c/listen-a/listen.js", other)[0] == 302


def test_a_prefix_that_is_no_bundle_does_not_open(web):
    assert web("/c/listen-b/")[0] == 502
    status, _, body = web("/")
    assert status == 200 and "暂时读不到" in body.decode()
