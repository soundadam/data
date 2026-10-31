"""A fake origin holding one tiny, synthetic roboheard release."""

from __future__ import annotations

import base64
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dataservice import catalog
from dataservice.store import BadRange, Stream

PREFIX = "train-v1/"


def parquet(columns: dict) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.table(columns), sink)
    return sink.getvalue()


def release_files() -> dict[str, bytes]:
    segments = {
        "seg_id": ["S001-P0001-0001", "S001-P0002-0001", "S001-R-0001", "S002-P0001-0001"],
        "session": ["S001", "S001", "S001", "S002"],
        "tier": ["P0001", "P0002", "audio_server_raw_audio_data", "P0001"],
        "kind": ["human", "human", "robot", "human"],
        "speaker": ["P0001", "P0002", None, "P0001"],
        "start": [1.0, 2.0, 4.0, 0.5],
        "end": [3.0, 3.5, 6.0, 1.5],
        "dur": [2.0, 1.5, 2.0, 1.0],
        "text": ["你好小智", "打开灯", "好的", "关灯"],
        "flags": ["", "overlap", "", ""],
        "n_ov_spk": [1, 1, 0, 0],
        "ov_human": [1.0, 1.0, 0.0, 0.0],
        "ov_robot": [0.0, 0.0, 0.0, 0.0],
        "snr_db": [12.5, float("nan"), 3.0, 9.0],
        "ref_db": [-60.0, -60.0, -20.0, -70.0],
        "gender": ["f", "m", None, "f"],
        "age": [31.0, float("nan"), None, 31.0],
        "origin": ["江苏", "四川", None, "江苏"],
    }
    sessions = {
        "session": ["S001", "S002"],
        "dur": [10.0, 5.0],
        "n_speakers": [2, 1],
        "human_speech": [3.5, 1.0],
        "robot_speech": [2.0, 0.0],
        "overlap_2plus": [1.0, 0.0],
        "has_aec": [True, False],
        "dead_channels": ["", "3"],
    }
    listen = {
        "signals": ["mic1", "mic2", "mean", "ref", "aec1"],
        "hop_s": 0.02,
        "tile": 4096,
        "rows": 128,
        "aec_shift_s": {"S001": 0.07375},
    }
    return {
        "index.json": json.dumps(
            {"version": 3, "sessions": 2, "cameras": [], "code": "abc1234", "listen": listen}
        ).encode(),
        "sessions.parquet": parquet(sessions),
        "segments.parquet": parquet(segments),
        "listen/S001/mic1.mp3": b"ID3",
        "listen/S001/mic1.jpg": b"\xff\xd8",
        "listen/S001/mean.mp3": b"ID3!0123456789",
        "listen/S001/mean.jpg": b"\xff\xd8",
        "listen/S001/ref.mp3": b"ID3",  # no sheet: not offered
        "Audio/S001.wav": b"RIFF0123456789",
        "Video/V001-head_leftImg.mp4": b"\0",
        "_SUCCESS": b"",
    }


class FakeStore:
    def __init__(self, objects: dict[tuple[str, str], bytes] | None = None):
        self.objects = dict(objects or {})
        self.reads = 0

    def put(self, files: dict[str, bytes], bucket: str = "roboheard", prefix: str = PREFIX) -> None:
        for name, body in files.items():
            self.objects[(bucket, prefix + name)] = body

    def get(self, bucket, key):
        self.reads += 1
        return self.objects[(bucket, key)]

    def seal(self, bucket, key):
        body = self.objects.get((bucket, key))
        return None if body is None else f'"{len(body)}-{hash(body)}"'

    def stream(self, bucket, key, byte_range):
        body = self.objects[(bucket, key)]
        if byte_range is None:
            return Stream(200, {"Content-Length": str(len(body))}, iter([body]))
        first, _, last = byte_range.removeprefix("bytes=").partition("-")
        start, stop = (
            (len(body) - int(last), len(body) - 1) if not first else (int(first), int(last or len(body) - 1))
        )
        if start >= len(body):
            raise BadRange(key)
        part = body[start : stop + 1]
        headers = {"Content-Length": str(len(part)), "Content-Range": f"bytes {start}-{stop}/{len(body)}"}
        return Stream(206, headers, iter([part]))

    def listing(self, bucket, prefix):
        return {
            k.removeprefix(prefix): len(v)
            for (b, k), v in self.objects.items()
            if b == bucket and k.startswith(prefix)
        }


@pytest.fixture
def competition():
    return catalog.Competition(
        id="roboheard-2027",
        title="RoboHEARD 2027 训练集",
        kind="roboheard",
        bucket="roboheard",
        prefix=PREFIX,
        license="CC BY-NC-SA 4.0",
    )


@pytest.fixture
def store():
    found = FakeStore()
    found.put(release_files())
    return found


def cookie(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"AccessToken=e30.{payload}.sig"
