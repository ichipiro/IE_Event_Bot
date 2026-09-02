"""E2E専用MCP境界向けWorker routeを外部通信なしで検証する。"""

import asyncio
import json
from hashlib import sha256
from types import SimpleNamespace

from workers import Response

import e2e_entry
from e2e_discord_probe import (
    cleanup_discord_crud_probe,
)
from e2e_entry import Default
from e2e_google_probe import (
    cleanup_google_calendar_crud_probe,
)
from e2e_notion_probe import cleanup_notion_crud_probe
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


RUN_ID = "E2E-20260901T000000Z-1234abcd"
OTHER_RUN_ID = "E2E-20260901T000001Z-deadbeef"
AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-E2E-Run-ID": RUN_ID,
}


def run(coroutine):
    return asyncio.run(coroutine)


def response_json(response: Response) -> dict:
    return json.loads(run(response.text()))


def make_worker(env) -> Default:
    worker = Default()
    worker.env = env
    return worker


def test_e2e_status_masks_resource_identifiers() -> None:
    kv = MemoryKV(
        {
            "gcal_watch_state": json.dumps(
                {
                    "channel_id": "sensitive-watch-channel-id",
                    "resource_id": "sensitive-watch-resource-id",
                    "webhook_token_sha256": "must-not-be-returned",
                }
            ),
        }
    )
    namespace = make_sync_coordinator_namespace(
        {
            "google": {
                "version": 1,
                "kind": "google_calendar_event",
                "dirty": False,
                "last_run_id": RUN_ID,
                "outcome": "passed",
                "cleanup_attempts": 1,
                "stages": {"create": 200, "delete": 204},
                "resource_fingerprints": {
                    "calendar_id_sha256": "a" * 64,
                    "event_id_sha256": "b" * 64,
                },
            },
            "discord": {
                "version": 1,
                "kind": "discord_event_message",
                "dirty": True,
                "run_id": OTHER_RUN_ID,
                "stage": "cleanup_failed",
                "event_id": "must-not-be-returned",
                "message_id": "must-not-be-returned",
                "stages": {"event_create": 200},
            },
            "qa_notification": {
                "version": 1,
                "kind": "qa_notification_job",
                "dirty": False,
                "last_run_id": RUN_ID,
                "outcome": "passed",
                "cleanup_attempts": 1,
                "stages": {"job_notify": 200, "message_delete": 204},
                "resource_fingerprints": {
                    "notion_page_id_sha256": "c" * 64,
                    "discord_message_id_sha256": "d" * 64,
                },
            },
            "reminder": {
                "version": 1,
                "kind": "day_before_reminder",
                "dirty": False,
                "last_run_id": RUN_ID,
                "outcome": "passed",
                "cleanup_attempts": 1,
                "stages": {"job_notify": 200, "message_delete": 204},
                "resource_fingerprints": {
                    "discord_event_id_sha256": "e" * 64,
                    "discord_message_id_sha256": "f" * 64,
                },
            },
            "notion_cleanup": {
                "version": 1,
                "kind": "notion_cleanup_job",
                "dirty": False,
                "last_run_id": RUN_ID,
                "outcome": "passed",
                "cleanup_attempts": 1,
                "stages": {"job_cleanup": 200, "job_interval_guard": 200},
                "resource_fingerprints": {
                    "notion_event_database_id_sha256": "1" * 64,
                    "due_page_id_sha256": "2" * 64,
                    "future_page_id_sha256": "3" * 64,
                },
            },
            "webhook_dispatch": {
                "version": 1,
                "kind": "google_webhook_simulation",
                "dirty": False,
                "last_run_id": RUN_ID,
                "outcome": "passed",
                "cleanup_attempts": 1,
                "stages": {
                    "webhook_delta_fetch": 200,
                    "webhook_dispatch": 200,
                },
                "resource_fingerprints": {
                    "google_event_id_sha256": "4" * 64,
                    "notion_page_id_sha256": "5" * 64,
                    "webhook_channel_id_sha256": "6" * 64,
                    "webhook_message_number_sha256": "7" * 64,
                },
            },
        }
    )
    worker = make_worker(
        SimpleNamespace(
            INTERNAL_API_TOKEN="test-token",
            STATE_KV=kv,
            SYNC_COORDINATOR=namespace,
            E2E_GOOGLE_CRUD_ENABLED="true",
            E2E_DISCORD_CRUD_ENABLED="true",
            E2E_NOTION_CRUD_ENABLED="true",
            E2E_QA_NOTIFICATION_ENABLED="true",
            E2E_REMINDER_ENABLED="true",
            E2E_NOTION_CLEANUP_ENABLED="true",
            E2E_WEBHOOK_SIMULATION_ENABLED="true",
            GOOGLE_API_BEARER_TOKEN="fake-google-token",
            GOOGLE_CALENDAR_ID="calendar-id",
            NOTION_TOKEN="fake-notion-token",
            NOTION_EVENT_INTERNAL_ID="notion-event-db",
            NOTION_QA_ID="notion-qa-db",
            DISCORD_TOKEN="fake-discord-token",
            DISCORD_GUILD_ID="discord-guild",
            EVENT_CREATE_CHANNEL_ID="discord-event-channel",
            EVENT_CREATE_ROLE_ID="discord-event-role",
            QA_CHANNEL_ID="discord-qa-channel",
            REMINDER_CHANNEL_ID="discord-reminder-channel",
            REMINDER_ROLE_ID="discord-reminder-role",
            CF_VERSION_METADATA=SimpleNamespace(
                id="sensitive-worker-version-id",
                tag=RUN_ID,
                timestamp="2026-09-01T00:00:00.000Z",
            ),
        )
    )

    unauthorized = run(worker.fetch(Request("https://bot.test/admin/e2e/status")))
    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/status",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    payload = response_json(response)

    assert unauthorized.status == 401
    assert response.status == 200
    assert payload["services"]["google"] == {
        "present": True,
        "dirty": False,
        "run_id": RUN_ID,
        "outcome": "passed",
        "stage": None,
        "cleanup_attempts": 1,
        "stages": {"create": 200, "delete": 204},
        "resource_fingerprints": {
            "calendar_id_sha256": "a" * 64,
            "event_id_sha256": "b" * 64,
        },
    }
    assert payload["services"]["discord"]["dirty"] is True
    assert payload["services"]["notion"] == {
        "present": False,
        "dirty": False,
        "run_id": None,
    }
    assert payload["scenarios"]["qa_notification"] == {
        "present": True,
        "dirty": False,
        "run_id": RUN_ID,
        "outcome": "passed",
        "stage": None,
        "cleanup_attempts": 1,
        "stages": {"job_notify": 200, "message_delete": 204},
        "resource_fingerprints": {
            "notion_page_id_sha256": "c" * 64,
            "discord_message_id_sha256": "d" * 64,
        },
    }
    assert payload["scenarios"]["reminder"] == {
        "present": True,
        "dirty": False,
        "run_id": RUN_ID,
        "outcome": "passed",
        "stage": None,
        "cleanup_attempts": 1,
        "stages": {"job_notify": 200, "message_delete": 204},
        "resource_fingerprints": {
            "discord_event_id_sha256": "e" * 64,
            "discord_message_id_sha256": "f" * 64,
        },
    }
    assert payload["scenarios"]["notion_cleanup"] == {
        "present": True,
        "dirty": False,
        "run_id": RUN_ID,
        "outcome": "passed",
        "stage": None,
        "cleanup_attempts": 1,
        "stages": {"job_cleanup": 200, "job_interval_guard": 200},
        "resource_fingerprints": {
            "notion_event_database_id_sha256": "1" * 64,
            "due_page_id_sha256": "2" * 64,
            "future_page_id_sha256": "3" * 64,
        },
    }
    assert payload["scenarios"]["webhook_dispatch"] == {
        "present": True,
        "dirty": False,
        "run_id": RUN_ID,
        "outcome": "passed",
        "stage": None,
        "cleanup_attempts": 1,
        "stages": {
            "webhook_delta_fetch": 200,
            "webhook_dispatch": 200,
        },
        "resource_fingerprints": {
            "google_event_id_sha256": "4" * 64,
            "notion_page_id_sha256": "5" * 64,
            "webhook_channel_id_sha256": "6" * 64,
            "webhook_message_number_sha256": "7" * 64,
        },
    }
    assert payload["worker_version"] == {
        "present": True,
        "id_sha256": sha256(b"sensitive-worker-version-id").hexdigest(),
        "tag": RUN_ID,
        "timestamp": "2026-09-01T00:00:00.000Z",
    }
    assert payload["watch"] == {
        "present": True,
        "channel_id_sha256": sha256(b"sensitive-watch-channel-id").hexdigest(),
        "resource_id_sha256": sha256(b"sensitive-watch-resource-id").hexdigest(),
    }
    assert payload["e2e_manifest_enabled"] is True
    assert payload["legacy_manifest_check_complete"] is True
    assert all(not item["present"] for item in payload["legacy_manifests"].values())
    assert all(payload["required_envs"].values())
    assert payload["google_auth"] == {
        "direct_env": True,
        "broker_configured": False,
        "service_account_json_configured": False,
        "cache_present": False,
        "cache_valid": False,
    }
    assert payload["sync_lock"] == {"enabled": True, "ok": True, "status": 200}
    assert payload["orchestrated_writes_enabled"] is False
    assert payload["scenario_routes_enabled"]["qa_notification"] is True
    assert payload["scenario_routes_enabled"]["reminder"] is True
    assert payload["scenario_routes_enabled"]["notion_cleanup"] is True
    assert payload["scenario_routes_enabled"]["webhook_dispatch"] is True
    serialized = json.dumps(payload)
    assert "must-not-be-returned" not in serialized
    assert "sensitive-watch-channel-id" not in serialized
    assert "sensitive-watch-resource-id" not in serialized
    assert "sensitive-worker-version-id" not in serialized


def test_e2e_status_reports_legacy_manifest_without_identifiers() -> None:
    kv = MemoryKV(
        {
            "e2e:google_calendar_crud": json.dumps(
                {
                    "dirty": True,
                    "event_id": "legacy-sensitive-event-id",
                }
            )
        }
    )
    worker = make_worker(
        SimpleNamespace(
            INTERNAL_API_TOKEN="test-token",
            STATE_KV=kv,
            SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        )
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/status",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    payload = response_json(response)

    assert response.status == 200
    assert payload["legacy_manifests"]["google"] == {
        "present": True,
        "dirty": True,
    }
    assert payload["services"]["google"]["present"] is False
    assert "legacy-sensitive-event-id" not in json.dumps(payload)


def test_e2e_webhook_simulation_requires_run_id(monkeypatch) -> None:
    worker = make_worker(
        SimpleNamespace(
            INTERNAL_API_TOKEN="test-token",
            STATE_KV=MemoryKV(),
            E2E_WEBHOOK_SIMULATION_ENABLED="true",
        )
    )
    run_ids: list[str] = []

    async def fake_probe(env, state, dispatch, run_id=None):
        run_ids.append(str(run_id or ""))
        return {"ok": True, "dirty": False, "run_id": run_id}

    monkeypatch.setattr(e2e_entry, "run_webhook_dispatch_probe", fake_probe)

    missing = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/trigger-webhook",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/trigger-webhook",
                method="POST",
                headers=AUTH_HEADERS,
            )
        )
    )

    assert missing.status == 400
    assert response_json(missing) == {"ok": False, "error": "invalid_run_id"}
    assert success.status == 200
    assert response_json(success) == {"ok": True, "dirty": False, "run_id": RUN_ID}
    assert run_ids == [RUN_ID]


def test_orchestrated_write_routes_require_post_and_run_id(monkeypatch) -> None:
    paths = [
        "/sync/all",
        "/gcal/sync",
        "/sync/discord-notion",
        "/jobs/qa-check",
        "/jobs/reminder",
        "/jobs/cleanup",
        "/jobs/run-all",
    ]
    delegated_paths: list[str] = []

    async def fake_base_fetch(self, request):
        delegated_paths.append(request.url)
        return e2e_entry._json_response({"ok": True})

    monkeypatch.setattr(e2e_entry.ApplicationDefault, "fetch", fake_base_fetch)

    for path in paths:
        worker = make_worker(
            SimpleNamespace(
                INTERNAL_API_TOKEN="test-token",
                STATE_KV=MemoryKV(),
                E2E_ORCHESTRATED_WRITES_ENABLED="true",
            )
        )
        missing = run(
            worker.fetch(
                Request(
                    f"https://bot.test{path}",
                    method="POST",
                    headers={"Authorization": "Bearer test-token"},
                )
            )
        )
        wrong_method = run(
            worker.fetch(
                Request(
                    f"https://bot.test{path}",
                    method="GET",
                    headers=AUTH_HEADERS,
                )
            )
        )
        success = run(
            worker.fetch(
                Request(
                    f"https://bot.test{path}",
                    method="POST",
                    headers=AUTH_HEADERS,
                )
            )
        )

        assert missing.status == 400
        assert response_json(missing) == {"ok": False, "error": "invalid_run_id"}
        assert wrong_method.status == 405
        assert response_json(wrong_method) == {
            "ok": False,
            "error": "method_not_allowed",
        }
        assert success.status == 200

    assert delegated_paths == [f"https://bot.test{path}" for path in paths]


def test_orchestrated_write_routes_are_hidden_by_default(monkeypatch) -> None:
    delegated_paths: list[str] = []
    dispatch_sources: list[str] = []

    async def fake_base_fetch(self, request):
        delegated_paths.append(request.url)
        return e2e_entry._json_response({"ok": True})

    async def fake_dispatch(request, state, source, *, google_applier=None):
        dispatch_sources.append(source)
        return e2e_entry._json_response({"ok": True})

    monkeypatch.setattr(e2e_entry.ApplicationDefault, "fetch", fake_base_fetch)
    worker = make_worker(
        SimpleNamespace(
            INTERNAL_API_TOKEN="test-token",
            STATE_KV=MemoryKV(),
        )
    )
    monkeypatch.setattr(worker, "_run_sync_dispatch", fake_dispatch)

    for path in (
        "/admin/e2e/trigger-webhook",
        "/sync/all",
        "/gcal/sync",
        "/sync/discord-notion",
        "/jobs/qa-check",
        "/jobs/reminder",
        "/jobs/cleanup",
        "/jobs/run-all",
    ):
        response = run(
            worker.fetch(
                Request(
                    f"https://bot.test{path}",
                    method="POST",
                    headers=AUTH_HEADERS,
                )
            )
        )

        assert response.status == 404
        assert response_json(response) == {"ok": False, "error": "not_found"}

    assert delegated_paths == []
    assert dispatch_sources == []


def test_unmanaged_mutating_routes_are_hidden(monkeypatch) -> None:
    delegated_paths: list[str] = []

    async def fake_base_fetch(self, request):
        delegated_paths.append(request.url)
        return e2e_entry._json_response({"ok": True})

    monkeypatch.setattr(e2e_entry.ApplicationDefault, "fetch", fake_base_fetch)
    worker = make_worker(SimpleNamespace(INTERNAL_API_TOKEN="test-token"))

    for path in (
        "/admin/google-token",
        "/admin/gcal/watch/ensure",
        "/admin/migration-status",
        "/gcal/webhook",
    ):
        response = run(
            worker.fetch(
                Request(
                    f"https://bot.test{path}",
                    method="POST",
                    headers=AUTH_HEADERS,
                )
            )
        )

        assert response.status == 404
        assert response_json(response) == {"ok": False, "error": "not_found"}

    assert delegated_paths == []


def test_e2e_scheduled_entry_is_disabled(monkeypatch) -> None:
    called = False

    async def fake_base_scheduled(self, controller, env, ctx):
        nonlocal called
        called = True
        return [{"ok": True}]

    monkeypatch.setattr(e2e_entry.ApplicationDefault, "scheduled", fake_base_scheduled)
    worker = make_worker(SimpleNamespace())

    assert run(worker.scheduled(None, worker.env, None)) == []
    assert called is False


def test_probe_exception_keeps_environment_dirty_without_detail(monkeypatch) -> None:
    async def failing_probe(env, state, run_id=None):
        raise RuntimeError("sensitive runtime detail")

    monkeypatch.setattr(e2e_entry, "run_google_calendar_crud_probe", failing_probe)
    worker = make_worker(
        SimpleNamespace(
            E2E_GOOGLE_CRUD_ENABLED="true",
            INTERNAL_API_TOKEN="test-token",
            STATE_KV=MemoryKV(),
            SYNC_DO_LOCK_ENABLED="false",
        )
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=AUTH_HEADERS,
            )
        )
    )

    assert response.status == 409
    assert response_json(response) == {
        "ok": False,
        "dirty": True,
        "error": "e2e_probe_exception",
        "cleanup_required": True,
    }


def test_cleanup_rejects_another_clean_run() -> None:
    cleanup_cases = [
        ("google", "google_calendar_event", cleanup_google_calendar_crud_probe),
        ("discord", "discord_event_message", cleanup_discord_crud_probe),
        ("notion", "notion_pages", cleanup_notion_crud_probe),
    ]
    for service, kind, cleanup in cleanup_cases:
        env = SimpleNamespace(
            STATE_KV=MemoryKV(),
            SYNC_COORDINATOR=make_sync_coordinator_namespace(
                {
                    service: {
                        "version": 1,
                        "kind": kind,
                        "dirty": False,
                        "last_run_id": RUN_ID,
                        "outcome": "passed",
                    }
                }
            ),
        )

        result = run(cleanup(env, StateStore(env), expected_run_id=OTHER_RUN_ID))

        assert result == {
            "ok": False,
            "dirty": False,
            "error": "cleanup_run_id_mismatch",
        }
