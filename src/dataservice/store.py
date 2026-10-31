"""The competitions' releases, read through S3 from their origin, and the links that stream them.

The origin is `rclone serve s3` on g18, beside the originals, reached on
127.0.0.1 through the pod's SSH tunnel with the origin's read-only key
(Secret `data/origin`). Nothing is copied off g18: a release is a directory
of tables plus symlinks to the originals (platform-gitops
docs/adr/datasets-own-service-per-competition-owner-grants.md).

A release is sealed: the publisher writes `_SUCCESS` last, and a prefix
without it is not published. The loaded release is kept until `_SUCCESS`
changes, checked at most every RECHECK_S.

Audio, video and downloads are streamed by this service. A link is the
file's path plus an expiry and an HMAC under a key drawn at start, so it
needs neither cookie nor personal key and dies with the process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from dataservice.catalog import Competition

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:18333")
# The ADR's ceiling is 15 minutes; a page asks again when it needs a fresh one.
LINK_S = 600
RECHECK_S = 300
SEALED = "_SUCCESS"
CHUNK = 256 * 1024


def client(endpoint: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class NotPublished(Exception):
    """The competition's prefix holds no sealed release (yet)."""


class BadRange(Exception):
    """The asked range lies outside the file."""


class Stream:
    """One GET's answer: status, the headers to pass on, and the body in chunks."""

    def __init__(self, status: int, headers: dict[str, str], body: Iterator[bytes]):
        self.status = status
        self.headers = headers
        self.body = body


class Store:
    def __init__(self, endpoint: str = ENDPOINT) -> None:
        self.s3 = client(endpoint)

    def get(self, bucket: str, key: str) -> bytes:
        return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def stream(self, bucket: str, key: str, byte_range: str | None) -> Stream:
        """`key`, or the one range `bytes=a-b` of it, to be copied out in chunks."""
        try:
            found = self.s3.get_object(
                Bucket=bucket, Key=key, **({"Range": byte_range} if byte_range else {})
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "InvalidRange":
                raise BadRange(key) from error
            raise
        headers = {"Content-Length": str(found["ContentLength"]), "Accept-Ranges": "bytes"}
        if found.get("ContentRange"):
            headers["Content-Range"] = found["ContentRange"]
        return Stream(206 if "Content-Range" in headers else 200, headers, found["Body"].iter_chunks(CHUNK))

    def seal(self, bucket: str, key: str) -> str | None:
        """What identifies this version of `key`, or None when it is absent."""
        try:
            found = self.s3.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NoSuchBucket",
                "NotFound",
            ):
                return None
            raise
        # The origin serves no ETag (hashing the originals would read them all).
        return f"{found.get('ETag', '')}|{found.get('LastModified', '')}|{found.get('ContentLength', '')}"

    def listing(self, bucket: str, prefix: str) -> dict[str, int]:
        """Key below `prefix` → size, for every object under it."""
        found: dict[str, int] = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", ()):
                found[item["Key"].removeprefix(prefix)] = item["Size"]
        return found


class Links:
    """Expiring links to a release's files, signed with a key that lives only in this process."""

    def __init__(self, key: bytes | None = None, clock: Callable[[], float] = time.time):
        self.key = key or secrets.token_bytes(32)
        self.clock = clock

    def mac(self, competition_id: str, name: str, expires: int) -> str:
        message = f"{competition_id}\n{name}\n{expires}".encode()
        digest = hmac.new(self.key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def link(self, competition_id: str, name: str, seconds: int = LINK_S) -> str:
        """`raw/<name>?expires=…&sig=…`, relative to the competition's API base."""
        expires = int(self.clock()) + seconds
        query = urllib.parse.urlencode({"expires": expires, "sig": self.mac(competition_id, name, expires)})
        return f"raw/{urllib.parse.quote(name)}?{query}"

    def valid(self, competition_id: str, name: str, query: dict[str, list[str]]) -> bool:
        try:
            expires = int(query.get("expires", [""])[0])
        except ValueError:
            return False
        if expires < self.clock():
            return False
        return hmac.compare_digest(self.mac(competition_id, name, expires), query.get("sig", [""])[0])


class Release:
    """One competition's sealed files: their listing, plus what its kind loaded from them."""

    def __init__(
        self,
        store: Store,
        links: Links,
        competition: Competition,
        seal: str,
        files: dict[str, int],
        view: Any,
    ):
        self.store = store
        self.links = links
        self.competition = competition
        self.seal = seal
        self.files = files
        self.view = view
        self.checked = time.monotonic()

    def read(self, name: str) -> bytes:
        return self.store.get(self.competition.bucket, self.competition.prefix + name)

    def url(self, name: str) -> str | None:
        """A link to `name`, a file of this release, or None when it holds no such file."""
        if name not in self.files:
            return None
        return self.links.link(self.competition.id, name)

    def stream(self, name: str, byte_range: str | None) -> Stream:
        return self.store.stream(self.competition.bucket, self.competition.prefix + name, byte_range)


class Releases:
    """The loaded release of each competition, reloaded when its seal changes."""

    def __init__(self, store: Store, views: dict[str, Callable[[Release], Any]], links: Links | None = None):
        self.store = store
        self.views = views
        self.links = links or Links()
        self.loaded: dict[str, Release] = {}
        self.lock = threading.Lock()

    def get(self, competition: Competition) -> Release:
        with self.lock:
            current = self.loaded.get(competition.id)
            if current is not None and time.monotonic() - current.checked < RECHECK_S:
                return current
            seal = self.store.seal(competition.bucket, competition.prefix + SEALED)
            if seal is None:
                self.loaded.pop(competition.id, None)
                raise NotPublished(competition.id)
            if current is not None and current.seal == seal:
                current.checked = time.monotonic()
                return current
            files = self.store.listing(competition.bucket, competition.prefix)
            release = Release(self.store, self.links, competition, seal, files, None)
            release.view = self.views[competition.kind](release)
            self.loaded[competition.id] = release
            return release
