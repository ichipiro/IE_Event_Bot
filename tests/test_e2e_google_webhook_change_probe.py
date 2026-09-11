"""Google実exists通知からの所有資源限定dispatchを外部通信なしで検証する。"""

import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from workers import Response

import e2e_entry
import e2e_google_notion_probe
import e2e_google_probe
import e2e_google_webhook_change_probe as probe
import e2e_google_webhook_delivery_probe
import google_calendar_sync
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_google_notion_probe import (
    PAGE_ID,
    RUN_ID,
    install_api_stub,
    make_env,
)


def run(coroutine):
    return asyncio.run(coroutine)


def make_change_env():
    env = make_env()
    env.GOOGLE_API_BEARER_TOKEN = "access-token"
    env.GCAL_WEBHOOK_URL = "https://e2e-worker.example.test/gcal/webhook"
    env.GCAL_WEBHOOK_TOKEN = "channel-token"
    env.E2E_GOOGLE_WEBHOOK_CHANGE_ENABLED = "true"
    env.E2E_GOOGLE_WEBHOOK_DELIVERY_ENABLED = "true"
    env.KV_SYNC_COOLDOWN_ENABLED = "false"
    env.KV_GCAL_DEDUPE_ENABLED = "true"
    env.SYNC_ALL_INCLUDE_DISCORD_NOTION = "false"
    env.SYNC_DO_LOCK_ENABLED = "true"
    env.SYNC_DO_LOCK_TTL_SECONDS = "120"
    return env


def webhook_request(
    channel_id: str,
    resource_id: str,
    state: str,
    message_number: str,
) -> Request:
    return Request(
        "https://e2e-worker.example.test/gcal/webhook",
        method="POST",
        headers={
            "X-Goog-Channel-Token": "channel-token",
            "X-Goog-Channel-ID": channel_id,
            "X-Goog-Resource-ID": resource_id,
            "X-Goog-Resource-State": state,
            "X-Goog-Message-Number": message_number,
        },
    )


def test_real_exists_callback_dispatches_once_and_cleans_owned_resources(
    monkeypatch,
) -> None:
    google_events, notion_pages, _, _ = install_api_stub(monkeypatch)
    env = make_change_env()
    state = StateStore(env)
    worker = e2e_entry.Default()
    worker.env = env
    resource_id = "google-change-resource"
    order: list[str] = []
    callback_statuses: list[int] = []
    delta_calls: list[str] = []

    async def fake_access_token(_env, _state):
        return "access-token"

    async def fake_watch_fetch(url, options=None):
        request = dict(options or {})
        payload = json.loads(str(request.get("body") or "{}"))
        if url.endswith("/events/watch"):
            order.append("watch_create")
            assert payload["address"] == env.GCAL_WEBHOOK_URL
            assert payload["token"] == env.GCAL_WEBHOOK_TOKEN
            assert payload["params"] == {"ttl": "600"}
            response = await worker.fetch(
                webhook_request(payload["id"], resource_id, "sync", "1")
            )
            callback_statuses.append(response.status)
            return Response(
                json.dumps(
                    {
                        "id": payload["id"],
                        "resourceId": resource_id,
                        "expiration": "1790000000000",
                    }
                ),
                status=200,
            )
        assert url.endswith("/channels/stop")
        order.append("watch_stop")
        assert payload["resourceId"] == resource_id
        return Response("", status=204)

    async def fake_delta_fetch(url, options=None):
        parsed = urlparse(url)
        assert parsed.netloc == "www.googleapis.com"
        assert parsed.path.endswith("/events")
        delta_calls.append(url)
        updated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        items = [
            {
                "id": "unowned-event",
                "updated": updated,
                "extendedProperties": {
                    "private": {"ie_event_bot_e2e_run": "different-run"},
                },
            },
            *(
                {**json.loads(json.dumps(event)), "updated": updated}
                for event in google_events.values()
            ),
        ]
        return Response(json.dumps({"items": items}), status=200)

    original_google_request = e2e_google_probe._google_request

    async def update_and_deliver(method, url, bearer_token, payload=None):
        if method != "PUT":
            return await original_google_request(method, url, bearer_token, payload)
        order.append("google_update")
        parsed = urlparse(url)
        event_id = unquote(parsed.path.split("/events/", 1)[1])
        updated = json.loads(json.dumps(payload or {}))
        updated["id"] = event_id
        google_events[event_id] = updated
        channel_id = probe._channel_id(RUN_ID)
        first = await worker.fetch(
            webhook_request(channel_id, resource_id, "exists", "2")
        )
        duplicate = await worker.fetch(
            webhook_request(channel_id, resource_id, "exists", "3")
        )
        callback_statuses.extend([first.status, duplicate.status])
        return 200, updated

    original_delete = e2e_google_probe._delete_event

    async def ordered_delete(*args, **kwargs):
        order.append("google_delete")
        return await original_delete(*args, **kwargs)

    monkeypatch.setattr(probe, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(e2e_google_webhook_delivery_probe, "fetch", fake_watch_fetch)
    monkeypatch.setattr(google_calendar_sync, "fetch", fake_delta_fetch)
    monkeypatch.setattr(probe, "_google_request", update_and_deliver)
    monkeypatch.setattr(e2e_google_notion_probe, "_delete_event", ordered_delete)

    result = run(probe.run_google_webhook_change_probe(env, state, RUN_ID))

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert callback_statuses == [204, 204, 204]
    assert len(delta_calls) == 1
    assert order.index("watch_stop") < order.index("google_delete")
    assert google_events == {}
    assert notion_pages[PAGE_ID]["archived"] is True
    assert env.STATE_KV.put_calls == []
    storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data
    assert not any(key.startswith("gcal_msg:") for key in storage)

    manifest = run(state.get_e2e_manifest("webhook_change"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "google_webhook_change_dispatch"
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert manifest["stages"]["watch_create"] == 200
    assert manifest["stages"]["webhook_sync_delivery"] == 204
    assert manifest["stages"]["google_update"] == 200
    assert manifest["stages"]["webhook_exists_claim"] == 200
    assert manifest["stages"]["webhook_exists_delivery"] == 204
    assert manifest["stages"]["webhook_message_dedupe"] == 200
    assert manifest["stages"]["webhook_delta_fetch"] == 200
    assert manifest["stages"]["webhook_cursor_isolated"] == 200
    assert manifest["stages"]["webhook_last_epoch_isolated"] == 200
    assert manifest["stages"]["webhook_last_result_isolated"] == 200
    assert manifest["stages"]["watch_stop"] == 204
    assert manifest["stages"]["webhook_dedupe_delete"] == 200
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "notion_database_id_sha256",
        "google_event_id_sha256",
        "notion_page_id_sha256",
        "watch_channel_id_sha256",
        "watch_resource_id_sha256",
        "webhook_message_number_sha256",
    }
    serialized = json.dumps(manifest)
    assert resource_id not in serialized
    assert env.GCAL_WEBHOOK_URL not in serialized
    assert env.GCAL_WEBHOOK_TOKEN not in serialized


def test_watch_stop_failure_keeps_event_and_manifest_dirty(monkeypatch) -> None:
    google_events, notion_pages, _, _ = install_api_stub(monkeypatch)
    env = make_change_env()
    state = StateStore(env)
    stop_status = 500
    resource_id = "google-change-resource"

    async def fake_access_token(_env, _state):
        return "access-token"

    async def fake_watch_fetch(url, options=None):
        payload = json.loads(str((options or {}).get("body") or "{}"))
        if url.endswith("/events/watch"):
            await state.record_e2e_webhook_change_sync(
                channel_id=payload["id"],
                resource_id=resource_id,
                resource_state="sync",
                message_number="1",
            )
            return Response(
                json.dumps({"id": payload["id"], "resourceId": resource_id}),
                status=200,
            )
        return Response("", status=stop_status)

    async def update_without_callback(method, url, bearer_token, payload=None):
        if method == "PUT":
            event_id = unquote(urlparse(url).path.split("/events/", 1)[1])
            updated = {**json.loads(json.dumps(payload or {})), "id": event_id}
            google_events[event_id] = updated
            return 200, updated
        return await e2e_google_probe._google_request(method, url, bearer_token, payload)

    monkeypatch.setattr(probe, "_DISPATCH_POLL_ATTEMPTS", 1)
    monkeypatch.setattr(probe, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(e2e_google_webhook_delivery_probe, "fetch", fake_watch_fetch)
    monkeypatch.setattr(probe, "_google_request", update_without_callback)

    result = run(probe.run_google_webhook_change_probe(env, state, RUN_ID))

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "google_watch_stop_failed"
    assert len(google_events) == 1
    assert notion_pages == {}
    manifest = run(state.get_e2e_manifest("webhook_change"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["stage"] == "cleanup_failed"

    stop_status = 204
    recovered = run(
        probe.cleanup_google_webhook_change_probe(env, state, RUN_ID)
    )

    assert recovered["ok"] is True
    assert recovered["dirty"] is False
    assert google_events == {}
