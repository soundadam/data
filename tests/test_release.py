from __future__ import annotations

import pytest

from conftest import release_files
from dataservice import store as store_module
from dataservice.kinds import BadRequest, NotFound, roboheard
from dataservice.store import NotPublished, Releases


@pytest.fixture
def view(store, competition):
    return Releases(store, {"roboheard": roboheard.View}).get(competition).view


def test_unsealed_prefix_is_not_published(store, competition):
    del store.objects[("roboheard", "train-v1/_SUCCESS")]
    with pytest.raises(NotPublished):
        Releases(store, {"roboheard": roboheard.View}).get(competition)


def test_release_reloads_only_when_the_seal_changes(store, competition, monkeypatch):
    releases = Releases(store, {"roboheard": roboheard.View})
    first = releases.get(competition)
    reads = store.reads
    assert releases.get(competition) is first and store.reads == reads

    monkeypatch.setattr(store_module, "RECHECK_S", -1)
    assert releases.get(competition) is first and store.reads == reads
    store.put({"_SUCCESS": b"again"})
    assert releases.get(competition) is not first


def test_overview_counts_queries_and_keeps_nan_out(view):
    found = view.answer(["overview"], {})
    assert [q["name"] for q in found["queries"]] == ["overlap", "empty"]
    assert found["queries"][0] | {"note": None} == {
        "name": "overlap",
        "note": None,
        "order": None,
        "segments": 2,
        "sessions": 1,
        "minutes": 0.1,
    }
    assert found["queries"][1]["segments"] == 0
    assert [s["session"] for s in found["sessions"]] == ["S001", "S002"]
    assert found["license"] == "CC BY-NC-SA 4.0"


def test_hits_keep_the_published_order(view):
    found = view.answer(["query", "overlap"], {"limit": ["1"]})
    assert found["total"] == 2
    assert [h["seg_id"] for h in found["hits"]] == ["S001-P0002-0001"]
    assert found["hits"][0]["snr_db"] is None  # NaN in the parquet
    second = view.answer(["query", "overlap"], {"offset": ["1"]})
    assert [h["seg_id"] for h in second["hits"]] == ["S001-P0001-0001"]


@pytest.mark.parametrize(
    "path",
    [
        ["query", "nope"],
        ["query", "DROP TABLE"],
        ["session", "S9999"],
        ["session", "x"],
        ["segment", "S001-P0001-9999"],
        ["frames", "S002"],
        ["other"],
    ],
)
def test_unknown_paths_are_not_found(view, path):
    with pytest.raises(NotFound):
        view.answer(path, {})


@pytest.mark.parametrize(
    "query", [{"limit": ["0"]}, {"limit": ["501"]}, {"offset": ["-1"]}, {"limit": ["nan"]}]
)
def test_out_of_range_parameters_are_refused(view, query):
    with pytest.raises(BadRequest):
        view.answer(["query", "overlap"], query)


def test_session_has_lanes_and_only_published_media(view):
    found = view.answer(["session", "S001"], {})
    assert [s["seg_id"] for s in found["segments"]] == ["S001-P0001-0001", "S001-P0002-0001", "S001-R-0001"]
    assert found["media"]["audio"].startswith("https://s3.api.example/roboheard/train-v1/Audio/S001.wav")
    assert found["media"]["aec"] is None
    assert list(found["media"]["video"]) == ["head_leftImg"]


def test_segment_span_and_nearby(view):
    found = view.answer(["segment", "S001-P0001-0001"], {"pad": ["0.5"]})
    assert found["span"] == [0.5, 3.5]
    assert [s["seg_id"] for s in found["nearby"]] == ["S001-P0002-0001"]
    assert found["session"]["session"] == "S001"


def test_frames_are_max_pooled(view):
    found = view.answer(["frames", "S001"], {"from": ["0"], "to": ["10"], "points": ["10"]})
    assert found["step"] == pytest.approx(1.0)
    assert len(found["mic_db"]) == 10
    assert found["mic_db"][:4] == [-50.0, -20.0, -20.0, -50.0]
    assert found["aec_db"] is None
    assert found["ref_db"] == [-70.0] * 10


def test_frames_bucket_of_only_nan_is_null(store, competition):
    files = release_files()
    import numpy as np

    from conftest import parquet

    mic = np.full(20, np.nan, dtype=np.float32)
    mic[15:] = -30.0
    store.put({"frames/S001.parquet": parquet({"mic_db": mic, "ref_db": mic}), "_SUCCESS": files["_SUCCESS"]})
    view = Releases(store, {"roboheard": roboheard.View}).get(competition).view
    found = view.answer(["frames", "S001"], {"from": ["0"], "to": ["1"], "points": ["10"]})
    assert found["mic_db"] == [None] * 7 + [-30.0] * 3
