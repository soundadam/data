"""The console frame comes from my/home's /frame, kept a while, and the page opens without it."""

from __future__ import annotations

import http.server
import json
import threading

from conftest import FakeHome
from dataservice import page


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def render(frame, theme="dark", held=frozenset({"data", "wallet"})):
    return page.page(
        claims_name="ann",
        held=set(held),
        theme=theme,
        title="数据集",
        heading="数据集",
        trail=page.crumbs(),
        body="<section>body</section>",
        below=page.tabs("/"),
        frame=frame,
    )


def test_page_asks_home_for_this_member_and_item():
    home = FakeHome()
    document = render(page.Frame(ask=home))
    assert home.asked == [{"user": "ann", "held": "data,wallet", "theme": "dark", "current": "/data/"}]
    assert '<link rel="stylesheet" href="/_design/6.0.0/app.css">' in document
    assert 'data-theme="dark"' in document
    side = document.index('<aside class="side" data-user="ann"')
    main = document.index("<main>")
    assert document.index('<body class="shell">') < side < main
    assert main < document.index('<header class="head">') < document.index('<nav class="segmented"')


def test_unknown_theme_is_auto_on_the_page_and_to_home():
    home = FakeHome()
    document = render(page.Frame(ask=home), theme="sepia")
    assert 'data-theme="auto"' in document and home.asked[0]["theme"] == "auto"


def test_frame_is_kept_a_while_then_asked_again():
    home, clock = FakeHome(), Clock()
    frame = page.Frame(ask=home, clock=clock)
    render(frame)
    render(frame)
    assert len(home.asked) == 1
    render(frame, held={"data"})
    assert len(home.asked) == 2  # other roles, other sidebar
    clock.now = page.FRAME_S
    render(frame)
    assert len(home.asked) == 3


def test_home_away_keeps_the_last_head_and_a_way_back():
    home, clock = FakeHome(), Clock()
    frame = page.Frame(ask=home, clock=clock)
    render(frame)
    home.away = True
    clock.now = page.FRAME_S
    document = render(frame)
    assert '<link rel="stylesheet" href="/_design/6.0.0/app.css">' in document
    assert page.FALLBACK_SIDE in document and '<a class="way" href="/">' in document


def serve(answer: bytes):
    class Home(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            Home.asked.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *_):
            pass

    Home.asked = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Home)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Home.asked


def test_home_is_asked_over_http(monkeypatch):
    server, asked = serve(json.dumps({"head": "<link>", "side": "<aside>侧栏</aside>"}).encode())
    monkeypatch.setattr(page, "FRAME_URL", f"http://127.0.0.1:{server.server_address[1]}/frame")
    try:
        document = render(page.Frame())
    finally:
        server.shutdown()
    assert "<aside>侧栏</aside>" in document
    assert asked == ["/frame?user=ann&held=data%2Cwallet&theme=dark&current=%2Fdata%2F"]


def test_a_wrong_answer_is_home_away(monkeypatch):
    server, _ = serve(b'{"head": 1}')
    monkeypatch.setattr(page, "FRAME_URL", f"http://127.0.0.1:{server.server_address[1]}/frame")
    try:
        assert page.FALLBACK_SIDE in render(page.Frame())
    finally:
        server.shutdown()
