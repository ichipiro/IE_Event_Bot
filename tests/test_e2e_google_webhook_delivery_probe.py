"""Google実Webhook到達プローブを外部通信なしで検証する。"""

import asyncio
import json
from types import SimpleNamespace

from workers import Response

import e2e_google_webhook_delivery_probe as probe
from state import StateStore
from tests.fakes import MemoryKV, make_sync_coordinator_namespace


RUN_ID = "E2E-20260902T110000Z-1234abcd"


def run(coroutine):
    return asyncio.run(coroutine)


def make_env():
    return SimpleNamespace(
        GOOGLE_CALENDAR_ID="e2e-calendar@example.test",
        GCAL_WEBHOOK_URL="https://e2e-worker.example.test/gcal/webhook",
        GCAL_WEBHOOK_TOKEN="channel-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
    )


def install_api_stub(
    monkeypatch,
    env,
    *,
    deliver_before_response: bool,
    stop_status: int = 204,
):
    calls: list[dict] = []
    resource_id = "google-watch-resource"

    async def fake_access_token(auth_env, auth_state):
        assert auth_env is env
        assert auth_state is not StateStore(env)
        return "access-token"

    async def fake_fetch(url, options=None):
        request = dict(options or {})
        method = str(request.get("method") or "GET")
        payload = json.loads(str(request.get("body") or "{}"))
        calls.append({"url": url, "method": method, "payload": payload})
        if url.endswith("/events/watch"):
            assert payload["type"] == "web_hook"
            assert payload["address"] == env.GCAL_WEBHOOK_URL
            assert payload["token"] == env.GCAL_WEBHOOK_TOKEN
            assert payload["params"] == {"ttl": "600"}
            if deliver_before_response:
                accepted = await StateStore(env).record_e2e_webhook_delivery(
                    channel_id=payload["id"],
                    resource_id=resource_id,
                    resource_state="sync",
                    message_number="1",
                )
                assert accepted is True
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
        assert payload["resourceId"] == resource_id
        return Response("", status=stop_status)

    monkeypatch.setattr(probe, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(probe, "fetch", fake_fetch)
    return calls, resource_id


def test_probe_accepts_notification_before_watch_response_and_stops_watch(
    monkeypatch,
) -> None:
    env = make_env()
    calls, _ = install_api_stub(
        monkeypatch,
        env,
        deliver_before_response=True,
    )

    result = run(probe.run_google_webhook_delivery_probe(env, StateStore(env), RUN_ID))

    assert result == {
        "ok": True,
        "dirty": False,
        "run_id": RUN_ID,
        "stages": {
            "watch_create": 200,
            "webhook_sync_delivery": 204,
            "watch_stop": 204,
        },
        "cleanup": {"ok": True, "attempts": 1},
    }
    assert [call["method"] for call in calls] == ["POST", "POST"]
    manifest = run(StateStore(env).get_e2e_manifest("webhook_delivery"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "webhook_url_sha256",
        "watch_channel_id_sha256",
        "watch_resource_id_sha256",
    }
    serialized = json.dumps(manifest)
    assert "google-watch-resource" not in serialized
    assert env.GCAL_WEBHOOK_URL not in serialized
    assert env.GCAL_WEBHOOK_TOKEN not in serialized
    assert env.STATE_KV.put_calls == []


def test_probe_accepts_notification_after_watch_response(monkeypatch) -> None:
    env = make_env()
    _, resource_id = install_api_stub(
        monkeypatch,
        env,
        deliver_before_response=False,
    )
    delivered = False

    async def fake_sleep(_seconds):
        nonlocal delivered
        if delivered:
            return
        delivered = True
        manifest = await StateStore(env).get_e2e_manifest("webhook_delivery")
        assert isinstance(manifest, dict)
        accepted = await StateStore(env).record_e2e_webhook_delivery(
            channel_id=manifest["channel_id"],
            resource_id=resource_id,
            resource_state="sync",
            message_number="1",
        )
        assert accepted is True

    monkeypatch.setattr(probe.asyncio, "sleep", fake_sleep)

    result = run(probe.run_google_webhook_delivery_probe(env, StateStore(env), RUN_ID))

    assert delivered is True
    assert result["ok"] is True
    assert result["dirty"] is False


def test_probe_timeout_is_stopped_and_recorded_as_failed_clean(monkeypatch) -> None:
    env = make_env()
    install_api_stub(monkeypatch, env, deliver_before_response=False)
    monkeypatch.setattr(probe, "_POLL_ATTEMPTS", 1)

    result = run(probe.run_google_webhook_delivery_probe(env, StateStore(env), RUN_ID))

    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["error"] == "google_webhook_delivery_not_observed"
    manifest = run(StateStore(env).get_e2e_manifest("webhook_delivery"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "failed_clean"
    assert manifest["stages"] == {"watch_create": 200, "watch_stop": 204}


def test_failed_stop_keeps_owned_manifest_for_explicit_cleanup(monkeypatch) -> None:
    env = make_env()
    install_api_stub(
        monkeypatch,
        env,
        deliver_before_response=True,
        stop_status=500,
    )

    failed = run(probe.run_google_webhook_delivery_probe(env, StateStore(env), RUN_ID))

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["cleanup_required"] is True
    dirty = run(StateStore(env).get_e2e_manifest("webhook_delivery"))
    assert isinstance(dirty, dict)
    assert dirty["dirty"] is True
    assert dirty["resource_id"] == "google-watch-resource"

    install_api_stub(
        monkeypatch,
        env,
        deliver_before_response=False,
        stop_status=204,
    )
    cleaned = run(
        probe.cleanup_google_webhook_delivery_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert cleaned == {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
    manifest = run(StateStore(env).get_e2e_manifest("webhook_delivery"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False


def test_probe_rejects_invalid_configuration_before_external_call(monkeypatch) -> None:
    env = make_env()
    env.GOOGLE_CALENDAR_ID = "primary"
    called = False

    async def unexpected_access_token(_env, _state):
        nonlocal called
        called = True
        return "access-token"

    monkeypatch.setattr(probe, "get_google_access_token", unexpected_access_token)

    result = run(probe.run_google_webhook_delivery_probe(env, StateStore(env), RUN_ID))

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "primary_calendar_forbidden",
    }
    assert called is False
