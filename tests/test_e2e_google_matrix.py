"""5件の形式・複数回繰越・共有KVでのAPI拒否を代替APIで検証する。"""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from workers import Response

import e2e_google_matrix_probe as matrix
import e2e_google_sync_probe as probe
import google_calendar_sync
from e2e_google_matrix_state import STATUSES
from e2e_google_sync_state import KEYS
from tests.test_e2e_google_sync_probe import Scenario


class MatrixScenario(Scenario):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        original = probe._google_request

        async def google(method, url, token, payload=None):
            path = unquote(urlparse(url).path)
            if path.endswith('/instances'):
                parent = path.split('/')[-2]
                return 200, {"items": [deepcopy(e) for e in self.google.values() if e.get("recurringEventId") == parent]}
            result = await original(method, url, token, payload)
            if method == "POST" and isinstance(payload, dict) and payload.get("recurrence"):
                for index in range(2):
                    item = deepcopy(result[1])
                    item.pop("recurrence")
                    item["id"] = f"instance-{index}"
                    item["recurringEventId"] = payload["id"]
                    for key in ("start", "end"):
                        item[key]["dateTime"] = (datetime.fromisoformat(item[key]["dateTime"]) + timedelta(days=index)).isoformat()
                    item["originalStartTime"] = deepcopy(item["start"])
                    self.google[item["id"]] = item
            if method == "DELETE":
                parent = path.rsplit('/', 1)[-1]
                for item in self.google.values():
                    if item.get("recurringEventId") == parent:
                        item["status"] = "cancelled"
            return result

        async def listing(url, options):
            params = parse_qs(urlparse(url).query)
            size = int(params.get("maxResults", [2500])[0])
            start = int(params.get("pageToken", [0])[0])
            items = [e for e in self.google.values() if not e.get("recurrence")]
            data: dict = {"items": items[start:start + size]}
            if start + size < len(items):
                data["nextPageToken"] = str(start + size)
            return Response(json.dumps(data))

        monkeypatch.setattr(matrix, "_google_request", google)
        monkeypatch.setattr(probe, "_google_request", google)
        monkeypatch.setattr(google_calendar_sync, "fetch", listing)
        for name in ("get_google_access_token", "_verify_calendar", "_verify_guild", "_verify_database"):
            monkeypatch.setattr(matrix, name, getattr(probe, name))


def run_to(test, last):
    for step in range(last + 1):
        status, payload = test.call("matrix" if step == 0 else "advance")
        assert status == 200, (step, payload)
        assert payload["status"] == STATUSES[step]
        status, payload = test.call("verify")
        assert status == 200, (step, payload)


@pytest.mark.parametrize("action", ["release", "status"])
@pytest.mark.parametrize("failure", ["exception", "invalid_json"])
def test_step11_control_failure_distinguishes_lock_residue(monkeypatch, action, failure):
    """固定障害で症状を比較する。実サービスの原因を証明するテストではない。"""
    test = MatrixScenario(monkeypatch)
    run_to(test, 10)
    stub = test.env.SYNC_COORDINATOR.getByName("e2e:google-sync-control")
    original = stub.sync_state

    async def fail(payload):
        if json.loads(payload)["action"] == action:
            if failure == "exception":
                raise RuntimeError("fixed control failure")
            return "invalid json"
        return await original(payload)

    monkeypatch.setattr(stub, "sync_state", fail)
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "google_sync_release_failed"
    diagnostic = payload["release_diagnostic"]
    assert diagnostic["step"] == f"{action}_{'rpc' if failure == 'exception' else 'response'}"
    assert diagnostic["exception"] == ("runtime_error" if failure == "exception" else "none")
    assert diagnostic["release_ok"] is (True if action == "status" else None if failure == "exception" else False)
    assert "fixed control failure" not in json.dumps(payload)
    assert test.owner()["stage"] == "ready"
    monkeypatch.setattr(stub, "sync_state", original)
    if action == "release":
        status, payload = test.call("cleanup")
        assert status == 503 and payload["error"] == "google_sync_busy"
        # 代替storageの期限だけを過去へ変更して回収可能になることを確認する。
        storage = stub.durable_object.ctx.storage
        lock = json.loads(storage.data["lock"])
        lock["expires_at"] = 0
        storage.data["lock"] = json.dumps(lock)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


@pytest.mark.parametrize("history_count", [1, 100])
def test_matrix_all_stages_shared_retry_and_cleanup(monkeypatch, history_count):
    test = MatrixScenario(monkeypatch)
    test.google["old"] = {"id": "old", "status": "cancelled"}
    for index in range(history_count - 1):
        test.google[str(index)] = {"id": str(index), "status": "cancelled"}
    run_to(test, 27)
    owner = test.owner()
    assert len(owner["fixtures"]) == 7
    assert owner["stages"]["google_matrix_api_rejection"] == 400
    assert owner["stages"]["google_matrix_cursor_preserved"] == 200
    assert owner["stages"]["google_matrix_multi_cursor_preserved"] == 200
    assert all(owner["stages"][f"google_matrix_rejection_{index}"] == 400 for index in (1, 2, 4))
    assert len(test.pages) == 7 and len(test.discord) == 4
    assert owner["pending_ids"] == []
    assert set(KEYS) <= set(test.env.STATE_KV.data)
    status, payload = test.call("cleanup")
    assert status == 200, payload
    assert test.owner()["outcome"] == "passed"
    assert test.owner()["stages"]["google_matrix_series_cleanup"] == 200
    assert all(e.get("status") == "cancelled" for e in test.google.values())
    assert not test.discord and all(p.get("archived") for p in test.pages.values())
    assert not set(KEYS) & set(test.env.STATE_KV.data)
    assert test.google["old"] == {"id": "old", "status": "cancelled"}


@pytest.mark.parametrize("step", [0, 1, 4, 5, 6, 9, 12, 14, 15, 16, 18, 19, 20, 21, 22, 24, 26])
def test_matrix_can_cleanup_between_phases(monkeypatch, step):
    test = MatrixScenario(monkeypatch)
    run_to(test, step)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"
    assert not test.discord and not set(KEYS) & set(test.env.STATE_KV.data)


def test_matrix_rejects_foreign_event_and_preserves_it(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 4)
    test.google["foreign"] = {"id": "foreign", "status": "cancelled"}
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "google_matrix_unowned_source"
    assert not test.pages and not test.discord
    assert test.call("cleanup")[0] == 200
    assert "foreign" in test.google


def test_matrix_rejects_owner_and_series_replacement(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 4)
    for field, value in (("matrix", False), ("full_apply", False), ("baseline_deleted", {"a" * 64: "b" * 64})):
        owner = test.owner()
        owner[field] = value
        with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
            asyncio.run(test.store.put_e2e_manifest("google_sync", owner))


@pytest.mark.parametrize("mutation", ["id", "parent", "time", "duplicate", "page"])
def test_matrix_rejects_untrusted_instances(monkeypatch, mutation):
    test = MatrixScenario(monkeypatch)
    run_to(test, 3)
    original = matrix._google_request

    async def corrupt(method, url, token, payload=None):
        status, data = await original(method, url, token, payload)
        if '/instances?' in url:
            if mutation == "page":
                data["nextPageToken"] = "unexpected"
            elif mutation == "duplicate":
                data["items"][1] = deepcopy(data["items"][0])
            else:
                data["items"][0][{"id": "id", "parent": "recurringEventId", "time": "originalStartTime"}[mutation]] = None
        return status, data

    monkeypatch.setattr(matrix, "_google_request", corrupt)
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "google_matrix_instances_invalid"
    assert not test.pages and not test.discord
    monkeypatch.setattr(matrix, "_google_request", original)
    assert test.call("cleanup")[0] == 200


@pytest.mark.parametrize("target", ["notion", "discord", "google"])
def test_matrix_checks_all_day_times_independently(monkeypatch, target):
    test = MatrixScenario(monkeypatch)
    run_to(test, 7)
    slot = test.owner()["fixtures"][0]
    if target == "notion":
        test.pages[slot["notion_page_id"]]["properties"]["日時"]["date"]["start"] = "2099-01-01"
    elif target == "discord":
        test.discord[slot["discord_event_id"]]["scheduled_start_time"] = "2099-01-01T00:00:00Z"
    else:
        test.google[slot["google_event_id"]]["end"]["date"] = "2099-01-01"
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200


def test_matrix_preserves_changed_series_on_cleanup(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 4)
    event = test.google[test.owner()["series"]["source"]["id"]]
    event["recurrence"] = ["RRULE:FREQ=DAILY;COUNT=3"]
    status, payload = test.call("cleanup")
    assert status == 409 and payload["error"] == "google_matrix_series_owner_mismatch"
    assert event.get("status") != "cancelled"


def test_matrix_partial_failure_keeps_cursor_and_ids(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 11)
    before = deepcopy(test.owner())
    cursor, epoch = (test.env.STATE_KV.data[k] for k in (KEYS[0], KEYS[4]))
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    assert [e["id"] for e in json.loads(test.env.STATE_KV.data[KEYS[3]])] == [before["fixtures"][1]["google_event_id"]]
    assert (test.env.STATE_KV.data[KEYS[0]], test.env.STATE_KV.data[KEYS[4]]) == (cursor, epoch)
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    assert test.owner()["fixtures"][1]["discord_event_id"] == before["fixtures"][1]["discord_event_id"]
    assert test.owner()["fixtures"][1]["notion_page_id"] == before["fixtures"][1]["notion_page_id"]
    assert test.call("cleanup")[0] == 200


def test_matrix_three_failures_drain_one_at_a_time(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 13)
    before = deepcopy(test.owner())
    cursor = test.env.STATE_KV.data[KEYS[0]]
    epoch = test.env.STATE_KV.data[KEYS[4]]
    for step, remaining in ((14, 3), (15, 2), (16, 1), (17, 0)):
        status, payload = test.call("advance")
        assert status == 200, (step, payload)
        assert test.call("verify")[0] == 200
        assert len(test.owner()["pending_ids"]) == remaining
        if step == 14:
            assert test.env.STATE_KV.data[KEYS[0]] == cursor
            assert test.env.STATE_KV.data[KEYS[4]] == epoch
        else:
            assert test.owner()["stages"][f"google_matrix_queue_drain_{step}"] == 200
        assert [(s.get("notion_page_id"), s.get("discord_event_id")) for s in test.owner()["fixtures"]] == [
            (s.get("notion_page_id"), s.get("discord_event_id")) for s in before["fixtures"]
        ]
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


@pytest.mark.parametrize(("step", "slot_index", "kind"), [(24, 5, "notion"), (26, 6, "delete")])
def test_matrix_new_failures_preserve_cursor_and_retry_without_creation(monkeypatch, step, slot_index, kind):
    test = MatrixScenario(monkeypatch)
    run_to(test, step - 1)
    before = deepcopy(test.owner())
    cursor, epoch = (test.env.STATE_KV.data[k] for k in (KEYS[0], KEYS[4]))
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    owner = test.owner()
    slot = owner["fixtures"][slot_index]
    assert owner["pending_ids"] == [slot["google_event_id"]]
    assert owner["stages"][f"google_matrix_{kind}_failure_injected"] == 200
    assert test.env.STATE_KV.data[KEYS[0]] == cursor
    assert test.env.STATE_KV.data[KEYS[4]] == epoch
    if kind == "delete":
        assert test.pages[slot["notion_page_id"]]["archived"] is True
        assert slot["discord_event_id"] in test.discord
    else:
        assert probe._property_text(test.pages[slot["notion_page_id"]], "内容", "rich_text") == slot["previous_description"]
        assert slot["source"]["description"] in test.discord[slot["discord_event_id"]]["description"]
    test.calls.clear()
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    assert test.owner()["pending_ids"] == []
    assert ("discord", "POST") not in test.calls
    assert len(test.pages) == 7  # NotionのPOST照会は許すが、新規ページは作らない。
    assert [(s.get("notion_page_id"), s.get("discord_event_id")) for s in test.owner()["fixtures"]] == [
        (s.get("notion_page_id"), s.get("discord_event_id")) for s in before["fixtures"]
    ]
    assert test.call("cleanup")[0] == 200


def test_matrix_seven_items_cross_pages_and_drain_in_order(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 19)
    original = google_calendar_sync.fetch
    page_tokens = []

    async def record(url, options):
        params = parse_qs(urlparse(url).query)
        assert params["maxResults"] == ["2"]
        assert "updatedMin" not in params
        page_tokens.append(params.get("pageToken"))
        return await original(url, options)

    monkeypatch.setattr(google_calendar_sync, "fetch", record)
    assert test.call("advance")[0] == 200
    assert page_tokens == [None, ["2"], ["4"], ["6"]]
    monkeypatch.setattr(google_calendar_sync, "fetch", original)
    assert test.call("verify")[0] == 200
    assert test.owner()["stages"]["google_matrix_seven_inputs"] == 200
    pending = test.owner()["pending_ids"]
    assert len(pending) == 5
    for remaining in (3, 1, 0):
        assert test.call("advance")[0] == 200
        assert test.call("verify")[0] == 200
        pending = pending[2:]
        assert test.owner()["pending_ids"] == pending and len(pending) == remaining
    assert test.call("cleanup")[0] == 200


@pytest.mark.parametrize("failure", ["second_page", "missing", "duplicate"])
def test_matrix_pagination_failure_does_not_apply_or_advance_cursor(monkeypatch, failure):
    test = MatrixScenario(monkeypatch)
    run_to(test, 19)
    before = dict(test.env.STATE_KV.data)
    original = google_calendar_sync.fetch

    async def broken(url, options):
        params = parse_qs(urlparse(url).query)
        response = await original(url, options)
        if params.get("pageToken") != ["2"]:
            return response
        if failure == "second_page":
            return Response("{}", status=503)
        data = json.loads(await response.text())
        if failure == "missing":
            data["items"].pop()
        else:
            data["items"].append(data["items"][0])
        return Response(json.dumps(data))

    monkeypatch.setattr(google_calendar_sync, "fetch", broken)
    test.calls.clear()
    assert test.call("advance")[0] == 409
    assert test.env.STATE_KV.data == before
    assert not any(method != "GET" for _, method in test.calls)
    assert test.call("cleanup")[0] == 200


def test_matrix_extended_requirement_cannot_be_downgraded(monkeypatch):
    test = MatrixScenario(monkeypatch)
    run_to(test, 0)
    owner = test.owner()
    owner.pop("matrix_extended")
    owner["fixtures"] = owner["fixtures"][:5]
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest("google_sync", owner))


def test_matrix_legacy_five_item_run_remains_recoverable(monkeypatch):
    original = matrix._new_owner

    def legacy(*args):
        owner = original(*args)
        owner.pop("matrix_extended")
        owner["fixtures"] = owner["fixtures"][:5]
        return owner

    monkeypatch.setattr(matrix, "_new_owner", legacy)
    test = MatrixScenario(monkeypatch)
    run_to(test, 17)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"


@pytest.mark.parametrize("failure", ["accepted", "unavailable"])
def test_matrix_multi_rejection_requires_actual_400(monkeypatch, failure):
    test = MatrixScenario(monkeypatch)
    run_to(test, 13)
    original = matrix.discord_request

    async def changed_response(env, stages, retries, stage, method, path, payload=None):
        if stage.startswith("google_matrix_rejection_") and stage[-1].isdigit():
            return (200, {}) if failure == "accepted" else (503, {})
        return await original(env, stages, retries, stage, method, path, payload)

    monkeypatch.setattr(matrix, "discord_request", changed_response)
    assert test.call("advance")[0] == 409
    monkeypatch.setattr(matrix, "discord_request", original)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


@pytest.mark.parametrize("target", ["parent_create", "instance_marker"])
def test_matrix_recovers_after_series_write_response_loss(monkeypatch, target):
    test = MatrixScenario(monkeypatch)
    run_to(test, 3)
    original = matrix._google_request

    async def lose_response(method, url, token, payload=None):
        result = await original(method, url, token, payload)
        if ((target == "parent_create" and method == "POST")
                or (target == "instance_marker" and method == "PATCH")):
            raise RuntimeError("simulated response loss")
        return result

    monkeypatch.setattr(matrix, "_google_request", lose_response)
    assert test.call("advance")[0] == 409
    monkeypatch.setattr(matrix, "_google_request", original)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"
    assert all(e.get("status") == "cancelled" for e in test.google.values())
