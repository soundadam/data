"""Kind `corpus`: a sample bank of ASR corpora, as `rh-corpus release` (roboheard-train) writes it.

The release holds

- `corpora.json`: the bank's version, the kinds of recording (远场会议, 车载, …)
  and every corpus on hand with its kind, size, channels, labels, licence and
  status (`sampled`, `excluded` when sampled but not used, `listed` when it has
  no clips);
- `clips.parquet`: one row per clip, its group within the corpus (e.g. `solo`
  and `overlap`), text, speaker, seconds, and where it was cut from;
- `clips/<corpus>/<clip_id>/<signal>.opus`: Ogg Opus, mono, 16 kHz, one file per
  signal (`far`, `near` or `mono`).

The page decodes the clips itself; nothing is encoded here.
"""

from __future__ import annotations

import io
import json
import re

import duckdb
import pyarrow.parquet as pq

from dataservice.kinds import NotFound
from dataservice.kinds.roboheard import finite

CORPUS = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
# Shown with each clip; the rest of a row (e.g. ov, dnsmos, votes) goes in `extra`.
CLIP_COLS = ("clip_id", "group", "text", "speaker", "start", "end", "dur", "src", "flags")
HIDDEN = {"corpus", "signals", "src_root", "error"}


class View:
    def __init__(self, release):
        self.release = release
        self.bank = json.loads(release.read("corpora.json"))
        self.db = duckdb.connect(":memory:")
        table = pq.read_table(io.BytesIO(release.read("clips.parquet")))
        self.db.register("incoming", table)
        self.db.execute("CREATE TABLE clips AS SELECT * FROM incoming")
        self.db.unregister("incoming")
        self.columns = [row[0] for row in self.db.execute("DESCRIBE clips").fetchall()]

    def answer(self, path: list[str], query: dict) -> dict:
        match path:
            case ["corpora"]:
                return self.corpora()
            case ["corpus", corpus]:
                return self.corpus(corpus)
        raise NotFound("/".join(path))

    def corpora(self) -> dict:
        return {
            **{k: v for k, v in self.bank.items() if k != "corpora"},
            "corpora": self.bank["corpora"],
            "license": self.release.competition.license,
        }

    def corpus(self, corpus: str) -> dict:
        entry = next((c for c in self.bank["corpora"] if c["id"] == corpus), None)
        if not CORPUS.fullmatch(corpus) or entry is None:
            raise NotFound(corpus)
        cursor = self.db.cursor()
        try:
            result = cursor.execute("SELECT * FROM clips WHERE corpus = ? ORDER BY clip_id", [corpus])
            names = [column[0] for column in result.description]
            rows = [dict(zip(names, row, strict=True)) for row in result.fetchall()]
        finally:
            cursor.close()
        clips = []
        for row in rows:
            clip = {c: finite(row.get(c)) for c in CLIP_COLS if c in row}
            extra = {k: finite(v) for k, v in row.items() if k not in CLIP_COLS and k not in HIDDEN}
            clip["extra"] = {k: v for k, v in extra.items() if v not in (None, "")}
            clip["signals"] = {}
            for name in (row.get("signals") or "").split():
                url = self.release.url(f"clips/{corpus}/{row['clip_id']}/{name}.opus")
                if url:
                    clip["signals"][name] = url
            clips.append(clip)
        return {"corpus": entry, "clips": clips}
