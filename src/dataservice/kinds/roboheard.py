"""Kind `roboheard`: a sealed RoboHEARD index, as `rh data publish` (roboheard-train) writes it.

The release holds

- `index.json` (version, cameras, code, and `listen`: the signals, the
  spectrogram's hop, tile and rows, and each session's AEC shift);
- `sessions` and `segments.parquet`, the index's own tables;
- `listen/<session>/<signal>.mp3` and `.jpg`: each signal encoded once, and
  its spectrogram as one sheet of tiles stacked top to bottom;
- `Audio/<session>.wav`, `Audio_AEC/<session>.wav` and
  `Video/V<nnn>-<camera>.mp4`, the originals, reached only by link.

The viewer is rh_preview's whole-session player: one session's video, a
chosen signal, its spectrogram, the speaker lanes and the transcript on one
clock. The tables go into one in-memory DuckDB per release; media is only
ever a link.
"""

from __future__ import annotations

import io
import json
import math
import re

import duckdb
import numpy as np
import pyarrow.parquet as pq

from dataservice.kinds import NotFound

TABLES = ("sessions", "segments")
SESSION = re.compile(r"S[0-9]{3,4}")
CAMERAS = (
    "head_leftImg",
    "head_rightImg",
    "head_stereo_leftImg",
    "head_stereo_rightImg",
    "left_wristImg",
    "right_wristImg",
)
# What the transcript and the lanes need of each segment.
LANE_COLS = ("seg_id", "kind", "speaker", "start", "end", "text")
# Who each speaker is, as the session header names them.
WHO_COLS = ("gender", "age", "origin")


def finite(value):
    """JSON-safe: NaN and infinities are null, numpy scalars plain numbers."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class View:
    def __init__(self, release):
        self.release = release
        self.index = json.loads(release.read("index.json"))
        self.listen = self.index.get("listen") or {}
        self.db = duckdb.connect(":memory:")
        for name in TABLES:
            table = pq.read_table(io.BytesIO(release.read(f"{name}.parquet")))
            self.db.register("incoming", table)
            self.db.execute(f"CREATE TABLE {name} AS SELECT * FROM incoming")
            self.db.unregister("incoming")
        self.columns = {
            name: [row[0] for row in self.db.execute(f"DESCRIBE {name}").fetchall()] for name in TABLES
        }

    def rows(self, sql: str, params: list | tuple = ()) -> list[dict]:
        cursor = self.db.cursor()
        try:
            result = cursor.execute(sql, params)
            names = [column[0] for column in result.description]
            return [
                {name: finite(value) for name, value in zip(names, row, strict=True)}
                for row in result.fetchall()
            ]
        finally:
            cursor.close()

    def answer(self, path: list[str], query: dict) -> dict:
        match path:
            case ["sessions"]:
                return self.sessions()
            case ["session", session]:
                return self.session(session)
        raise NotFound("/".join(path))

    def sessions(self) -> dict:
        return {
            "sessions": self.rows("SELECT * FROM sessions ORDER BY session"),
            "index": {k: v for k, v in self.index.items() if k != "listen"},
            "license": self.release.competition.license,
        }

    def session(self, session: str) -> dict:
        if not SESSION.fullmatch(session):
            raise NotFound(session)
        found = self.rows("SELECT * FROM sessions WHERE session = ?", [session])
        if not found:
            raise NotFound(session)
        have = self.columns["segments"]
        lanes = ", ".join(f'"{c}"' for c in LANE_COLS if c in have)
        who = "".join(f', any_value("{c}") AS "{c}"' for c in WHO_COLS if c in have)
        return {
            "session": found[0],
            "segments": self.rows(
                f"SELECT {lanes} FROM segments WHERE session = ? ORDER BY start", [session]
            ),
            "speakers": self.rows(
                f'SELECT speaker{who}, count(*) AS turns, sum("end" - start) AS speech FROM segments'
                " WHERE session = ? AND kind = 'human' GROUP BY speaker ORDER BY speaker",
                [session],
            ),
            "signals": self.signals(session),
            "video": self.video(session),
            "listen": {
                "hop_s": self.listen.get("hop_s"),
                "tile": self.listen.get("tile"),
                "rows": self.listen.get("rows"),
                "aec_shift_s": (self.listen.get("aec_shift_s") or {}).get(session, 0.0),
            },
        }

    def signals(self, session: str) -> dict:
        """Each signal whose MP3 and sheet this release holds, in the order index.json lists them."""
        out = {}
        for name in self.listen.get("signals", ()):
            mp3 = self.release.url(f"listen/{session}/{name}.mp3")
            jpg = self.release.url(f"listen/{session}/{name}.jpg")
            if mp3 and jpg:
                out[name] = {"mp3": mp3, "jpg": jpg}
        return out

    def video(self, session: str) -> dict:
        """Links to the session's cameras this release holds, relative to the API base."""
        out = {}
        for camera in CAMERAS:
            url = self.release.url(f"Video/V{session[1:]}-{camera}.mp4")
            if url:
                out[camera] = url
        return out
