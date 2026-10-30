"""The competitions this service holds (catalog.json).

Each one is an id, a title, a kind and the release it reads: one bucket and
a prefix in SeaweedFS. The kind is the contract between the repository that
publishes the data and the viewer here (kinds/): which files the release
holds and how they are looked at. members reads id, title and kind from the
internal port; bucket and prefix stay here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources

# The same shapes members accepts from GET /catalog.
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
PREFIX = re.compile(r"(?:[A-Za-z0-9._-]+/)+")


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


def load(text: str | None = None, kinds: frozenset[str] | None = None) -> dict[str, Competition]:
    """id → Competition, refusing an entry members or S3 would not take."""
    if text is None:
        text = resources.files(__package__).joinpath("catalog.json").read_text("utf-8")
    found: dict[str, Competition] = {}
    for item in json.loads(text)["competitions"]:
        entry = Competition(**item)
        if not ID.fullmatch(entry.id) or entry.id in found:
            raise ValueError(f"competition id {entry.id!r}")
        if not 1 <= len(entry.title) <= 100 or not 1 <= len(entry.kind) <= 40:
            raise ValueError(f"{entry.id}: title or kind length")
        if kinds is not None and entry.kind not in kinds:
            raise ValueError(f"{entry.id}: no viewer for kind {entry.kind!r}")
        if not ID.fullmatch(entry.bucket) or not PREFIX.fullmatch(entry.prefix):
            raise ValueError(f"{entry.id}: bucket or prefix")
        found[entry.id] = entry
    return found
