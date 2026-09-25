"""作成失敗からの復旧・重複拒否・途中回収。APIはローカル代替。"""

import asyncio
import json
from copy import deepcopy

import pytest
from workers import Response

import google_apply_sync
import e2e_google_boundary_probe as boundary
import e2e_google_sync_probe as probe
from e2e_google_sync_state import KEYS, SERVICE
from tests.test_e2e_google_sync_probe import Scenario


class CreateScenario(Scenario):
    rejections: int = 0


def scenario(monkeypatch, status=400, code="validation_error"):
    test = CreateScenario(monkeypatch)
    monkeypatch.setattr(boundary, "_google_request", probe._google_request)
    original = google_apply_sync.fetch
    test.rejections = 0

    async def api(url, options):
        if json.loads(options.get("body") or "{}").get("children") == "invalid":
            test.rejections += 1
            return Response(json.dumps({"code": code}), status=status)
        return await original(url, options)

    monkeypatch.setattr(google_apply_sync, "fetch", api)
    return test


def advance(test, count):
    status, payload = test.call("notion-create")
    assert status == 200, payload
    status, payload = test.call("verify")
    assert status == 200 and payload["stage"] == "google_pending_verified", payload
    for step in range(count):
        status, payload = test.call("advance")
        assert status == 200, payload
        status, payload = test.call("verify")
        assert status == 200, payload
        assert payload["stage"] == f"google_{('retry_pending', 'retried', 'drained')[step]}_verified"


def test_actual_create_response_retains_queue_then_recovers(monkeypatch):
    test = scenario(monkeypatch)
    baseline = dict(test.env.STATE_KV.data)
    advance(test, 0)
    before = {k: test.env.STATE_KV.data[k] for k in KEYS}
    first_ids = [(s.get("notion_page_id"), s.get("discord_event_id")) for s in test.owner()["fixtures"]]
    assert test.call("advance")[0] == 200
    assert test.rejections == 1
    assert all(test.env.STATE_KV.data[k] == before[k] for k in KEYS[:-1])
    assert len(test.pages) == len(test.discord) == 1
    assert test.call("advance")[0] == 409
    assert test.call("verify")[0] == 200
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    assert len(test.pages) == len(test.discord) == 3
    ids = [(s.get("notion_page_id"), s.get("discord_event_id")) for s in test.owner()["fixtures"]]
    assert all(pair == ids[i] for i, pair in enumerate(first_ids) if pair[0])
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    assert ids == [(s.get("notion_page_id"), s.get("discord_event_id")) for s in test.owner()["fixtures"]]
    assert len(test.pages) == len(test.discord) == 3
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"
    stages = test.owner()["stages"]
    assert stages["notion_create_api_rejection"] == 400
    assert stages["notion_create_failed_dispatch"] == 500
    assert all(stages[f"notion_create_{stage}"] == 200 for stage in (
        "validation_error", "cursor_preserved", "queue_only_retry", "reapply",
        *[f"step_{i}" for i in range(4)], *[f"unique_{i}" for i in range(4)],
    ))
    assert not any(k.startswith("notion_query_") for k in stages)
    assert all(test.owner()["stages"][f"notion_create_cleanup_{i}"] == 200 for i in range(3))
    assert test.env.STATE_KV.data == baseline
    assert not test.discord and all(p.get("archived") for p in test.pages.values())


@pytest.mark.parametrize("status,code", [(200, "validation_error"), (400, "invalid_request"), (403, "restricted_resource"), (429, "rate_limited"), (503, "service_unavailable")])
def test_unexpected_response_cannot_pass(monkeypatch, status, code):
    test = scenario(monkeypatch, status, code)
    advance(test, 0)
    assert test.call("advance")[0] == 409
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


@pytest.mark.parametrize("step", [0, 1, 2])
def test_incomplete_scenario_cleanup_is_failed_clean(monkeypatch, step):
    test = scenario(monkeypatch)
    advance(test, step)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_cannot_remove_create_failure_requirement(monkeypatch):
    test = scenario(monkeypatch)
    advance(test, 0)
    owner = test.owner()
    owner["notion_create_retry"] = False
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest(SERVICE, owner))


def test_duplicate_after_recovery_prevents_success(monkeypatch):
    test = scenario(monkeypatch)
    advance(test, 3)
    page = deepcopy(next(iter(test.pages.values())))
    page["id"] = "duplicate"
    test.pages["duplicate"] = page
    assert test.call("verify")[0] == 409
    assert test.owner()["stage"] == "ready"
    assert test.call("cleanup")[0] == 409
    assert test.owner()["dirty"] is True


def test_retry_failure_is_not_accepted(monkeypatch):
    test = scenario(monkeypatch)
    advance(test, 1)
    test.fail_create = True
    assert test.call("advance")[0] == 409
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_many_tombstones_preserved_with_bounded_manifest(monkeypatch):
    test = scenario(monkeypatch)
    tombstones = {f"old-{i}": {"id": f"old-{i}", "status": "cancelled"} for i in range(256)}
    test.google.update(deepcopy(tombstones))
    advance(test, 3)
    assert len(json.dumps(test.owner()).encode()) < 32768
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"
    assert {k: test.google[k] for k in tombstones} == tombstones


@pytest.mark.parametrize("mutation", ["removed", "changed"])
def test_tombstone_baseline_is_immutable(monkeypatch, mutation):
    test = scenario(monkeypatch)
    test.google["old"] = {"id": "old", "status": "cancelled"}
    advance(test, 0)
    owner = test.owner()
    owner["baseline_deleted"] = [] if mutation == "removed" else ["a" * 64]
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest(SERVICE, owner))


def test_changed_tombstone_is_not_applied(monkeypatch):
    test = scenario(monkeypatch)
    test.google["old"] = {"id": "old", "status": "cancelled"}
    advance(test, 0)
    test.google["old"]["description"] = "changed"
    assert test.call("advance")[0] == 409
    assert test.rejections == 0
    assert test.call("cleanup")[0] == 200
    assert test.google["old"]["description"] == "changed"


def test_tombstone_limit_rejects_before_writes(monkeypatch):
    test = scenario(monkeypatch)
    test.google.update({f"old-{i}": {"id": f"old-{i}", "status": "cancelled"} for i in range(257)})
    status, payload = test.call("notion-create")
    assert status == 409 and payload["error"] == "google_sync_baseline_limit"
    assert not test.pages and not test.discord and len(test.google) == 257
    assert not asyncio.run(test.store.get_e2e_manifest(SERVICE))
