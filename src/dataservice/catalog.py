"""The competitions this service holds: catalog.json, plus those published at run time.

Each one is an id, a title, a kind and the release it reads: one bucket and
a prefix in SeaweedFS. The kind is the contract between the repository that
publishes the data and the viewer here (kinds/): which files the release
holds and how they are looked at. members reads id, title and kind from the
internal port; bucket and prefix stay here.

catalog.json is in the image. Beside it, the owner publishes a dataset
without a release of this service by writing `catalog/<id>.json` ({title,
kind, prefix, license}) into bucket RUNTIME_BUCKET, which is also the bucket
it reads; Catalog lists them every CATALOG_S. An entry the checks refuse, or
whose id catalog.json already has, is left out and logged.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from importlib import resources

# The same shapes members accepts from GET /catalog.
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
PREFIX = re.compile(r"(?:[A-Za-z0-9._-]+/)+")
RUNTIME_BUCKET = "datasets"
RUNTIME = "catalog/"
CATALOG_S = 60


@dataclass(frozen=True)
class Competition:
    id: str
    title: str
    kind: str
    bucket: str
    prefix: str
    license: str = ""

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind}


def check(entry: Competition, kinds: frozenset[str] | None) -> None:
    """Refuse an entry members or S3 would not take."""
    if not ID.fullmatch(entry.id):
        raise ValueError(f"competition id {entry.id!r}")
    if not 1 <= len(entry.title) <= 100 or not 1 <= len(entry.kind) <= 40:
        raise ValueError(f"{entry.id}: title or kind length")
    if kinds is not None and entry.kind not in kinds:
        raise ValueError(f"{entry.id}: no viewer for kind {entry.kind!r}")
    if (
        not ID.fullmatch(entry.bucket)
        or not PREFIX.fullmatch(entry.prefix)
        or {".", ".."} & set(entry.prefix.split("/"))
    ):
        raise ValueError(f"{entry.id}: bucket or prefix")


def load(text: str | None = None, kinds: frozenset[str] | None = None) -> dict[str, Competition]:
    """id → Competition from catalog.json, refusing an entry members or S3 would not take."""
    if text is None:
        text = resources.files(__package__).joinpath("catalog.json").read_text("utf-8")
    found: dict[str, Competition] = {}
    for item in json.loads(text)["competitions"]:
        entry = Competition(**item)
        if entry.id in found:
            raise ValueError(f"competition id {entry.id!r}")
        check(entry, kinds)
        found[entry.id] = entry
    return found


def runtime_entry(name: str, body: bytes, kinds: frozenset[str] | None) -> Competition:
    """`catalog/<id>.json` as a Competition reading RUNTIME_BUCKET."""
    item = json.loads(body)
    if not isinstance(item, dict) or not set(item) <= {"title", "kind", "prefix", "license"}:
        raise ValueError(f"{name}: not {{title, kind, prefix, license}}")
    if not all(isinstance(value, str) for value in item.values()):
        raise ValueError(f"{name}: values are not strings")
    entry = Competition(id=name.removesuffix(".json"), bucket=RUNTIME_BUCKET, **item)
    check(entry, kinds)
    if entry.prefix.startswith(RUNTIME):
        raise ValueError(f"{entry.id}: prefix inside {RUNTIME}")
    return entry


class Catalog:
    """catalog.json, and the runtime entries as of the last refresh."""

    def __init__(self, built_in: dict[str, Competition], store=None, kinds: frozenset[str] | None = None):
        self.built_in = built_in
        self.store = store
        self.kinds = kinds
        self.found = dict(built_in)
        self.refused: set[str] = set()
        self.lock = threading.Lock()

    def current(self) -> dict[str, Competition]:
        with self.lock:
            return self.found

    def refresh(self) -> None:
        """List RUNTIME again; while the store cannot be read, keep what the last listing found."""
        if self.store is None:
            return
        found, refused = dict(self.built_in), set()
        try:
            names = sorted(self.store.listing(RUNTIME_BUCKET, RUNTIME))
            for name in names:
                if "/" in name or not name.endswith(".json"):
                    continue
                try:
                    entry = runtime_entry(name, self.store.get(RUNTIME_BUCKET, RUNTIME + name), self.kinds)
                except (ValueError, TypeError) as error:
                    refused.add(f"{name}: {error}")
                    continue
                if entry.id in self.built_in:
                    refused.add(f"{name}: catalog.json has {entry.id}")
                    continue
                found[entry.id] = entry
        except Exception as error:  # noqa: BLE001 - the store; keep the last listing
            sys.stderr.write(f"catalog unreadable: {type(error).__name__}\n")
            return
        for line in sorted(refused - self.refused):
            sys.stderr.write(f"catalog refused {line}\n")
        with self.lock:
            self.found, self.refused = found, refused

    def run(self) -> None:
        while True:
            time.sleep(CATALOG_S)
            self.refresh()
