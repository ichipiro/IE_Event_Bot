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
