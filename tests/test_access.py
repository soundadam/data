from __future__ import annotations

import pytest

from conftest import cookie
from dataservice import access, catalog


def test_claims_need_exactly_one_token():
    assert access.access_token_claims(cookie({"a": 1})) == {"a": 1}
    with pytest.raises(access.Unauthenticated):
        access.access_token_claims(cookie({"a": 1}) + "; " + cookie({"a": 2}))
    with pytest.raises(access.Unauthenticated):
        access.access_token_claims("other=1")


@pytest.mark.parametrize("value", [None, "", "0123", "abc", True, 12.5])
def test_github_id_is_digits(value):
    with pytest.raises(access.Unauthenticated):
        access.github_id({"github_user_id": value})


def test_github_id_accepts_int():
    assert access.github_id({"github_user_id": 4242}) == "4242"


def test_grants_are_cached_for_a_minute():
    calls, clock = [], [0.0]
    grants = access.Grants(ask=lambda g: calls.append(g) or ["roboheard-2027"], now=lambda: clock[0])
    assert grants.of("1") == {"roboheard-2027"}
    clock[0] = 59
    grants.of("1")
    assert calls == ["1"]
    clock[0] = 61
    grants.of("1")
    assert calls == ["1", "1"]


def test_keys_issue_owner_revoke(tmp_path):
    keys = access.Keys(str(tmp_path / "keys.sqlite3"))
    key_id, key = keys.issue("7", "g18")
    assert key.startswith("sdd_") and access.KEY.fullmatch(key)
    assert keys.owner(f"Bearer {key}") == "7"
    listed = keys.of("7")
    assert [k["id"] for k in listed] == [key_id] and listed[0]["used"]
    assert key not in (tmp_path / "keys.sqlite3").read_bytes().decode("latin-1")
    assert not keys.revoke("8", key_id)
    assert keys.revoke("7", key_id)
    with pytest.raises(access.Unauthenticated):
        keys.owner(f"Bearer {key}")


@pytest.mark.parametrize(
    "header", ["", "Bearer", "Basic sdd_x", "Bearer sdd_short", "Bearer " + "sdd_" + "a" * 43]
)
def test_unknown_keys_are_refused(tmp_path, header):
    with pytest.raises(access.Unauthenticated):
        access.Keys(str(tmp_path / "k.sqlite3")).owner(header)


def test_keys_are_limited_and_labelled(tmp_path):
    keys = access.Keys(str(tmp_path / "keys.sqlite3"))
    with pytest.raises(ValueError):
        keys.issue("7", "")
    with pytest.raises(ValueError):
        keys.issue("7", "x" * 41)
    for n in range(access.MAX_KEYS):
        keys.issue("7", f"k{n}")
    with pytest.raises(ValueError):
        keys.issue("7", "one more")
    keys.issue("8", "someone else")


def test_shipped_catalog_loads():
    found = catalog.load(kinds=frozenset({"roboheard"}))
    assert list(found) == ["roboheard-2027"]
    assert found["roboheard-2027"].public() == {
        "id": "roboheard-2027",
        "title": "RoboHEARD 2027 训练集",
        "kind": "roboheard",
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "Bad", "title": "t", "kind": "roboheard", "bucket": "b", "prefix": "p/"},
        {"id": "ok", "title": "", "kind": "roboheard", "bucket": "b", "prefix": "p/"},
        {"id": "ok", "title": "t", "kind": "unknown", "bucket": "b", "prefix": "p/"},
        {"id": "ok", "title": "t", "kind": "roboheard", "bucket": "b", "prefix": "p"},
    ],
)
def test_catalog_refuses_bad_entries(entry):
    import json

    with pytest.raises(ValueError):
        catalog.load(json.dumps({"competitions": [entry]}), kinds=frozenset({"roboheard"}))
