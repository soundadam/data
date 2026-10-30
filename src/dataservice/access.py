"""Who is asking, and which competitions they may see.

People come through SecurityPolicy `my`: Envoy signs the browser in and
verifies the AccessToken cookie before a request reaches the web port, so
its claims are trusted here without a second signature check. That policy
admits every member product's role; the web port checks `data` itself.

Scripts come to data.api with a personal key, which carries no claims: the
key stands for the GitHub id it was issued to, and its hash is all this
service keeps (keys.sqlite3 on the PVC).

Either way the competitions come from members (`GET /data/<github id>`),
cached for at most GRANTS_S, so a grant or a revocation lands within that.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
from datetime import UTC, datetime

MEMBERS_URL = os.environ.get("MEMBERS_URL", "http://members.members.svc.cluster.local:8081")
ROLE = "data"
GRANTS_S = 60
GITHUB_ID = re.compile(r"[1-9][0-9]{0,19}")
# A key is PREFIX + 43 url-safe characters (32 random bytes).
KEY_PREFIX = "sdd_"
KEY = re.compile(r"sdd_[A-Za-z0-9_-]{43}")
KEY_ID = re.compile(r"[0-9a-f]{12}")
MAX_KEYS = 10
LABEL = re.compile(r"[^\x00-\x1f\x7f]{1,40}")


class Unauthenticated(Exception):
    """No credential this service can read."""


class Unavailable(Exception):
    """members did not answer."""


def access_token_claims(cookie_header: str) -> dict:
    """Claims of the one AccessToken cookie Envoy verified.

    Two AccessToken cookies are refused: Envoy verifies one of them, and
    reading the other would trust claims nobody checked.
    """
    tokens = []
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == "AccessToken":
            tokens.append(value)
    if len(tokens) != 1:
        raise Unauthenticated(f"{len(tokens)} AccessToken cookies")
    segments = tokens[0].split(".")
    if len(segments) != 3:
        raise Unauthenticated("AccessToken is not a JWT")
    payload = segments[1] + "=" * (-len(segments[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError) as error:
        raise Unauthenticated("AccessToken payload is unreadable") from error
    if not isinstance(claims, dict):
        raise Unauthenticated("AccessToken payload is not an object")
    return claims


def roles(claims: dict) -> set[str]:
    value = claims.get("gateway_roles")
    return {role for role in value if isinstance(role, str)} if isinstance(value, list) else set()


def github_id(claims: dict) -> str:
    value = claims.get("github_user_id")
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not GITHUB_ID.fullmatch(value):
        raise Unauthenticated("token has no github_user_id")
    return value


def members_grants(github: str) -> list[str]:
    url = f"{MEMBERS_URL}/data/{github}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = json.loads(response.read())
    except (OSError, ValueError) as error:
        raise Unavailable(type(error).__name__) from error
    found = body.get("competitions") if isinstance(body, dict) else None
    if not isinstance(found, list):
        raise Unavailable("no competitions list")
    return [item for item in found if isinstance(item, str)]


class Grants:
    """github id → the competitions members grants them, kept at most GRANTS_S."""

    def __init__(self, ask=members_grants, now=time.monotonic):
        self.ask = ask
        self.now = now
        self.cache: dict[str, tuple[float, frozenset[str]]] = {}
        self.lock = threading.Lock()

    def of(self, github: str) -> frozenset[str]:
        with self.lock:
            hit = self.cache.get(github)
            if hit is not None and self.now() - hit[0] < GRANTS_S:
                return hit[1]
        found = frozenset(self.ask(github))
        with self.lock:
            self.cache[github] = (self.now(), found)
        return found


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Keys:
    """Personal keys for data.api: hashed, one GitHub id each."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS keys ("
            " id TEXT PRIMARY KEY, github_id TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,"
            " label TEXT NOT NULL, created TEXT NOT NULL, used TEXT)"
        )

    def issue(self, github: str, label: str) -> tuple[str, str]:
        """(key id, the key): the key is shown once and never stored."""
        if not LABEL.fullmatch(label):
            raise ValueError("名称要 1 到 40 个字。")
        with self.lock:
            (count,) = self.db.execute("SELECT count(*) FROM keys WHERE github_id = ?", (github,)).fetchone()
            if count >= MAX_KEYS:
                raise ValueError(f"每人最多 {MAX_KEYS} 把密钥，先作废不用的。")
            key = KEY_PREFIX + secrets.token_urlsafe(32)
            key_id = secrets.token_hex(6)
            self.db.execute(
                "INSERT INTO keys (id, github_id, hash, label, created) VALUES (?, ?, ?, ?, ?)",
                (key_id, github, digest(key), label, stamp()),
            )
        return key_id, key

    def revoke(self, github: str, key_id: str) -> bool:
        if not KEY_ID.fullmatch(key_id):
            return False
        with self.lock:
            return (
                self.db.execute("DELETE FROM keys WHERE id = ? AND github_id = ?", (key_id, github)).rowcount
                == 1
            )

    def of(self, github: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, label, created, used FROM keys WHERE github_id = ? ORDER BY created", (github,)
            ).fetchall()
        return [
            {"id": i, "label": label, "created": created, "used": used} for i, label, created, used in rows
        ]

    def owner(self, authorization: str) -> str:
        """The GitHub id a `Bearer <key>` header stands for."""
        scheme, _, key = authorization.partition(" ")
        if scheme.lower() != "bearer" or not KEY.fullmatch(key.strip()):
            raise Unauthenticated("no personal key")
        with self.lock:
            row = self.db.execute(
                "SELECT id, github_id FROM keys WHERE hash = ?", (digest(key.strip()),)
            ).fetchone()
            if row is None:
                raise Unauthenticated("unknown key")
            self.db.execute("UPDATE keys SET used = ? WHERE id = ?", (stamp(), row[0]))
        return row[1]


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
