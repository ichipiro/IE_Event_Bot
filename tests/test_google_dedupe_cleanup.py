"""通知の巡回掃除で期限切れだけを回収し、所有権と処理中leaseを守る。"""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

import sync_lock_do
from sync_lock_do import SyncCoordinator
from tests.fakes import make_durable_object


def mark(coordinator, number="new"):
    return asyncio.run(coordinator._handle_action({
        "action": "mark_google_message_seen", "channel_id": "z-channel",
        "message_number": number, "ttl_seconds": 60,
    }))


def test_new_notification_removes_only_expired_unowned_markers(monkeypatch):
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    coordinator = make_durable_object(SyncCoordinator())
    storage = coordinator.ctx.storage
    protected = {
        "gcal_msg:active:1": {"expires_at": 1001},
        "gcal_msg:processing:1": {"expires_at": 1001, "status": "processing", "claim_owner": "owner"},
        "gcal_msg:e2e:1": {"expires_at": 999, "owner_run_id": "E2E-20260902T100000Z-1234abcd"},
        "e2e:manifest:google": {"expires_at": 999, "dirty": True},
        "other:marker": {"expires_at": 999},
    }
    storage.data.update({key: json.dumps(value) for key, value in protected.items()})
    storage.data["gcal_msg:expired:1"] = json.dumps({"expires_at": 999})
    storage.data["gcal_msg:abandoned:1"] = json.dumps({
        "expires_at": 999, "status": "processing", "claim_owner": "old-owner",
    })
    asyncio.run(storage.setAlarm(123456))

    assert mark(coordinator)[1] == 200

    assert "gcal_msg:expired:1" not in storage.data
    assert "gcal_msg:abandoned:1" not in storage.data
    assert {key: json.loads(storage.data[key]) for key in protected} == protected
    assert storage.alarm_at == 123456


def test_cleanup_pages_over_100_markers_and_wraps_to_earlier_keys(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: clock[0])
    coordinator = make_durable_object(SyncCoordinator())
    storage = coordinator.ctx.storage
    storage.data["gcal_msg:a-active:1"] = json.dumps({"expires_at": 2000})
    expired = {f"gcal_msg:m-expired:{i:03d}" for i in range(205)}
    storage.data.update({key: json.dumps({"expires_at": 999}) for key in expired})
    original_list = storage.list
    options_seen = []

    async def bounded_list(options):
        options_seen.append(options)
        return await original_list(options)

    monkeypatch.setattr(storage, "list", bounded_list)
    previous = len(expired)
    for number in range(3):
        assert mark(coordinator, str(number))[1] == 200
        remaining = len(expired & storage.data.keys())
        assert 0 < previous - remaining <= 100
        previous = remaining
    assert not expired & storage.data.keys()
    assert len(options_seen) == 3
    assert all(options["prefix"] == "gcal_msg:" and options["limit"] <= 100 for options in options_seen)
    clock[0] = 2001.0
    assert mark(coordinator, "next-cycle")[1] == 200
    assert "gcal_msg:a-active:1" not in storage.data


@pytest.mark.parametrize("replacement", [
    {"expires_at": 1100, "status": "processing", "claim_owner": "new-owner"},
    {"expires_at": 999, "owner_run_id": "E2E-20260902T100000Z-1234abcd"},
])
def test_cleanup_rechecks_latest_record_before_deleting(monkeypatch, replacement):
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    coordinator = make_durable_object(SyncCoordinator())
    storage = coordinator.ctx.storage
    key = "gcal_msg:replaced:1"
    storage.data[key] = json.dumps({"expires_at": 999})
    storage.data["gcal_msg:expired:1"] = json.dumps({"expires_at": 999})
    original_list = storage.list

    async def replace_after_list(options):
        listed = await original_list(options)
        storage.data[key] = json.dumps(replacement)
        return listed

    monkeypatch.setattr(storage, "list", replace_after_list)

    assert mark(coordinator)[1] == 200

    assert "gcal_msg:expired:1" not in storage.data
    assert json.loads(storage.data[key]) == replacement
    if replacement.get("status") == "processing":
        result, status = asyncio.run(coordinator._handle_action({
            "action": "finish_google_message", "channel_id": "replaced", "message_number": "1",
            "claim_owner": "new-owner", "succeeded": True, "ttl_seconds": 60,
        }))
        assert status == 200 and result["ok"]


def test_cleanup_preserves_records_without_a_valid_expiration(monkeypatch):
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    coordinator = make_durable_object(SyncCoordinator())
    storage = coordinator.ctx.storage
    malformed = ["bad json", "[]", "null", {}, {"expires_at": "invalid"},
                 {"expires_at": True}, {"expires_at": 0}, {"expires_at": -1},
                 {"expires_at": float("nan")}, {"expires_at": float("inf")},
                 {"expires_at": 999, "status": "unknown"}]
    protected = {f"gcal_msg:invalid:{i}": value if isinstance(value, str) else json.dumps(value)
                 for i, value in enumerate(malformed)}
    storage.data.update(protected)
    storage.data["gcal_msg:expired:1"] = json.dumps({"expires_at": 999})

    assert mark(coordinator)[1] == 200

    assert "gcal_msg:expired:1" not in storage.data
    assert {key: storage.data[key] for key in protected} == protected


def test_cleanup_does_not_read_or_delete_another_durable_object(monkeypatch):
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    coordinator = make_durable_object(SyncCoordinator())
    other = make_durable_object(SyncCoordinator())
    key = "gcal_msg:expired:1"
    for obj in (coordinator, other):
        obj.ctx.storage.data[key] = json.dumps({"expires_at": 999})

    assert mark(coordinator)[1] == 200

    assert key not in coordinator.ctx.storage.data
    assert key in other.ctx.storage.data


def test_cleanup_stops_at_time_budget_and_resumes_after_restart(monkeypatch):
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    ticks = iter(i / 10 for i in range(20))
    monkeypatch.setattr(sync_lock_do, "monotonic", lambda: next(ticks))
    coordinator = make_durable_object(SyncCoordinator())
    storage = coordinator.ctx.storage
    expired = [f"gcal_msg:expired:{i}" for i in range(3)]
    storage.data.update({key: json.dumps({"expires_at": 999}) for key in expired})

    for index, key in enumerate(expired):
        restarted = make_durable_object(SyncCoordinator())
        restarted.ctx = coordinator.ctx
        assert mark(restarted, str(index))[1] == 200
        assert key not in storage.data
        assert all(later in storage.data for later in expired[index + 1:])


def test_cleanup_converts_js_options_and_map(monkeypatch):
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    coordinator = make_durable_object(SyncCoordinator())
    storage = coordinator.ctx.storage
    key = "gcal_msg:expired:1"
    storage.data[key] = json.dumps({"expires_at": 999})
    original_list = storage.list

    def parse_options(text):
        return SimpleNamespace(values=json.loads(text))

    async def native_list(options):
        assert type(options) is SimpleNamespace
        entries = await original_list(options.values)
        return SimpleNamespace(to_py=lambda: entries)

    monkeypatch.setitem(sys.modules, "js", SimpleNamespace(JSON=SimpleNamespace(parse=parse_options)))
    monkeypatch.setattr(storage, "list", native_list)

    assert mark(coordinator)[1] == 200

    assert key not in storage.data
