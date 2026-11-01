"""Kind `corpus`: a synthetic sample bank, read directly and over HTTP."""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from conftest import cookie, parquet
from dataservice import access, app, catalog
from dataservice.kinds import NotFound, corpus, roboheard
from dataservice.store import Releases

PREFIX = "samples/asr-v1/"
MEMBER = {"preferred_username": "ann", "github_user_id": "7", "gateway_roles": ["data"]}


def bank_files() -> dict[str, bytes]:
    bank = {
        "version": "asr-v1",
        "sr": 16000,
        "kbps": 32,
        "kinds": [{"id": "far-meeting", "title": "远场会议", "note": "8 路阵列"}],
        "corpora": [
            {
                "id": "alimeeting",
                "title": "AliMeeting",
                "kind": "far-meeting",
                "status": "sampled",
                "clips": 2,
                "groups": ["overlap", "solo"],
            },
            {"id": "chime6", "title": "CHiME-6", "kind": "far-meeting", "status": "listed"},
        ],
    }
    clips = {
        "clip_id": ["alimeeting-solo-00", "alimeeting-overlap-00"],
        "corpus": ["alimeeting", "alimeeting"],
        "group": ["solo", "overlap"],
        "text": ["一个人说", "插话"],
        "speaker": ["SPK0001", "SPK0002"],
        "start": [1.0, 5.0],
        "end": [4.0, 9.0],
        "dur": [3.6, 4.6],
        "src": ["Train_Ali_far/audio_dir/R0001_MS001.wav"] * 2,
        "src_root": ["/secret/path"] * 2,
        "flags": ["", ""],
        "signals": ["far near", "far"],
        "ov": [0.0, float("nan")],
        "error": ["", ""],
    }
    return {
        "corpora.json": json.dumps(bank).encode(),
        "clips.parquet": parquet(clips),
        "clips/alimeeting/alimeeting-solo-00/far.opus": b"OggS0123",
        "clips/alimeeting/alimeeting-solo-00/near.opus": b"OggS",
        "clips/alimeeting/alimeeting-overlap-00/far.opus": b"OggS",
        "_SUCCESS": b"",
    }


@pytest.fixture
def bank(store):
    store.put(bank_files(), bucket="datasets", prefix=PREFIX)
    return catalog.Competition(
        id="asr-corpora",
        title="ASR 语料样本",
        kind="corpus",
        bucket="datasets",
        prefix=PREFIX,
        license="各语料自己的许可",
    )


KINDS = {"roboheard": roboheard.View, "corpus": corpus.View}


def test_corpora_and_clips(store, bank):
    view = Releases(store, KINDS).get(bank).view
    found = view.answer(["corpora"], {})
    assert found["version"] == "asr-v1" and found["license"] == "各语料自己的许可"
    assert [c["status"] for c in found["corpora"]] == ["sampled", "listed"]

    found = view.answer(["corpus", "alimeeting"], {})
    assert found["corpus"]["title"] == "AliMeeting"
    solo = next(c for c in found["clips"] if c["clip_id"] == "alimeeting-solo-00")
    assert solo["text"] == "一个人说" and solo["dur"] == 3.6
    assert set(solo["signals"]) == {"far", "near"}
    assert solo["signals"]["near"].startswith("raw/clips/alimeeting/alimeeting-solo-00/near.opus?")
    assert solo["extra"] == {"ov": 0.0} and "src_root" not in solo and "signals" not in solo["extra"]
    overlap = next(c for c in found["clips"] if c["group"] == "overlap")
    assert list(overlap["signals"]) == ["far"] and overlap["extra"] == {}


@pytest.mark.parametrize("path", [["corpus", "chime6x"], ["corpus", "../x"], ["corpus"], ["sessions"], []])
def test_unknown_corpus_paths(store, bank, path):
    with pytest.raises(NotFound):
        Releases(store, KINDS).get(bank).view.answer(path, {})


def test_corpus_over_http(store, competition, bank, tmp_path):
    service = app.Service(
        {competition.id: competition, bank.id: bank},
        Releases(store, KINDS),
        access.Grants(ask=lambda github: ["asr-corpora"]),
        access.Keys(str(tmp_path / "keys.sqlite3")),
    )
    server = app.serve(app.Web, service, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        connection.request("GET", path, headers={"Cookie": cookie(MEMBER), **(headers or {})})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    try:
        status, _, body = get("/c/asr-corpora/")
        assert status == 200 and "/data/static/corpus.js" in body.decode()
        status, _, body = get("/api/asr-corpora/corpus/alimeeting")
        link = json.loads(body)["clips"][1]["signals"]["far"]
        status, headers, body = get("/api/asr-corpora/" + link)
        assert status == 200 and body == b"OggS0123" and headers["Content-Type"] == "audio/ogg"
        assert get("/static/corpus.css")[0] == 200
        assert get("/api/roboheard-2027/sessions")[0] == 404  # not granted
    finally:
        server.shutdown()
        server.server_close()
