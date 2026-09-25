"""照会失敗からの復旧・重複拒否・途中回収。APIはローカル代替。"""

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


class QueryScenario(Scenario):
    rejections: int = 0


def scenario(monkeypatch, status=400, code="validation_error"):
    test = QueryScenario(monkeypatch)
    monkeypatch.setattr(boundary, "_google_request", probe._google_request)
    original = google_apply_sync.fetch
    test.rejections = 0

    async def api(url, options):
        if json.loads(options.get("body") or "{}").get("page_size") == "invalid":
            test.rejections += 1
            return Response(json.dumps({"code": code}), status=status)
        return await original(url, options)

    monkeypatch.setattr(google_apply_sync, "fetch", api)
    return test


def advance(test, count):
    status, payload = test.call("notion-query")
    assert status == 200, payload
    status, payload = test.call("verify")
    assert status == 200 and payload["stage"] == "google_pending_verified", payload
    for step in range(count):
        status, payload = test.call("advance")
        assert status == 200, payload
        status, payload = test.call("verify")
        assert status == 200, payload
        assert payload["stage"] == f"google_{('retry_pending', 'retried', 'drained')[step]}_verified"


def test_actual_query_response_retains_queue_then_recovers(monkeypatch):
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
    assert all(test.owner()["stages"][f"notion_query_cleanup_{i}"] == 200 for i in range(3))
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


def test_cannot_remove_query_failure_requirement(monkeypatch):
    test = scenario(monkeypatch)
    advance(test, 0)
    owner = test.owner()
    owner["notion_query_retry"] = False
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
