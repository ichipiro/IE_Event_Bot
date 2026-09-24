"""共有キーと全取得入力を使う経路。外部APIは代替する。"""

import asyncio
import json
from copy import deepcopy

import pytest
from workers import Response

import google_calendar_sync
from e2e_google_sync_state import GoogleKV, KEYS
from tests.test_e2e_google_sync_probe import Scenario
from tests.test_e2e_sync_lock_probe import RUN, request


def test_full_input_shared_keys_and_cleanup(monkeypatch):
    test = Scenario(monkeypatch)
    before = dict(test.env.STATE_KV.data)
    received = []

    async def reversed_pages(url, options):
        items = list(reversed(list(test.google.values())))
        received.append([event["id"] for event in items])
        return Response(json.dumps({"items": items}))

    monkeypatch.setattr(google_calendar_sync, "fetch", reversed_pages)
    status, payload = test.call("full")
    assert status == 200, payload
    owner = test.owner()
    assert owner["full_apply"] is True
    assert len(owner["fixtures"]) == 3
    assert GoogleKV(test.store, owner).prefix == ""
    assert set(KEYS) <= set(test.env.STATE_KV.data)
    queue = json.loads(test.env.STATE_KV.data[KEYS[3]])
    assert [event["id"] for event in queue] == received[-1][1:]
    assert len(queue) == 2
    for phase in ("verify", "advance", "verify", "advance", "verify", "advance", "verify"):
        status, payload = test.call(phase)
        assert status == 200, payload
    assert len(test.pages) == 3 and len(test.discord) == 2
    assert test.call("advance")[0] == 409
    status, payload = test.call("cleanup")
    assert status == 200, payload
    assert test.owner()["outcome"] == "passed"
    assert test.owner()["stages"]["google_sync_shared_cleanup"] == 200
    assert test.env.STATE_KV.data == before
    assert not test.discord and all(page.get("archived") for page in test.pages.values())


@pytest.mark.parametrize("target", ["kv", "empty_text", "calendar", "tombstone", "guild", "notion"])
def test_full_refuses_existing_data_before_writes(monkeypatch, target):
    test = Scenario(monkeypatch)
    if target in ("kv", "empty_text"):
        test.env.STATE_KV.data[KEYS[0]] = "old-cursor" if target == "kv" else ""
    elif target in ("calendar", "tombstone"):
        test.google["foreign"] = {"id": "foreign", "status": "cancelled" if target == "tombstone" else "confirmed"}
    elif target == "guild":
        test.discord["foreign"] = {"id": "foreign"}
    else:
        test.pages["foreign"] = {"id": "foreign"}
    before = deepcopy((test.google, test.pages, test.discord, test.env.STATE_KV.data))
    status, payload = test.call("full")
    assert status == 409, payload
    assert "not_empty" in payload["error"]
    assert (test.google, test.pages, test.discord, test.env.STATE_KV.data) == before
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


def test_full_does_not_silently_filter_new_foreign_event(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("full")[0] == 200
    assert test.call("verify")[0] == 200
    test.google["foreign"] = {"id": "foreign", "summary": "keep"}
    before = deepcopy((test.pages, test.discord, test.env.STATE_KV.data))
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "google_sync_unowned_source"
    assert (test.pages, test.discord, test.env.STATE_KV.data) == before
    assert test.call("cleanup")[0] == 200
    assert test.google["foreign"]["summary"] == "keep"
    assert test.owner()["outcome"] == "failed_clean"


def test_full_cleanup_preserves_foreign_shared_value(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("full")[0] == 200
    value = test.env.STATE_KV.data[KEYS[2]]
    test.env.STATE_KV.data[KEYS[2]] = '{"foreign":"keep"}'
    before = deepcopy((test.pages, test.discord, test.env.STATE_KV.data))
    status, payload = test.call("cleanup")
    assert status == 409 and payload["error"] == "google_sync_shared_owner_mismatch"
    assert (test.pages, test.discord, test.env.STATE_KV.data) == before
    test.env.STATE_KV.data[KEYS[2]] = value
    assert test.call("cleanup")[0] == 200


def test_full_records_kv_write_intent_before_lost_response(monkeypatch):
    test = Scenario(monkeypatch)
    original = test.env.STATE_KV.put

    async def write_then_fail(key, value):
        await original(key, value)
        if key == KEYS[1]:
            raise RuntimeError("simulated lost response")

    monkeypatch.setattr(test.env.STATE_KV, "put", write_then_fail)
    status, payload = test.call("full")
    assert status == 409, payload
    assert KEYS[1] in test.owner()["shared_writes"]
    assert KEYS[1] in test.env.STATE_KV.data
    assert test.call("cleanup")[0] == 200
    assert not set(KEYS) & set(test.env.STATE_KV.data)
    assert not test.discord


def test_full_mode_and_write_journal_cannot_be_replaced(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("full")[0] == 200
    for changes in ({"full_apply": False}, {"shared_writes": {}}, {"retry_enabled": True}):
        owner = test.owner()
        owner.update(changes)
        with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
            asyncio.run(test.store.put_e2e_manifest("google_sync", owner))


@pytest.mark.parametrize("flag", ["E2E_ORCHESTRATED_WRITES_ENABLED", "CRON_ENABLE_SYNC"])
def test_full_requires_normal_writes_disabled(monkeypatch, flag):
    test = Scenario(monkeypatch)
    setattr(test.env, flag, "true")
    status, payload = test.call("full")
    assert status >= 400 and payload["error"] == "google_sync_full_configuration_invalid"
    assert test.calls == []


def test_full_excludes_other_scenarios_until_cleanup(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("full")[0] == 200
    notion_owner = {"version": 1, "kind": "notion_pages", "dirty": True,
                    "run_id": test.owner()["run_id"]}
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest("notion", notion_owner))
    response, payload = asyncio.run(request(test.env))
    assert response == 409 and payload["error"] == "google_sync_shared_busy"
    assert test.call("cleanup")[0] == 200
    asyncio.run(test.store.put_e2e_manifest("notion", notion_owner))


def test_full_refuses_another_dirty_owner(monkeypatch):
    test = Scenario(monkeypatch)
    asyncio.run(test.store.put_e2e_manifest("notion", {
        "version": 1, "kind": "notion_pages", "dirty": True, "run_id": RUN,
    }))
    assert test.call("full")[0] == 409
    assert not test.google and not test.pages and not test.discord
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


def test_full_retries_cleanup_after_partial_kv_deletion(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("full")[0] == 200
    original = test.env.STATE_KV.delete

    async def fail_delete(key):
        if key == KEYS[2]:
            raise RuntimeError("simulated deletion failure")
        await original(key)

    monkeypatch.setattr(test.env.STATE_KV, "delete", fail_delete)
    assert test.call("cleanup")[0] == 409
    assert KEYS[0] not in test.env.STATE_KV.data
    assert KEYS[2] in test.env.STATE_KV.data
    monkeypatch.setattr(test.env.STATE_KV, "delete", original)
    assert test.call("cleanup")[0] == 200
    assert not set(KEYS) & set(test.env.STATE_KV.data)


def test_full_uses_every_calendar_page(monkeypatch):
    test = Scenario(monkeypatch)
    pages = []

    async def paged(url, options):
        items = list(test.google.values())
        second = "pageToken=second" in url
        pages.append(second)
        if not items:
            return Response('{"items":[]}')
        return Response(json.dumps({"items": items[1:] if second else items[:1],
                                    **({} if second else {"nextPageToken": "second"})}))

    monkeypatch.setattr(google_calendar_sync, "fetch", paged)
    assert test.call("full")[0] == 200
    assert pages == [False, False, True]
    assert len(json.loads(test.env.STATE_KV.data[KEYS[3]])) == 2
    assert test.call("verify")[0] == 200
    assert test.call("advance")[0] == 200
    assert len(test.pages) == len(test.discord) == 3
    assert test.call("cleanup")[0] == 200
