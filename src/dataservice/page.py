"""The console frame around the data pages, taken from my.soundadam.com's home.

These pages sit in my.soundadam.com's console next to the products
platform-gitops renders. The frame is not copied here: home answers
`GET /frame` on its internal port (FRAME_URL) with the head's links and the
sidebar for the member's name, roles, theme and page, so these pages follow
every change to the console without a release of this service
(platform-gitops docs/adr/member-console-items-one-wallet-bill.md). An answer
is kept FRAME_S per member and page; while home cannot be reached a page
keeps the last head it got and a sidebar of one link back to the overview.
"""

from __future__ import annotations

import html
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

BASE = "/data"
LABEL = "数据集"
FRAME_URL = os.environ.get("FRAME_URL", "http://home.my.svc.cluster.local:8081/frame")
FRAME_S = 30
FRAME_TIMEOUT_S = 2

THEME_COOKIE = "theme"
THEMES = ("light", "dark", "auto")
# The sidebar while home cannot be reached: one way back to the overview.
FALLBACK_SIDE = (
    '<aside class="side" aria-label="控制台"><div class="top"><nav class="ways" aria-label="我的">'
    '<a class="way" href="/"><span>概要</span></a></nav></div></aside>'
)


def cookie(header: str, name: str) -> str | None:
    found = [
        value.strip()
        for part in header.split(";")
        for key, _, value in [part.partition("=")]
        if key.strip() == name
    ]
    return found[0] if len(found) == 1 else None


def theme_of(header: str) -> str:
    """THEME_COOKIE of a Cookie header, the system's when absent, sent twice, or unknown."""
    found = cookie(header, THEME_COOKIE)
    return found if found in THEMES else "auto"


def ask_home(query: dict[str, str]) -> dict:
    """home's answer for one page: {head, side}."""
    url = f"{FRAME_URL}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=FRAME_TIMEOUT_S) as reply:  # noqa: S310 - FRAME_URL is ours
        found = json.loads(reply.read())
    if not isinstance(found, dict) or not all(isinstance(found.get(key), str) for key in ("head", "side")):
        raise ValueError("frame is not {head, side}")
    return found


class Frame:
    """home's frame per (user, roles, theme, page), kept FRAME_S."""

    def __init__(
        self, ask: Callable[[dict[str, str]], dict] = ask_home, clock: Callable[[], float] = time.monotonic
    ):
        self.ask = ask
        self.clock = clock
        self.kept: dict[tuple[str, ...], tuple[float, dict]] = {}
        self.head = ""
        self.lock = threading.Lock()

    def get(self, user: str, held: set[str], theme: str, current: str) -> tuple[str, str]:
        """(head, side) for one page."""
        query = {"user": user, "held": ",".join(sorted(held)), "theme": theme, "current": current}
        key = tuple(query.values())
        with self.lock:
            found = self.kept.get(key)
        if found and self.clock() - found[0] < FRAME_S:
            return found[1]["head"], found[1]["side"]
        try:
            answer = self.ask(query)
        except Exception as error:  # noqa: BLE001 - home away or answering wrong: the page still opens
            print(f"frame unavailable: {type(error).__name__}", flush=True)
            return self.head, FALLBACK_SIDE
        with self.lock:
            if len(self.kept) >= 512:
                self.kept.clear()
            self.kept[key] = (self.clock(), answer)
            self.head = answer["head"]
        return answer["head"], answer["side"]


FRAME = Frame()


def crumbs(here: str = "") -> str:
    """概要 › 数据集 › a competition."""
    trail = '<nav class="crumbs" aria-label="位置"><a href="/">概要</a>'
    if not here:
        return trail + f'<span aria-current="page">{html.escape(LABEL)}</span></nav>'
    return (
        trail
        + f'<a href="{BASE}/">{html.escape(LABEL)}</a><span aria-current="page">{html.escape(here)}</span></nav>'
    )


# The item's tabs, (path under BASE, label).
TABS = (("/", "比赛"), ("/keys", "密钥"))
CURRENT = ' aria-current="page"'


def tabs(path: str) -> str:
    return (
        '<nav class="segmented" aria-label="数据集">'
        + "".join(
            f'<a href="{BASE}{href}"{CURRENT if href == path else ""}>{label}</a>' for href, label in TABS
        )
        + "</nav>"
    )


def page(
    *,
    claims_name: str,
    held: set[str],
    theme: str,
    title: str,
    heading: str,
    trail: str,
    body: str,
    below: str = "",
    scripts: tuple[str, ...] = (),
    styles: tuple[str, ...] = (),
    data: dict | None = None,
    wide: bool = False,
    frame: Frame | None = None,
) -> str:
    """A whole console page. `heading`, `below` (the tabs) and `body` are trusted HTML; scripts
    and styles are under /static/. The sidebar lights this item's row on every page of it."""
    theme = theme if theme in THEMES else "auto"
    head, side = (frame or FRAME).get(claims_name, held, theme, BASE + "/")
    attrs = "".join(f' data-{name}="{html.escape(value)}"' for name, value in (data or {}).items())
    links = "".join(f'<link rel="stylesheet" href="{BASE}/static/{name}">' for name in ("data.css", *styles))
    tags = "".join(f'<script src="{BASE}/static/{name}" defer></script>' for name in scripts)
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="{theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="same-origin">
<meta name="color-scheme" content="light dark">
<title>{html.escape(title)} · soundadam</title>
{head}{links}
{tags}
</head><body class="shell">{side}
<main{attrs}><div class="page{" wide" if wide else ""}">
<header class="head">{trail}<h1>{heading}</h1>{below}</header>
{body}
</div></main>
</body></html>
"""
