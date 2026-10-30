"""SeaweedFS through its S3 API, and each competition's published release.

Reads go to the in-cluster endpoint with the read-only key (Secret
`data/s3-read`, confined to the competitions' buckets). Presigned URLs are
signed for the public endpoint (`s3.api`): the signature covers the host, so
a URL signed for the ClusterIP would not verify from a browser. Signing is
local; nothing is sent to SeaweedFS to make one.

A release is sealed: the publisher writes `_SUCCESS` last, and a prefix
without it is not published. The loaded release is kept until `_SUCCESS`
changes, checked at most every RECHECK_S.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from dataservice.catalog import Competition

ENDPOINT = os.environ.get("S3_ENDPOINT", "http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333")
PUBLIC_ENDPOINT = os.environ.get("S3_PUBLIC_ENDPOINT", "https://s3.api.soundadam.com")
# The ADR's ceiling is 15 minutes; a page asks again when it needs a fresh one.
PRESIGN_S = 600
RECHECK_S = 300
SEALED = "_SUCCESS"


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


class Store:
    def __init__(self, endpoint: str = ENDPOINT, public_endpoint: str = PUBLIC_ENDPOINT) -> None:
        self.s3 = client(endpoint)
        self.public = client(public_endpoint)

    def get(self, bucket: str, key: str) -> bytes:
        return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def etag(self, bucket: str, key: str) -> str | None:
        try:
            return self.s3.head_object(Bucket=bucket, Key=key)["ETag"]
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NoSuchBucket",
                "NotFound",
            ):
                return None
            raise

    def listing(self, bucket: str, prefix: str) -> dict[str, int]:
        """Key below `prefix` → size, for every object under it."""
        found: dict[str, int] = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", ()):
                found[item["Key"].removeprefix(prefix)] = item["Size"]
        return found

    def presign(self, bucket: str, key: str, seconds: int = PRESIGN_S) -> str:
        return self.public.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=seconds
        )


class Release:
    """One competition's sealed files: their listing, plus what its kind loaded from them."""

    def __init__(self, store: Store, competition: Competition, seal: str, files: dict[str, int], view: Any):
        self.store = store
        self.competition = competition
        self.seal = seal
        self.files = files
        self.view = view
        self.checked = time.monotonic()

    def read(self, name: str) -> bytes:
        return self.store.get(self.competition.bucket, self.competition.prefix + name)

    def url(self, name: str) -> str | None:
        """A presigned GET for `name`, a file of this release, or None when it holds no such file."""
        if name not in self.files:
            return None
        return self.store.presign(self.competition.bucket, self.competition.prefix + name)


class Releases:
    """The loaded release of each competition, reloaded when its seal changes."""

    def __init__(self, store: Store, views: dict[str, Callable[[Release], Any]]):
        self.store = store
        self.views = views
        self.loaded: dict[str, Release] = {}
        self.lock = threading.Lock()

    def get(self, competition: Competition) -> Release:
        with self.lock:
            current = self.loaded.get(competition.id)
            if current is not None and time.monotonic() - current.checked < RECHECK_S:
                return current
            seal = self.store.etag(competition.bucket, competition.prefix + SEALED)
            if seal is None:
                self.loaded.pop(competition.id, None)
                raise NotPublished(competition.id)
            if current is not None and current.seal == seal:
                current.checked = time.monotonic()
                return current
            files = self.store.listing(competition.bucket, competition.prefix)
            release = Release(self.store, competition, seal, files, None)
            release.view = self.views[competition.kind](release)
            self.loaded[competition.id] = release
            return release
