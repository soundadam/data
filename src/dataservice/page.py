"""The console frame around the data pages: site bar, sidebar, theme.

These pages sit in my.soundadam.com's console next to the products
platform-gitops renders, so the frame is a copy of theirs (site_bar_html,
sidebar_html, PRODUCTS, the theme and identity cookies in
clusters/production/apps/*/portal/portal.py there). Keep it equal to those
when they change; DESIGN_VERSION follows the release my/home serves under
/_design/.
"""

from __future__ import annotations

import html
import re

BASE = "/data"
DESIGN_VERSION = "4.6.0"
DESIGN = f"/_design/{DESIGN_VERSION}"
# The console's products in sidebar order: (role, path, label, icon, sections).
PRODUCTS = [
    ("wallet", "/llm2/", "llm2 · 个人", "spark", ()),
    ("llm2", "/org/llm2/", "llm2 · 组织", "spark", ()),
    ("sub", "/sub/", "订阅", "globe", ()),
    ("speech", "/speech/", "语音", "wave", ()),
    ("tailnet", "/tailnet/", "tailnet", "grid", ()),
    ("data", "/data/", "数据集", "grid", ()),
    (
        "gcluster",
        "/gcluster/",
        "gcluster",
        "server",
        (
            ("/gcluster/", "显卡"),
            ("/gcluster/watch", "关注"),
            ("/gcluster/load", "CPU · IO"),
            ("/gcluster/topology", "网络"),
            ("/gcluster/history", "记录"),
            ("/gcluster/alerts", "告警"),
        ),
    ),
    (
        "org-admin",
        "/org/",
        "组织管理",
        "users",
        (("/org/", "成员"), ("/org/budget", "额度"), ("/org/usage", "用量"), ("/org/invites", "邀请")),
    ),
]
OVERVIEW = (("/", "概要"), ("/bill", "账单"))
LABEL = next(label for role, _, label, *_ in PRODUCTS if role == "data")

THEME_COOKIE = "theme"
THEMES = {"light": "浅色", "dark": "深色", "auto": "跟随系统"}
THEME_PICK = (
    '<nav class="segmented theme-pick" aria-label="主题">'
    + "".join(
        f'<button type="button" data-theme-pick="{value}">{label}</button>' for value, label in THEMES.items()
    )
    + "</nav>"
)

SITE = "https://soundadam.com/zh"
SITE_SECTIONS = (("/about/", "关于"), ("/projects/", "项目"), ("/blog/", "博客"))
CONSOLE = "https://my.soundadam.com/"
ACCOUNT_SETTINGS = "https://auth.soundadam.com/realms/soundadam/account/"
ADMIN_CONSOLE = "https://portal.admin.soundadam.com/"

HAT_COOKIE = "as"
OWNER_HAT = "*"
SWITCH_HAT = CONSOLE + "_as"
HAT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


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


def hat_of(header: str) -> str:
    """HAT_COOKIE of a Cookie header: an org's name, or empty when absent, sent twice, or malformed."""
    found = cookie(header, HAT_COOKIE) or ""
    return found if found == OWNER_HAT or HAT_NAME.fullmatch(found) else ""


def site_bar_html(design: str, account: str, pick: str = "", held=frozenset(), hat: str = "") -> str:
    """The top bar every console shares; `account`, already escaped, opens the account card."""
    items = "".join(
        f'<div class="mega-item"><p class="mega-item__label"><a href="{SITE}{path}">{label}</a></p></div>'
        for path, label in SITE_SECTIONS
    )
    hats = [("", "成员", SWITCH_HAT + "?next=/")]
    if "org-admin" in held:
        hats.append(("org", "组织管理", SWITCH_HAT + "?next=/org/"))
    if "admin" in held:
        hats.append((OWNER_HAT, "所有者", ADMIN_CONSOLE))
    worn = "" if not hat else OWNER_HAT if hat == OWNER_HAT else "org"
    mark = ' aria-current="true"'
    switch = (
        (
            '<p class="account-label">身份</p><nav class="segmented" aria-label="身份">'
            + "".join(
                f'<a href="{href}"{mark if key == worn else ""}>{label}</a>' for key, label, href in hats
            )
            + "</nav>"
        )
        if len(hats) > 1
        else ""
    )
    role = "所有者" if hat == OWNER_HAT else f"{html.escape(hat)} 管理员" if hat else ""
    card = (
        f'<details class="site-header-account"><summary><span>{account}</span></summary><div class="account-card">'
        f'<p class="account-who"><b>{account}</b>'
        + (f"<span>{role}</span>" if role else "")
        + "</p>"
        + switch
        + (f'<p class="account-label">主题</p>{pick}' if pick else "")
        + f'<p class="account-links"><a href="{ACCOUNT_SETTINGS}">账号设置</a><a href="/logout">退出</a></p></div></details>'
    )
    return (
        '<header class="site-bar"><div class="site-header"><div class="site-header-inner">'
        f'<div class="site-header-brand"><a class="site-logo" href="{SITE}/">'
        f'<img src="{design}/waveform-dark.svg" alt="soundadam" width="183" height="40"></a></div>'
        f'<nav class="site-header-menu" aria-label="站点"><div class="site-header-items">{items}</div>'
        f'<div class="site-header-cta"><a href="{CONSOLE}">Console</a></div></nav>'
        + (card if account else "")
        + "</div></div></header>"
    )


def sidebar_html(held: set[str], current: str, hat: str = "") -> str:
    """The console sidebar: OVERVIEW, then each held product that has sections."""

    def icon(name: str) -> str:
        return f'<i class="nav-icon nav-{name}" aria-hidden="true"></i>'

    def link(href: str, label: str, glyph: str = "") -> str:
        mark = ' aria-current="page"' if href == current else ""
        return f'<a href="{href}"{mark}>{glyph}{html.escape(label)}</a>'

    mine = [(href, name) for href, name in OVERVIEW if href == "/" or "wallet" in held]
    here = next((name for href, name in mine if href == current), "概要")
    groups = (
        f'<div class="side-group"><span class="side-title">{icon("grid")}我的</span>'
        + "".join(link(href, name) for href, name in mine)
        + "</div>"
    )
    for role, _, label, glyph, sections in PRODUCTS:
        if role not in held or not sections or role == "org-admin" and not hat:
            continue
        here = next((f"{label} · {name}" for href, name in sections if href == current), here)
        groups += (
            f'<div class="side-group"><span class="side-title">{icon(glyph)}{html.escape(label)}</span>'
            + "".join(link(href, name) for href, name in sections)
            + "</div>"
        )
    return (
        '<nav class="console-sidebar" aria-label="导航">'
        '<input type="checkbox" id="side-toggle" class="side-toggle">'
        f'<label for="side-toggle" class="side-bar">{icon("menu")}<span>{html.escape(here)}</span>{icon("chevron")}</label>'
        f'<div class="side-list">{groups}</div>'
        "</nav>"
    )


def crumbs(username: str, here: str = "") -> str:
    """概要 › 数据集 (the member) › a competition."""
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
    hat: str,
    title: str,
    heading: str,
    trail: str,
    body: str,
    scripts: tuple[str, ...] = (),
    styles: tuple[str, ...] = (),
    data: dict | None = None,
) -> str:
    """A whole console page. `heading` and `body` are trusted HTML; scripts and styles are under /static/."""
    attrs = "".join(f' data-{name}="{html.escape(value)}"' for name, value in (data or {}).items())
    links = "".join(f'<link rel="stylesheet" href="{BASE}/static/{name}">' for name in styles)
    tags = "".join(f'<script src="{BASE}/static/{name}" defer></script>' for name in ("console.js", *scripts))
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="{theme if theme in THEMES else "auto"}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="same-origin">
<meta name="color-scheme" content="light dark">
<title>{html.escape(title)} · soundadam</title>
<link rel="icon" href="{DESIGN}/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{DESIGN}/fonts.css">
<link rel="stylesheet" href="{DESIGN}/tokens.css">
<link rel="stylesheet" href="{DESIGN}/header.css">
<link rel="stylesheet" href="{DESIGN}/console.css">
<link rel="stylesheet" href="{BASE}/static/data.css">{links}
{tags}
</head><body>
{site_bar_html(DESIGN, html.escape(claims_name), THEME_PICK, held, hat)}<div class="console-shell">{sidebar_html(held, "/", hat)}
<main class="console"{attrs}>
{trail}<h1>{heading}</h1>
{body}
</main>
</div>
</body></html>
"""
