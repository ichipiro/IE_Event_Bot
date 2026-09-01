"""Google Calendar watch の channel token 設定を検証する。"""

import asyncio
import hashlib
import json
from types import SimpleNamespace

from workers import Response

import google_watch
from google_watch import ensure_watch_active, register_watch
from state import StateStore
from tests.fakes import MemoryKV


def run(coroutine):
    return asyncio.run(coroutine)


def test_register_watch_requires_channel_token() -> None:
    env = SimpleNamespace(GCAL_WEBHOOK_URL="https://bot.test/gcal/webhook")

    result = run(register_watch(env, StateStore(env)))

    assert result == {"ok": False, "error": "missing_gcal_webhook_token"}


def test_register_watch_rejects_channel_token_over_256_characters() -> None:
    env = SimpleNamespace(
        GCAL_WEBHOOK_URL="https://bot.test/gcal/webhook",
        GCAL_WEBHOOK_TOKEN="x" * 257,
    )

    result = run(register_watch(env, StateStore(env)))

    assert result == {"ok": False, "error": "gcal_webhook_token_too_long"}


def test_register_watch_sends_token_and_saves_only_fingerprint(monkeypatch) -> None:
    kv = MemoryKV()
    env = SimpleNamespace(
        STATE_KV=kv,
        GCAL_WEBHOOK_URL="https://bot.test/gcal/webhook",
        GCAL_WEBHOOK_TOKEN="channel-token",
        GOOGLE_CALENDAR_ID="calendar-id",
    )
    calls: list[dict] = []

    async def fake_access_token(env, state):
        return "google-access-token"

    async def fake_fetch(url, options=None):
        calls.append({"url": url, **(options or {})})
        return Response(
            json.dumps(
                {
                    "id": "channel-id",
                    "resourceId": "resource-id",
                    "expiration": "4102444800000",
                    "token": "channel-token",
                }
            ),
            status=200,
        )

    monkeypatch.setattr(google_watch, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(google_watch, "fetch", fake_fetch)

    result = run(register_watch(env, StateStore(env)))

    request_payload = json.loads(calls[0]["body"])
    saved_state = json.loads(kv.data["gcal_watch_state"])
    fingerprint = hashlib.sha256(b"channel-token").hexdigest()
    assert request_payload["token"] == "channel-token"
    assert saved_state["webhook_token_sha256"] == fingerprint
    assert "token" not in saved_state
    assert "webhook_token_sha256" not in result["watch_state"]
    assert "channel-token" not in json.dumps(result)


def test_register_watch_does_not_return_google_error_body(monkeypatch) -> None:
    env = SimpleNamespace(
        GCAL_WEBHOOK_URL="https://bot.test/gcal/webhook",
        GCAL_WEBHOOK_TOKEN="channel-token",
        GOOGLE_CALENDAR_ID="calendar-id",
    )

    async def fake_access_token(env, state):
        return "google-access-token"

    async def fake_fetch(url, options=None):
        return Response(
            'invalid channel token: "channel-token"',
            status=400,
        )

    monkeypatch.setattr(google_watch, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(google_watch, "fetch", fake_fetch)

    result = run(register_watch(env, StateStore(env)))

    assert result == {
        "ok": False,
        "status": 400,
        "error": "google_watch_http_400",
    }
    assert "channel-token" not in json.dumps(result)


def test_ensure_watch_renews_existing_watch_without_token_fingerprint(monkeypatch) -> None:
    kv = MemoryKV(
        {
            "gcal_watch_state": json.dumps(
                {
                    "channel_id": "legacy-channel",
                    "resource_id": "legacy-resource",
                    "expiration": "4102444800000",
                }
            )
        }
    )
    env = SimpleNamespace(STATE_KV=kv, GCAL_WEBHOOK_TOKEN="channel-token")
    renew_calls = 0

    async def fake_renew(env, state):
        nonlocal renew_calls
        renew_calls += 1
        return {"ok": True}

    monkeypatch.setattr(google_watch, "renew_watch", fake_renew)

    result = run(ensure_watch_active(env, StateStore(env)))

    assert result["ok"] is True
    assert result["action"] == "renew_token_changed"
    assert renew_calls == 1


def test_ensure_watch_renews_after_channel_token_rotation(monkeypatch) -> None:
    old_fingerprint = hashlib.sha256(b"old-token").hexdigest()
    kv = MemoryKV(
        {
            "gcal_watch_state": json.dumps(
                {
                    "channel_id": "old-channel",
                    "resource_id": "old-resource",
                    "expiration": "4102444800000",
                    "webhook_token_sha256": old_fingerprint,
                }
            )
        }
    )
    env = SimpleNamespace(STATE_KV=kv, GCAL_WEBHOOK_TOKEN="new-token")
    renew_calls = 0

    async def fake_renew(env, state):
        nonlocal renew_calls
        renew_calls += 1
        return {"ok": True}

    monkeypatch.setattr(google_watch, "renew_watch", fake_renew)

    result = run(ensure_watch_active(env, StateStore(env)))

    assert result["ok"] is True
    assert result["action"] == "renew_token_changed"
    assert renew_calls == 1


def test_ensure_watch_keeps_existing_watch_when_token_is_missing(monkeypatch) -> None:
    original_state = {
        "channel_id": "existing-channel",
        "resource_id": "existing-resource",
        "expiration": "4102444800000",
    }
    kv = MemoryKV({"gcal_watch_state": json.dumps(original_state)})
    env = SimpleNamespace(STATE_KV=kv)

    async def fail_if_renewed(env, state):
        raise AssertionError("設定不備の状態で既存 watch を停止してはならない")

    monkeypatch.setattr(google_watch, "renew_watch", fail_if_renewed)

    result = run(ensure_watch_active(env, StateStore(env)))

    assert result == {
        "ok": False,
        "action": "configuration_error",
        "error": "missing_gcal_webhook_token",
    }
    assert json.loads(kv.data["gcal_watch_state"]) == original_state
    assert kv.put_calls == []


def test_ensure_watch_keeps_watch_with_current_token_fingerprint(monkeypatch) -> None:
    fingerprint = hashlib.sha256(b"channel-token").hexdigest()
    kv = MemoryKV(
        {
            "gcal_watch_state": json.dumps(
                {
                    "channel_id": "current-channel",
                    "resource_id": "current-resource",
                    "expiration": "4102444800000",
                    "webhook_token_sha256": fingerprint,
                }
            )
        }
    )
    env = SimpleNamespace(STATE_KV=kv, GCAL_WEBHOOK_TOKEN="channel-token")

    async def fail_if_renewed(env, state):
        raise AssertionError("同じ token の watch を更新してはならない")

    monkeypatch.setattr(google_watch, "renew_watch", fail_if_renewed)

    result = run(ensure_watch_active(env, StateStore(env)))

    assert result["ok"] is True
    assert result["action"] == "noop_valid"
    assert "webhook_token_sha256" not in result["watch_state"]
