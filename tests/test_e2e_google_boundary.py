"""通常dispatch・実DO判定・代替APIで17件の別HTTP繰越と回収を検証する。"""

import asyncio
import json
from copy import deepcopy

import pytest

import e2e_google_boundary_probe as boundary
import e2e_google_sync_probe as probe
from e2e_google_boundary_state import STATUSES, pending_count
from e2e_google_sync_state import KEYS
from tests.test_e2e_google_sync_probe import Scenario


def scenario(monkeypatch):
    """通信だけを代替し、通常同期・KV・DOの所有権判定は実装を使う。"""
    test = Scenario(monkeypatch)
    for name in ("_google_request", "get_google_access_token", "_verify_calendar", "_verify_guild", "_verify_database"):
        monkeypatch.setattr(boundary, name, getattr(probe, name))
    return test


def run_to(test, last):
    """全段階を別HTTPで進め、verify前のadvanceを行わない。"""
    for step in range(last + 1):
        status, result = test.call("boundary" if step == 0 else "advance")
        assert status == 200, (step, result)
        assert result["status"] == STATUSES[step]
        status, result = test.call("verify")
        assert status == 200, (step, result)


def cleanup(test):
    """最大4HTTPで回収し、部分回収を成功に数えない。"""
    for _ in range(4):
        status, result = test.call("cleanup")
        if status == 200:
            return result
        assert result["error"] == "google_sync_cleanup_pending", result
        assert test.owner()["dirty"] is True
    pytest.fail("cleanup did not finish")


@pytest.mark.parametrize("history_count", [0, 100])
def test_seventeen_shared_queue_cursor_maps_and_cleanup(monkeypatch, history_count):
    test = scenario(monkeypatch)
    before = dict(test.env.STATE_KV.data)
    for i in range(history_count):
        test.google[f"old-{i}"] = {"id": f"old-{i}", "status": "cancelled"}
    run_to(test, 18)
    assert len(test.google) == 17 + history_count
    assert len(test.pages) == len(test.discord) == 0
    assert len(json.loads(test.env.STATE_KV.data[KEYS[3]])) == 17
    assert KEYS[0] not in test.env.STATE_KV.data
    previous_ids = {}
    for step, remaining in zip(range(19, 23), (12, 7, 2, 0)):
        status, result = test.call("advance")
        assert status == 200, result
        verified_status, verified = test.call("verify")
        assert verified_status == 200, (step, verified, len(json.dumps(test.owner()).encode()))
        assert len(json.loads(test.env.STATE_KV.data[KEYS[3]])) == remaining
        assert len(test.pages) == len(test.discord) == 17 - remaining
        owner = test.owner()
        assert len(owner["pending_ids"]) == pending_count(step)
        assert test.env.STATE_KV.data[KEYS[0]] == owner["expected_cursor"]
        mapping = json.loads(test.env.STATE_KV.data[KEYS[2]])
        assert all(mapping[k] == v for k, v in previous_ids.items())
        previous_ids = mapping
    for _ in range(23, 27):
        assert test.call("advance")[0] == 200
        assert test.call("verify")[0] == 200
    cleanup(test)
    owner = test.owner()
    assert owner["outcome"] == "passed" and owner["dirty"] is False
    assert all(owner["stages"][f"google_boundary_cleanup_{i}"] == 200 for i in range(17))
    assert all(e["status"] == "cancelled" for e in test.google.values())
    assert not test.discord and all(p.get("archived") for p in test.pages.values())
    assert test.env.STATE_KV.data == before
    assert test.call("cleanup")[0] == 200


@pytest.mark.parametrize("step", [0, 1, 17, 18, 19, 21, 22, 25])
def test_partial_run_cleanup_is_failed_clean(monkeypatch, step):
    test = scenario(monkeypatch)
    run_to(test, step)
    cleanup(test)
    assert test.owner()["outcome"] == "failed_clean"
    assert not test.discord and all(p.get("archived") for p in test.pages.values())


@pytest.mark.parametrize("mutation", ["count", "id", "run", "boundary", "source", "step", "pending", "cleaned"])
def test_owner_mutation_rejected(monkeypatch, mutation):
    test = scenario(monkeypatch)
    run_to(test, 18)
    owner = deepcopy(test.owner())
    if mutation == "count":
        owner["fixtures"].pop()
    elif mutation == "id":
        owner["fixtures"][0]["google_event_id"] = "foreign"
    elif mutation == "run":
        owner["run_id"] = "E2E-20260925T000000Z-aaaaaaaa"
    elif mutation == "boundary":
        owner["boundary"] = False
    elif mutation == "source":
        owner["fixtures"][0]["source"]["description"] += "changed"
    elif mutation == "step":
        owner["step"] += 2
    elif mutation == "pending":
        owner["pending_ids"][0] = "foreign"
    else:
        owner["fixtures"][0]["cleaned"] = True
    with pytest.raises(RuntimeError):
        asyncio.run(test.store.put_e2e_manifest("google_sync", owner))
    cleanup(test)


def test_queue_corruption_does_not_advance_or_delete_foreign_state(monkeypatch):
    test = scenario(monkeypatch)
    run_to(test, 19)
    original = test.env.STATE_KV.data[KEYS[3]]
    test.env.STATE_KV.data[KEYS[3]] = '[]'
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 409
    assert test.owner()["dirty"] is True
    test.env.STATE_KV.data[KEYS[3]] = original
    cleanup(test)


def test_failed_apply_preserves_cursor_and_reclaims_partial_targets(monkeypatch):
    test = scenario(monkeypatch)
    run_to(test, 19)
    cursor = test.env.STATE_KV.data[KEYS[0]]
    test.fail_create = True
    status, result = test.call("advance")
    assert status == 409 and result["error"] == "google_boundary_apply_mismatch"
    assert test.env.STATE_KV.data[KEYS[0]] == cursor
    assert len(json.loads(test.env.STATE_KV.data[KEYS[3]])) == 12
    test.fail_create = False
    cleanup(test)
    assert test.owner()["outcome"] == "failed_clean"
