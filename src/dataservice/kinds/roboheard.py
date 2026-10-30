"""Kind `roboheard`: a sealed RoboHEARD index, as `rh data publish` (roboheard-train) writes it.

The release holds

- `index.json` (version, frame hop, cameras, code) and `stats.json`, the
  aggregates the index computed once;
- `sessions`, `segments`, `speakers.parquet`, the index's own tables;
- `mining.parquet` (query, seg_id, rank) and `queries.json`, every hit of
  every mining query in the order the listen page draws them;
- `frames/<session>.parquet`, frame levels in dB (mic_db, ref_db, aec_db);
- `Audio/<session>.wav`, `Audio_AEC/<session>.wav` and
  `Video/V<nnn>-<camera>.mp4`, the originals, reached only by presigned URL.

Everything here is metadata. The tables go into one in-memory DuckDB per
release; frames are read per session when a timeline asks for them.
"""

from __future__ import annotations

import io
import json
import math
import re
import threading
import warnings
from collections import OrderedDict

import duckdb
import numpy as np
import pyarrow.parquet as pq

from dataservice.kinds import BadRequest, NotFound

TABLES = ("sessions", "segments", "speakers", "mining")
SESSION = re.compile(r"S[0-9]{3,4}")
SEG_ID = re.compile(r"S[0-9]{3,4}-(?:P[0-9]{1,4}|R)-[0-9]{4}")
QUERY = re.compile(r"[a-z0-9_]{1,40}")
CAMERAS = (
    "head_leftImg",
    "head_rightImg",
    "head_stereo_leftImg",
    "head_stereo_rightImg",
    "left_wristImg",
    "right_wristImg",
)
# The columns a list of segments shows (rh_preview's SEG_COLS, plus where it is).
SEG_COLS = (
    "seg_id",
    "session",
    "speaker",
    "kind",
    "start",
    "dur",
    "n_ov_spk",
    "ov_human",
    "ov_robot",
    "snr_db",
    "ref_db",
    "flags",
    "text",
)
# What a session's timeline needs of each segment.
LANE_COLS = ("seg_id", "tier", "kind", "speaker", "start", "end", "text", "flags")
MAX_HITS = 500
MAX_POINTS = 2000
MAX_PAD_S = 30.0
FRAME_CACHE = 16


def finite(value):
    """JSON-safe: NaN and infinities are null, numpy scalars plain numbers."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def number(query: dict, name: str, default: float, low: float, high: float) -> float:
    raw = query.get(name, [None])[0]
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise BadRequest(name) from error
    if not math.isfinite(value) or not low <= value <= high:
        raise BadRequest(name)
    return value


class View:
    def __init__(self, release):
        self.release = release
        self.index = json.loads(release.read("index.json"))
        self.stats = json.loads(release.read("stats.json"))
        self.queries = json.loads(release.read("queries.json"))
        self.db = duckdb.connect(":memory:")
        for name in TABLES:
            table = pq.read_table(io.BytesIO(release.read(f"{name}.parquet")))
            self.db.register("incoming", table)
            self.db.execute(f"CREATE TABLE {name} AS SELECT * FROM incoming")
            self.db.unregister("incoming")
        self.columns = {
            name: [row[0] for row in self.db.execute(f"DESCRIBE {name}").fetchall()] for name in TABLES
        }
        self.frames: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()

    # --- queries ---------------------------------------------------------

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

    def cols(self, table: str, wanted: tuple[str, ...], alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        return ", ".join(f'{prefix}"{c}"' for c in wanted if c in self.columns[table])

    # --- answers ---------------------------------------------------------

    def answer(self, path: list[str], query: dict) -> dict:
        match path:
            case ["overview"]:
                return self.overview()
            case ["query", name]:
                return self.hits(name, query)
            case ["session", session]:
                return self.session(session)
            case ["segment", seg_id]:
                return self.segment(seg_id, query)
            case ["frames", session]:
                return self.levels(session, query)
        raise NotFound("/".join(path))

    def overview(self) -> dict:
        counts = {
            row["query"]: row
            for row in self.rows(
                "SELECT m.query, count(*) AS segments, count(DISTINCT s.session) AS sessions,"
                " round(sum(s.dur) / 60, 1) AS minutes"
                " FROM mining m JOIN segments s USING (seg_id) GROUP BY m.query"
            )
        }
        queries = [
            {
                "name": q["name"],
                "note": q.get("note", ""),
                "order": q.get("order"),
                "segments": counts.get(q["name"], {}).get("segments", 0),
                "sessions": counts.get(q["name"], {}).get("sessions", 0),
                "minutes": counts.get(q["name"], {}).get("minutes", 0.0),
            }
            for q in self.queries
        ]
        sessions = self.rows("SELECT * FROM sessions ORDER BY session")
        return {
            "index": self.index,
            "stats": self.stats,
            "queries": queries,
            "sessions": sessions,
            "license": self.release.competition.license,
        }

    def hits(self, name: str, query: dict) -> dict:
        if not QUERY.fullmatch(name) or name not in {q["name"] for q in self.queries}:
            raise NotFound(name)
        offset = int(number(query, "offset", 0, 0, 10_000_000))
        limit = int(number(query, "limit", 50, 1, MAX_HITS))
        (total,) = self.db.cursor().execute("SELECT count(*) FROM mining WHERE query = ?", [name]).fetchone()
        hits = self.rows(
            f"SELECT {self.cols('segments', SEG_COLS, 's')} FROM mining m JOIN segments s USING (seg_id)"
            " WHERE m.query = ? ORDER BY m.rank LIMIT ? OFFSET ?",
            [name, limit, offset],
        )
        return {"query": name, "total": total, "offset": offset, "hits": hits}

    def session_row(self, session: str) -> dict:
        if not SESSION.fullmatch(session):
            raise NotFound(session)
        found = self.rows("SELECT * FROM sessions WHERE session = ?", [session])
        if not found:
            raise NotFound(session)
        return found[0]

    def session(self, session: str) -> dict:
        row = self.session_row(session)
        lanes = self.rows(
            f"SELECT {self.cols('segments', LANE_COLS)} FROM segments WHERE session = ? ORDER BY start",
            [session],
        )
        return {"session": row, "segments": lanes, "media": self.media(session)}

    def segment(self, seg_id: str, query: dict) -> dict:
        if not SEG_ID.fullmatch(seg_id):
            raise NotFound(seg_id)
        found = self.rows("SELECT * FROM segments WHERE seg_id = ?", [seg_id])
        if not found:
            raise NotFound(seg_id)
        row = found[0]
        pad = number(query, "pad", 1.0, 0.0, MAX_PAD_S)
        start, end = max(0.0, row["start"] - pad), row["end"] + pad
        nearby = self.rows(
            f"SELECT {self.cols('segments', LANE_COLS)} FROM segments"
            ' WHERE session = ? AND "end" > ? AND start < ? AND seg_id <> ? ORDER BY start',
            [row["session"], start, end, seg_id],
        )
        return {
            "segment": row,
            "span": [start, end],
            "nearby": nearby,
            "session": self.session_row(row["session"]),
            "media": self.media(row["session"]),
        }

    def media(self, session: str) -> dict:
        """Presigned GETs for the session's originals this release holds."""
        video = {}
        for camera in CAMERAS:
            url = self.release.url(f"Video/V{session[1:]}-{camera}.mp4")
            if url:
                video[camera] = url
        return {
            "audio": self.release.url(f"Audio/{session}.wav"),
            "aec": self.release.url(f"Audio_AEC/{session}.wav"),
            "video": video,
        }

    def levels(self, session: str, query: dict) -> dict:
        """Frame levels of [from, to) s, max-pooled to at most `points` values."""
        row = self.session_row(session)
        frames = self.frame_table(session)
        hop = frames["hop"]
        length = len(frames["mic_db"])
        start = number(query, "from", 0.0, 0.0, 1e6)
        stop = number(query, "to", row["dur"] or length * hop, 0.0, 1e6)
        points = int(number(query, "points", 800, 10, MAX_POINTS))
        a = min(length, int(start / hop))
        b = min(length, max(a, math.ceil(stop / hop)))
        step = max(1, math.ceil((b - a) / points))
        out = {"hop": hop, "from": a * hop, "step": step * hop}
        for name in ("mic_db", "ref_db", "aec_db"):
            values = frames[name]
            if values is None or len(values) == 0:
                out[name] = None
                continue
            span = values[a:b]
            pad = (-len(span)) % step
            if pad:
                span = np.concatenate([span, np.full(pad, np.nan, dtype=span.dtype)])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN bucket is null
                peak = np.nanmax(span.reshape(-1, step), axis=1)
            out[name] = [None if not math.isfinite(v) else round(float(v), 1) for v in peak]
        return out

    def frame_table(self, session: str) -> dict:
        with self.lock:
            if session in self.frames:
                self.frames.move_to_end(session)
                return self.frames[session]
        name = f"frames/{session}.parquet"
        if name not in self.release.files:
            raise NotFound(name)
        table = pq.read_table(io.BytesIO(self.release.read(name)))
        frames = {"hop": float(self.index.get("frame_hop_s", 0.05))}
        for column in ("mic_db", "ref_db", "aec_db"):
            frames[column] = (
                table.column(column).to_numpy(zero_copy_only=False).astype(np.float32)
                if column in table.column_names
                else None
            )
        with self.lock:
            self.frames[session] = frames
            while len(self.frames) > FRAME_CACHE:
                self.frames.popitem(last=False)
        return frames
