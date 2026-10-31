from __future__ import annotations

import urllib.parse

import pytest

from dataservice import store as store_module
from dataservice.kinds import NotFound, roboheard
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


def test_sessions_list_the_release(view):
    found = view.answer(["sessions"], {})
    assert [s["session"] for s in found["sessions"]] == ["S001", "S002"]
    assert found["license"] == "CC BY-NC-SA 4.0"
    assert found["index"]["code"] == "abc1234" and "listen" not in found["index"]


@pytest.mark.parametrize(
    "path", [["session", "S9999"], ["session", "x"], ["overview"], ["frames", "S001"], []]
)
def test_unknown_paths_are_not_found(view, path):
    with pytest.raises(NotFound):
        view.answer(path, {})


def test_session_has_lanes_speakers_and_only_published_media(view):
    found = view.answer(["session", "S001"], {})
    assert [s["seg_id"] for s in found["segments"]] == ["S001-P0001-0001", "S001-P0002-0001", "S001-R-0001"]
    assert found["segments"][2] == {
        "seg_id": "S001-R-0001",
        "kind": "robot",
        "speaker": None,
        "start": 4.0,
        "end": 6.0,
        "text": "好的",
    }
    assert found["speakers"] == [
        {"speaker": "P0001", "gender": "f", "age": 31.0, "origin": "江苏", "turns": 1, "speech": 2.0},
        {"speaker": "P0002", "gender": "m", "age": None, "origin": "四川", "turns": 1, "speech": 1.5},
    ]
    assert list(found["signals"]) == ["mic1", "mean"]  # index order; ref has no sheet, the rest no files
    assert found["signals"]["mean"]["mp3"].startswith("raw/listen/S001/mean.mp3?expires=")
    assert found["signals"]["mean"]["jpg"].startswith("raw/listen/S001/mean.jpg?expires=")
    assert list(found["video"]) == ["head_leftImg"]
    assert found["listen"] == {"hop_s": 0.02, "tile": 4096, "rows": 128, "aec_shift_s": 0.07375}


def test_session_without_listen_media_has_no_signals(view):
    found = view.answer(["session", "S002"], {})
    assert found["signals"] == {} and found["video"] == {}
    assert found["listen"]["aec_shift_s"] == 0.0


def test_release_without_listen_still_loads(store, competition):
    store.put({"index.json": b'{"version": 1}', "_SUCCESS": b"older"})
    view = Releases(store, {"roboheard": roboheard.View}).get(competition).view
    found = view.answer(["session", "S001"], {})
    assert found["signals"] == {} and found["listen"]["hop_s"] is None


def test_links_expire_and_bind_competition_and_file():
    now = [1000.0]
    links = store_module.Links(b"k" * 32, clock=lambda: now[0])
    link = links.link("roboheard-2027", "Audio/S001.wav")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert links.valid("roboheard-2027", "Audio/S001.wav", query)
    assert not links.valid("roboheard-2027", "Audio/S002.wav", query)
    assert not links.valid("other", "Audio/S001.wav", query)
    assert not store_module.Links(b"j" * 32, clock=lambda: now[0]).valid(
        "roboheard-2027", "Audio/S001.wav", query
    )
    assert not links.valid("roboheard-2027", "Audio/S001.wav", {**query, "expires": ["99999999999"]})
    now[0] += store_module.LINK_S + 1
    assert not links.valid("roboheard-2027", "Audio/S001.wav", query)
