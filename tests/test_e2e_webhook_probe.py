"""所有資源限定Google webhook ingress simulationを外部通信なしで検証する。"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import e2e_entry
import e2e_google_notion_probe
import e2e_webhook_probe
import google_calendar_sync
from state import StateStore
from tests.fakes import MemoryKV, Request
from tests.test_e2e_google_notion_probe import (
    PAGE_ID,
    ROUTE_HEADERS,
    RUN_ID,
    install_api_stub,
    make_env,
)


def run(coroutine):
    return asyncio.run(coroutine)


def response_json(response: Response) -> dict:
    return json.loads(run(response.text()))


def install_delta_stub(monkeypatch, google_events, *, include_owned: bool = True):
    list_calls: list[str] = []

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        assert method == "GET"
        assert parsed.netloc == "www.googleapis.com"
        assert parsed.path.endswith("/events")
        list_calls.append(url)

        updated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        events = [
            {
                "id": "unowned-event",
                "updated": updated,
                "extendedProperties": {
                    "private": {"ie_event_bot_e2e_run": "different-run"},
                },
            }
        ]
        if include_owned:
            events.extend(
                {**json.loads(json.dumps(event)), "updated": updated}
                for event in google_events.values()
            )
        return Response(json.dumps({"items": events}), status=200)

    monkeypatch.setattr(google_calendar_sync, "fetch", fake_fetch)
    return list_calls


def make_webhook_env():
    env = make_env()
    env.GOOGLE_API_BEARER_TOKEN = "access-token"
    env.GCAL_WEBHOOK_TOKEN = "webhook-channel-token"
    env.KV_SYNC_COOLDOWN_ENABLED = "false"
    env.KV_GCAL_DEDUPE_ENABLED = "true"
    env.SYNC_ALL_INCLUDE_DISCORD_NOTION = "false"
    env.SYNC_DO_LOCK_ENABLED = "false"
    return env


def test_webhook_probe_fetches_delta_applies_owned_event_and_cleans(
    monkeypatch,
) -> None:
    google_events, notion_pages, _, _ = install_api_stub(monkeypatch)
    list_calls = install_delta_stub(monkeypatch, google_events)
    env = make_webhook_env()
    worker = e2e_entry.Default()
    worker.env = env

    async def cache_token_in_received_state(_env, received_state):
        await received_state.put_text_if_changed(
            "google:access_token",
            "transient-access-token",
        )
        await received_state.put_text_if_changed("google:expires_at", "4102444800")
        return "access-token"

    monkeypatch.setattr(
        e2e_google_notion_probe,
        "get_google_access_token",
        cache_token_in_received_state,
    )

    async def deliver(webhook_request, sync_state, google_applier):
        return await worker._handle_gcal_webhook(
            webhook_request,
            sync_state,
            google_applier=google_applier,
        )

    result = run(
        e2e_webhook_probe.run_webhook_dispatch_probe(
            env,
            StateStore(env),
            deliver,
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"]["webhook_fixture"] == 200
    assert result["stages"]["webhook_token_reject"] == 401
    assert result["stages"]["webhook_token_reject_isolated"] == 200
    assert result["stages"]["webhook_first_delivery"] == 204
    assert result["stages"]["webhook_duplicate_delivery"] == 204
    assert result["stages"]["webhook_message_dedupe"] == 200
    assert result["stages"]["webhook_delta_fetch"] == 200
    assert result["stages"]["webhook_dispatch"] == 204
    assert result["stages"]["webhook_cursor_isolated"] == 200
    assert result["stages"]["webhook_last_epoch_isolated"] == 200
    assert result["stages"]["webhook_last_result_isolated"] == 200
    assert result["stages"]["webhook_dedupe_delete"] == 200
    assert len(list_calls) == 1
    assert google_events == {}
    assert notion_pages[PAGE_ID]["archived"] is True
    assert env.STATE_KV.put_calls == []
    storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data
    assert not any(key.startswith("gcal_msg:") for key in storage)

    manifest = run(StateStore(env).get_e2e_manifest("webhook_dispatch"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "google_webhook_simulation"
    assert manifest["dirty"] is False
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "notion_database_id_sha256",
        "google_event_id_sha256",
        "notion_page_id_sha256",
        "webhook_channel_id_sha256",
        "webhook_message_number_sha256",
    }
    assert "webhook_dedupe" not in manifest
    assert "webhook_dedupe_fingerprints" not in manifest


def test_webhook_probe_delta_miss_fails_clean_without_notion_write(monkeypatch) -> None:
    google_events, notion_pages, _, _ = install_api_stub(monkeypatch)
    install_delta_stub(monkeypatch, google_events, include_owned=False)
    env = make_webhook_env()
    worker = e2e_entry.Default()
    worker.env = env

    async def deliver(webhook_request, sync_state, google_applier):
        return await worker._handle_gcal_webhook(
            webhook_request,
            sync_state,
            google_applier=google_applier,
        )

    result = run(
        e2e_webhook_probe.run_webhook_dispatch_probe(
            env,
            StateStore(env),
            deliver,
            RUN_ID,
        )
    )

    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["error"] == "webhook_dispatch_failed"
    assert result["stages"]["webhook_delta_fetch"] == 500
    assert google_events == {}
    assert notion_pages == {}
    manifest = run(StateStore(env).get_e2e_manifest("webhook_dispatch"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "failed_clean"


def test_webhook_probe_rejects_disabled_dedupe_before_external_io() -> None:
    env = make_webhook_env()
    env.KV_GCAL_DEDUPE_ENABLED = "false"

    async def unexpected_delivery(webhook_request, sync_state, google_applier):
        raise AssertionError("Webhook delivery must not start")

    result = run(
        e2e_webhook_probe.run_webhook_dispatch_probe(
            env,
            StateStore(env),
            unexpected_delivery,
            RUN_ID,
        )
    )

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "google_message_dedupe_must_be_enabled",
    }


def test_webhook_probe_keeps_dedupe_marker_dirty_until_cleanup_recovers(
    monkeypatch,
) -> None:
    google_events, notion_pages, _, _ = install_api_stub(monkeypatch)
    install_delta_stub(monkeypatch, google_events)
    env = make_webhook_env()
    worker = e2e_entry.Default()
    worker.env = env
    original_clear = StateStore.clear_e2e_google_message_seen

    async def fail_clear(self, channel_id, message_number, owner_run_id):
        raise RuntimeError("fixed test failure")

    monkeypatch.setattr(StateStore, "clear_e2e_google_message_seen", fail_clear)

    async def deliver(webhook_request, sync_state, google_applier):
        return await worker._handle_gcal_webhook(
            webhook_request,
            sync_state,
            google_applier=google_applier,
        )

    failed = run(
        e2e_webhook_probe.run_webhook_dispatch_probe(
            env,
            StateStore(env),
            deliver,
            RUN_ID,
        )
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "webhook_dedupe_cleanup_failed"
    assert google_events == {}
    assert notion_pages[PAGE_ID]["archived"] is True
    storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data
    assert any(key.startswith("gcal_msg:") for key in storage)

    monkeypatch.setattr(StateStore, "clear_e2e_google_message_seen", original_clear)
    state = StateStore(env)
    dirty_manifest = run(state.get_e2e_manifest("webhook_dispatch"))
    assert isinstance(dirty_manifest, dict)
    tampered_manifest = json.loads(json.dumps(dirty_manifest))
    tampered_manifest["webhook_dedupe_fingerprints"][
        "webhook_channel_id_sha256"
    ] = "0" * 64
    run(state.put_e2e_manifest("webhook_dispatch", tampered_manifest))
    rejected = run(
        e2e_webhook_probe.cleanup_webhook_dispatch_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert rejected["ok"] is False
    assert rejected["dirty"] is True
    assert rejected["error"] == "webhook_dedupe_target_mismatch"
    assert any(key.startswith("gcal_msg:") for key in storage)

    run(state.put_e2e_manifest("webhook_dispatch", dirty_manifest))
    recovered = run(
        e2e_webhook_probe.cleanup_webhook_dispatch_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert recovered["ok"] is True
    assert recovered["dirty"] is False
    assert not any(key.startswith("gcal_msg:") for key in storage)
    manifest = run(state.get_e2e_manifest("webhook_dispatch"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "recovered"


def test_webhook_route_requires_dedicated_flag_auth_post_and_run_id(
    monkeypatch,
) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, dispatch, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False, "run_id": run_id}

    monkeypatch.setattr(e2e_entry, "run_webhook_dispatch_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_WEBHOOK_SIMULATION_ENABLED="true",
        E2E_ORCHESTRATED_WRITES_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
    )

    hidden_worker = e2e_entry.Default()
    hidden_worker.env = SimpleNamespace(
        E2E_WEBHOOK_SIMULATION_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
    )
    hidden = run(
        hidden_worker.fetch(
            Request(
                "https://bot.test/admin/e2e/trigger-webhook",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )
    unauthorized = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/trigger-webhook",
                method="POST",
            )
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/trigger-webhook",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
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
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert hidden.status == 404
    assert response_json(hidden) == {"ok": False, "error": "not_found"}
    assert unauthorized.status == 401
    assert wrong_method.status == 405
    assert missing_run_id.status == 400
    assert success.status == 200
    assert response_json(success)["run_id"] == RUN_ID
    assert calls == [RUN_ID]
