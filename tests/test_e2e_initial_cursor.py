"""古い削除履歴を含むCalendarで、実時刻からのE2E準備と所有境界を検証する。"""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from workers import Response

import google_calendar_sync
from e2e import e2e_all_sync_probe as all_sync
from e2e import e2e_google_sync_probe as google
from e2e.e2e_google_sync_state import GoogleKV, digest
from tests.test_e2e_all_http import normal
from tests.test_e2e_all_sync import AllScenario
from tests.test_e2e_watch_shared import WatchScenario


NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
CURSOR = NOW.isoformat().replace("+00:00", "Z")
OVERLAP = (NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


def window_scenario(monkeypatch, mode="all", old_count=150):
    test = WatchScenario(monkeypatch) if mode == "watch" else AllScenario(monkeypatch)
    test.env.E2E_ALL_HTTP_ENABLED = "true"
    monkeypatch.setattr(all_sync, "datetime", FrozenDateTime, raising=False)
    old = {f"old-{index}": {"id": f"old-{index}", "status": "cancelled",
                           "updated": "2025-01-01T00:00:00Z"} for index in range(old_count)}
    test.google.update(deepcopy(old))
    requested = []

    async def calendar_fetch(url, options):
        params = parse_qs(urlparse(url).query)
        requested.append(params)
        cutoff = params.get("updatedMin", [None])[0]
        items = list(test.google.values())
        if cutoff:
            start = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
            items = [event for event in items
                     if datetime.fromisoformat(event["updated"].replace("Z", "+00:00")) > start]
        return Response(json.dumps({"items": items}))

    request = google._google_request

    async def timestamp_writes(method, url, token, payload=None):
        status, result = await request(method, url, token, payload)
        if method in ("POST", "PATCH") and status == 200:
            event_id = result["id"]
            test.google[event_id]["updated"] = CURSOR
            result["updated"] = CURSOR
        return status, result

    monkeypatch.setattr(google_calendar_sync, "fetch", calendar_fetch)
    monkeypatch.setattr(google, "_google_request", timestamp_writes)
    return test, old, requested


@pytest.mark.parametrize("mode", ["all", "http", "watch"])
def test_prepare_and_dispatch_use_initial_window_with_many_old_tombstones(monkeypatch, mode):
    test, old, requested = window_scenario(monkeypatch, mode)
    before = deepcopy(test.env.STATE_KV.data)
    recent = {"id": "recent-deleted", "status": "cancelled",
              "updated": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")}
    test.google[recent["id"]] = deepcopy(recent)

    status, payload = test.call(mode)

    assert status == 200, payload
    owner = test.owner()
    assert owner["initial_cursor"] == CURSOR
    assert len(owner["baseline_deleted"]) == 1
    kv = GoogleKV(test.store, owner)
    assert test.env.STATE_KV.data[kv.prefix + "sync:updated_min"] == CURSOR
    assert owner["hashes"]["sync:updated_min"] == digest(CURSOR)
    assert requested[0]["updatedMin"] == [OVERLAP]
    assert test.call("verify")[0] == 200
    if mode == "all":
        assert test.call("advance")[0] == 200
        assert test.call("verify")[0] == 200
    elif mode == "http":
        status, payload = normal(test)
        assert status == 200, payload
        assert test.call("verify")[0] == 200
    else:
        assert isinstance(test, WatchScenario)
        test.deliver(1)
    assert all(params.get("updatedMin") == [OVERLAP] for params in requested)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"
    assert test.env.STATE_KV.data == before
    assert {key: test.google[key] for key in old} == old
    assert test.google[recent["id"]] == recent


def test_old_active_event_is_rejected_before_fixture_or_manifest(monkeypatch):
    test, _, _ = window_scenario(monkeypatch)
    test.google["foreign"] = {"id": "foreign", "status": "confirmed", "updated": "2024-01-01T00:00:00Z"}
    before = deepcopy((test.google, test.env.STATE_KV.data))

    status, payload = test.call("all")

    assert status == 409 and payload["error"] == "google_sync_calendar_not_empty"
    assert (test.google, test.env.STATE_KV.data) == before
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


def test_recent_tombstone_limit_is_not_raised(monkeypatch):
    test, _, _ = window_scenario(monkeypatch, old_count=0)
    test.google.update({str(i): {"id": str(i), "status": "cancelled", "updated": CURSOR} for i in range(101)})
    before = deepcopy((test.google, test.env.STATE_KV.data))

    status, payload = test.call("all")

    assert status == 409 and payload["error"] == "google_sync_baseline_limit"
    assert (test.google, test.env.STATE_KV.data) == before
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


def test_full_mode_still_checks_complete_deleted_history(monkeypatch):
    test, _, requested = window_scenario(monkeypatch)

    status, payload = test.call("full")

    assert status == 409 and payload["error"] == "google_sync_baseline_limit"
    assert "updatedMin" not in requested[0]


@pytest.mark.parametrize("value", [None, "invalid", "2026-11-09T12:00:01Z"])
def test_initial_cursor_cannot_change_after_prepare(monkeypatch, value):
    test, _, _ = window_scenario(monkeypatch)
    status, payload = test.call("all")
    assert status == 200, payload
    owner = test.owner()
    owner["initial_cursor"] = value
    with pytest.raises(RuntimeError, match="write_failed"):
        asyncio.run(test.store.put_e2e_manifest("google_sync", owner))
    assert test.call("cleanup")[0] == 200


@pytest.mark.parametrize("payload", [{}, {"items": None}, {"items": [{}]}, {"items": [], "nextPageToken": 7}])
def test_invalid_active_listing_is_not_treated_as_empty(monkeypatch, payload):
    test, _, _ = window_scenario(monkeypatch)
    request = google._google_request

    async def invalid(method, url, token, body=None):
        if "showDeleted=false" in url:
            return 200, payload
        return await request(method, url, token, body)

    monkeypatch.setattr(google, "_google_request", invalid)
    assert test.call("all")[0] == 409
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


@pytest.mark.parametrize("second", [
    {"items": [{"status": "confirmed"}]},
    {"items": [], "nextPageToken": "next"},
])
def test_active_listing_checks_following_page_and_rejects_token_loop(monkeypatch, second):
    test, _, _ = window_scenario(monkeypatch)
    request = google._google_request
    pages = []

    async def paged(method, url, token, body=None):
        if "showDeleted=false" in url:
            params = parse_qs(urlparse(url).query)
            assert "updatedMin" not in params
            pages.append(params)
            return 200, second if "pageToken" in params else {"items": [], "nextPageToken": "next"}
        return await request(method, url, token, body)

    monkeypatch.setattr(google, "_google_request", paged)
    assert test.call("all")[0] == 409
    assert len(pages) == 2
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


def test_active_listing_http_failure_stops_before_cursor_seed(monkeypatch):
    test, _, _ = window_scenario(monkeypatch)
    request = google._google_request
    before = deepcopy(test.env.STATE_KV.data)

    async def failed(method, url, token, body=None):
        if "showDeleted=false" in url:
            return 503, {}
        return await request(method, url, token, body)

    monkeypatch.setattr(google, "_google_request", failed)
    assert test.call("all")[0] == 409
    assert test.env.STATE_KV.data == before
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


def test_change_during_active_check_is_checked_in_initial_delta(monkeypatch):
    test, _, requested = window_scenario(monkeypatch)
    request = google._google_request

    async def changed(method, url, token, body=None):
        result = await request(method, url, token, body)
        if "showDeleted=false" in url:
            test.google["foreign"] = {"id": "foreign", "status": "confirmed", "updated": CURSOR}
        return result

    monkeypatch.setattr(google, "_google_request", changed)
    status, payload = test.call("all")
    assert status == 409 and payload["error"] == "google_sync_calendar_not_empty"
    assert requested[0]["updatedMin"] == [OVERLAP]
    assert not asyncio.run(test.store.get_e2e_manifest("google_sync"))


@pytest.mark.parametrize("mode", ["all", "http"])
def test_initial_cursor_write_response_loss_keeps_cleanup_ownership(monkeypatch, mode):
    test, old, _ = window_scenario(monkeypatch, mode)
    before = deepcopy(test.env.STATE_KV.data)
    put = test.env.STATE_KV.put

    async def lost(key, value, **kwargs):
        await put(key, value, **kwargs)
        if key.endswith("sync:updated_min"):
            raise RuntimeError("test cursor response lost")

    monkeypatch.setattr(test.env.STATE_KV, "put", lost)
    assert test.call(mode)[0] == 409
    owner = test.owner()
    assert owner["dirty"] and owner["initial_cursor"] == CURSOR
    assert all(not slot["source_attempted"] for slot in owner["fixtures"])
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"
    assert test.env.STATE_KV.data == before
    assert test.google == old


def test_legacy_manifest_without_initial_cursor_can_be_cleaned(monkeypatch):
    test, _, _ = window_scenario(monkeypatch)
    assert test.call("all")[0] == 200
    owner = test.owner()
    owner.pop("initial_cursor")
    storage = test.env.SYNC_COORDINATOR.getByName("global").durable_object.ctx.storage
    storage.data["e2e:manifest:google_sync"] = json.dumps(owner)

    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"
