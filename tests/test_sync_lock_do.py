"""SyncCoordinator Durable Object の排他と重複抑止を検証する。"""

import asyncio
import json

import sync_lock_do
from sync_lock_do import SyncCoordinator
from tests.fakes import Request, make_durable_object


def run(coroutine):
    return asyncio.run(coroutine)


def post(coordinator: SyncCoordinator, payload: dict):
    return run(
        coordinator.fetch(
            Request(
                "https://sync-lock/internal",
                method="POST",
                body=json.dumps(payload),
            )
        )
    )


def response_json(response) -> dict:
    return json.loads(run(response.text()))


def test_lock_rejects_other_owner_until_release(monkeypatch) -> None:
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: 1000.0)
    coordinator = make_durable_object(SyncCoordinator())

    acquired = post(
        coordinator,
        {"action": "acquire", "owner": "owner-a", "ttl_seconds": 30},
    )
    conflict = post(
        coordinator,
        {"action": "acquire", "owner": "owner-b", "ttl_seconds": 30},
    )
    post(coordinator, {"action": "release", "owner": "owner-a"})
    reacquired = post(
        coordinator,
        {"action": "acquire", "owner": "owner-b", "ttl_seconds": 30},
    )

    assert acquired.status == 200
    assert conflict.status == 409
    assert response_json(conflict) == {
        "ok": False,
        "locked": True,
        "owner": "owner-a",
    }
    assert reacquired.status == 200
    assert response_json(reacquired)["owner"] == "owner-b"


def test_google_message_dedupe_expires(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: now[0])
    coordinator = make_durable_object(SyncCoordinator())
    payload = {
        "action": "mark_google_message_seen",
        "channel_id": "channel",
        "message_number": "100",
        "ttl_seconds": 60,
    }

    first = response_json(post(coordinator, payload))
    now[0] = 1001.0
    duplicate = response_json(post(coordinator, payload))
    now[0] = 1061.0
    after_expiration = response_json(post(coordinator, payload))

    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert after_expiration["duplicate"] is False


def test_e2e_google_message_dedupe_requires_matching_owner_to_clear() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    run_id = "E2E-20260902T100000Z-1234abcd"
    other_run_id = "E2E-20260902T100001Z-deadbeef"
    marker = {
        "action": "mark_google_message_seen",
        "channel_id": "e2e-webhook-channel",
        "message_number": "100",
        "ttl_seconds": 60,
        "owner_run_id": run_id,
    }

    first = post(coordinator, marker)
    duplicate = post(coordinator, marker)
    wrong_owner_mark = post(coordinator, {**marker, "owner_run_id": other_run_id})
    wrong_owner = post(
        coordinator,
        {
            "action": "clear_e2e_google_message_seen",
            "channel_id": "e2e-webhook-channel",
            "message_number": "100",
            "owner_run_id": other_run_id,
        },
    )
    cleared = post(
        coordinator,
        {
            "action": "clear_e2e_google_message_seen",
            "channel_id": "e2e-webhook-channel",
            "message_number": "100",
            "owner_run_id": run_id,
        },
    )
    after_clear = post(coordinator, marker)

    assert first.status == 200
    assert response_json(first)["duplicate"] is False
    assert response_json(duplicate)["duplicate"] is True
    assert wrong_owner_mark.status == 409
    assert response_json(wrong_owner_mark) == {
        "ok": False,
        "error": "google_message_owner_mismatch",
    }
    assert wrong_owner.status == 409
    assert response_json(wrong_owner) == {
        "ok": False,
        "error": "google_message_owner_mismatch",
    }
    assert cleared.status == 200
    assert response_json(cleared) == {"ok": True, "cleared": True}
    assert response_json(after_clear)["duplicate"] is False


def test_e2e_manifest_round_trip_uses_service_scoped_storage() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "google_calendar_event",
        "dirty": True,
        "run_id": "E2E-20260901T000000Z-1234abcd",
        "event_id": "temporary-resource-id",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "google",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "google"},
    )

    assert stored.status == 200
    assert response_json(stored) == {"ok": True}
    assert loaded.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}
    assert "e2e:manifest:google" in coordinator.ctx.storage.data


def test_e2e_manifest_rejects_invalid_service_kind_and_size() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    valid_manifest = {
        "version": 1,
        "kind": "google_calendar_event",
        "dirty": False,
        "last_run_id": "E2E-20260901T000000Z-1234abcd",
    }

    invalid_service = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "unknown",
            "manifest_json": json.dumps(valid_manifest),
        },
    )
    invalid_kind = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "google",
            "manifest_json": json.dumps({**valid_manifest, "kind": "notion_pages"}),
        },
    )
    oversized = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "google",
            "manifest_json": json.dumps({**valid_manifest, "padding": "x" * 33_000}),
        },
    )

    assert invalid_service.status == 400
    assert response_json(invalid_service) == {
        "ok": False,
        "error": "invalid_e2e_manifest_service",
    }
    assert invalid_kind.status == 400
    assert response_json(invalid_kind) == {
        "ok": False,
        "error": "invalid_e2e_manifest_kind",
    }
    assert oversized.status == 413
    assert response_json(oversized) == {
        "ok": False,
        "error": "e2e_manifest_too_large",
    }


def test_google_discord_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "google_discord_sync",
        "dirty": False,
        "last_run_id": "E2E-20260901T000000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "google_discord",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "google_discord"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def test_discord_notion_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "discord_notion_sync",
        "dirty": False,
        "last_run_id": "E2E-20260902T000000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "discord_notion",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "discord_notion"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def test_discord_google_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "discord_google_sync",
        "dirty": False,
        "last_run_id": "E2E-20260902T040000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "discord_google",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "discord_google"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def test_qa_notification_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "qa_notification_job",
        "dirty": False,
        "last_run_id": "E2E-20260902T060000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "qa_notification",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "qa_notification"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def test_reminder_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "day_before_reminder",
        "dirty": False,
        "last_run_id": "E2E-20260902T070000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "reminder",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "reminder"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def test_notion_cleanup_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "notion_cleanup_job",
        "dirty": False,
        "last_run_id": "E2E-20260902T080000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "notion_cleanup",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "notion_cleanup"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def test_webhook_dispatch_manifest_uses_dedicated_service_kind() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = {
        "version": 1,
        "kind": "google_webhook_simulation",
        "dirty": False,
        "last_run_id": "E2E-20260902T090000Z-1234abcd",
    }

    stored = post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "webhook_dispatch",
            "manifest_json": json.dumps(manifest),
        },
    )
    loaded = post(
        coordinator,
        {"action": "get_e2e_manifest", "service": "webhook_dispatch"},
    )

    assert stored.status == 200
    assert response_json(loaded) == {"ok": True, "manifest": manifest}


def _webhook_delivery_manifest() -> dict:
    return {
        "version": 1,
        "kind": "google_webhook_delivery",
        "dirty": True,
        "run_id": "E2E-20260902T110000Z-1234abcd",
        "channel_id": "e2e-webhook-owned-channel",
        "stage": "watch_create_started",
        "stages": {},
    }


def test_webhook_delivery_accepts_notification_before_watch_response() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = _webhook_delivery_manifest()
    assert post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "webhook_delivery",
            "manifest_json": json.dumps(manifest),
        },
    ).status == 200

    recorded = post(
        coordinator,
        {
            "action": "record_e2e_webhook_delivery",
            "channel_id": manifest["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "sync",
            "message_number": "1",
        },
    )
    attached = post(
        coordinator,
        {
            "action": "attach_e2e_webhook_watch",
            "run_id": manifest["run_id"],
            "channel_id": manifest["channel_id"],
            "resource_id": "google-resource-id",
            "expiration": "1790000000000",
            "watch_status": 200,
        },
    )
    loaded = response_json(
        post(
            coordinator,
            {"action": "get_e2e_manifest", "service": "webhook_delivery"},
        )
    )["manifest"]

    assert response_json(recorded) == {
        "ok": True,
        "accepted": True,
        "duplicate": False,
    }
    assert response_json(attached) == {
        "ok": True,
        "notification_received": True,
    }
    assert loaded["resource_id"] == "google-resource-id"
    assert loaded["notification"]["resource_state"] == "sync"
    assert loaded["notification"]["message_number"] == "1"
    assert loaded["stages"] == {
        "watch_create": 200,
        "webhook_sync_delivery": 204,
    }


def test_webhook_delivery_accepts_watch_response_before_notification() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = _webhook_delivery_manifest()
    post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "webhook_delivery",
            "manifest_json": json.dumps(manifest),
        },
    )

    attached = post(
        coordinator,
        {
            "action": "attach_e2e_webhook_watch",
            "run_id": manifest["run_id"],
            "channel_id": manifest["channel_id"],
            "resource_id": "google-resource-id",
            "expiration": "1790000000000",
            "watch_status": 200,
        },
    )
    recorded = post(
        coordinator,
        {
            "action": "record_e2e_webhook_delivery",
            "channel_id": manifest["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "sync",
            "message_number": "1",
        },
    )
    duplicate = post(
        coordinator,
        {
            "action": "record_e2e_webhook_delivery",
            "channel_id": manifest["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "sync",
            "message_number": "1",
        },
    )

    assert response_json(attached) == {
        "ok": True,
        "notification_received": False,
    }
    assert response_json(recorded)["duplicate"] is False
    assert response_json(duplicate) == {
        "ok": True,
        "accepted": True,
        "duplicate": True,
    }


def test_webhook_delivery_rejects_unowned_or_non_initial_notification() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = _webhook_delivery_manifest()
    post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "webhook_delivery",
            "manifest_json": json.dumps(manifest),
        },
    )

    wrong_channel = post(
        coordinator,
        {
            "action": "record_e2e_webhook_delivery",
            "channel_id": "other-channel",
            "resource_id": "google-resource-id",
            "resource_state": "sync",
            "message_number": "1",
        },
    )
    wrong_state = post(
        coordinator,
        {
            "action": "record_e2e_webhook_delivery",
            "channel_id": manifest["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "exists",
            "message_number": "2",
        },
    )

    assert wrong_channel.status == 404
    assert response_json(wrong_channel)["error"] == (
        "e2e_webhook_delivery_target_mismatch"
    )
    assert wrong_state.status == 404
    assert response_json(wrong_state)["error"] == (
        "e2e_webhook_delivery_notification_mismatch"
    )


def _webhook_change_manifest() -> dict:
    return {
        "version": 1,
        "kind": "google_webhook_change_dispatch",
        "dirty": True,
        "run_id": "E2E-20260902T120000Z-1234abcd",
        "google_event_id": "owned-event",
        "watch": {"channel_id": "e2e-change-owned-channel"},
        "create_attempted": {
            "google_event": True,
            "notion_page": True,
            "watch_channel": True,
        },
        "stage": "watch_create_started",
        "stages": {},
    }


def test_webhook_change_claims_only_first_owned_exists_notification() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = _webhook_change_manifest()
    assert post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "webhook_change",
            "manifest_json": json.dumps(manifest),
        },
    ).status == 200

    sync = post(
        coordinator,
        {
            "action": "record_e2e_webhook_change_sync",
            "channel_id": manifest["watch"]["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "sync",
            "message_number": "1",
        },
    )
    attached = post(
        coordinator,
        {
            "action": "attach_e2e_webhook_change_watch",
            "run_id": manifest["run_id"],
            "channel_id": manifest["watch"]["channel_id"],
            "resource_id": "google-resource-id",
            "expiration": "1790000000000",
            "watch_status": 200,
        },
    )
    prepared = post(
        coordinator,
        {
            "action": "prepare_e2e_webhook_change",
            "run_id": manifest["run_id"],
            "updated_min": "2026-09-02T11:59:00+00:00",
        },
    )
    claimed = post(
        coordinator,
        {
            "action": "claim_e2e_webhook_change",
            "channel_id": manifest["watch"]["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "exists",
            "message_number": "2",
        },
    )
    duplicate = post(
        coordinator,
        {
            "action": "claim_e2e_webhook_change",
            "channel_id": manifest["watch"]["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "exists",
            "message_number": "7",
        },
    )
    completed = post(
        coordinator,
        {
            "action": "complete_e2e_webhook_change",
            "run_id": manifest["run_id"],
            "channel_id": manifest["watch"]["channel_id"],
            "resource_id": "google-resource-id",
            "message_number": "2",
            "dispatch_status": 204,
            "selected_count": 1,
            "dedupe_calls": 1,
            "apply_ok": True,
            "processed": 1,
            "pending_events": 0,
            "error_count": 0,
            "notion_write_started": True,
            "cursor_written": True,
            "last_epoch_written": True,
            "last_result_written": True,
        },
    )
    loaded = response_json(
        post(
            coordinator,
            {"action": "get_e2e_manifest", "service": "webhook_change"},
        )
    )["manifest"]

    assert response_json(sync)["duplicate"] is False
    assert response_json(attached)["notification_received"] is True
    assert response_json(prepared) == {"ok": True}
    assert response_json(claimed) == {
        "ok": True,
        "accepted": True,
        "duplicate": False,
    }
    assert response_json(duplicate) == {
        "ok": True,
        "accepted": True,
        "duplicate": True,
    }
    assert response_json(completed) == {"ok": True}
    assert loaded["change_notification"]["message_number"] == "2"
    assert loaded["dispatch"] == {
        "status": 204,
        "selected_count": 1,
        "dedupe_calls": 1,
        "apply_ok": True,
        "processed": 1,
        "pending_events": 0,
        "error_count": 0,
        "notion_write_started": True,
        "cursor_written": True,
        "last_epoch_written": True,
        "last_result_written": True,
    }


def test_webhook_change_rejects_unprepared_or_unowned_exists_notification() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    manifest = _webhook_change_manifest()
    post(
        coordinator,
        {
            "action": "put_e2e_manifest",
            "service": "webhook_change",
            "manifest_json": json.dumps(manifest),
        },
    )

    unprepared = post(
        coordinator,
        {
            "action": "claim_e2e_webhook_change",
            "channel_id": manifest["watch"]["channel_id"],
            "resource_id": "google-resource-id",
            "resource_state": "exists",
            "message_number": "2",
        },
    )
    wrong_channel = post(
        coordinator,
        {
            "action": "record_e2e_webhook_change_sync",
            "channel_id": "not-owned",
            "resource_id": "google-resource-id",
            "resource_state": "sync",
            "message_number": "1",
        },
    )

    assert unprepared.status == 404
    assert response_json(unprepared)["error"] == (
        "e2e_webhook_change_notification_mismatch"
    )
    assert wrong_channel.status == 404
    assert response_json(wrong_channel)["error"] == (
        "e2e_webhook_change_target_mismatch"
    )
