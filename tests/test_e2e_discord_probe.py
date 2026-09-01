"""E2E専用Discord CRUDプローブを外部通信なしで検証する。"""

import asyncio
import json
import re
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import e2e_discord_probe
import e2e_entry
from e2e_entry import Default
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


ROUTE_RUN_ID = "E2E-20260901T000000Z-1234abcd"
ROUTE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-E2E-Run-ID": ROUTE_RUN_ID,
}


def run(coroutine):
    return asyncio.run(coroutine)


def response_json(response: Response) -> dict:
    return json.loads(run(response.text()))


def make_worker(env) -> Default:
    worker = Default()
    worker.env = env
    return worker


def make_env(
    kv: MemoryKV | None = None,
    manifests: dict[str, dict] | None = None,
):
    return SimpleNamespace(
        DISCORD_TOKEN="bot-token",
        DISCORD_GUILD_ID="guild-id",
        EVENT_CREATE_CHANNEL_ID="channel-id",
        EVENT_CREATE_ROLE_ID="role-id",
        STATE_KV=kv or MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(manifests),
    )


def install_discord_api_stub(
    monkeypatch,
    *,
    guild_name: str = "IE Event Bot E2E",
    channel_name: str = "e2e-events",
    channel_guild_id: str = "guild-id",
    role_name: str = "E2E Reminder",
    role_id: str = "role-id",
    role_mentionable: bool = True,
    event_delete_statuses: list[int] | None = None,
    message_delete_statuses: list[int] | None = None,
    lose_event_create_response: bool = False,
    hide_event_search_results: bool = False,
):
    events: dict[str, dict] = {}
    messages: dict[str, dict] = {}
    calls: list[dict] = []
    control = {
        "event_delete_statuses": list(event_delete_statuses or [204]),
        "message_delete_statuses": list(message_delete_statuses or [204]),
        "lose_event_create_response": lose_event_create_response,
        "hide_event_search_results": hide_event_search_results,
    }

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        path = parsed.path.removeprefix("/api/v10")
        payload = json.loads(str(request.get("body") or "{}"))
        calls.append(
            {
                "method": method,
                "path": path,
                "query": parsed.query,
                "payload": payload,
                "headers": dict(request.get("headers") or {}),
            }
        )

        if method == "GET" and path == "/guilds/guild-id":
            return Response(
                json.dumps({"id": "guild-id", "name": guild_name}),
                status=200,
            )
        if method == "GET" and path == "/channels/channel-id":
            return Response(
                json.dumps(
                    {
                        "id": "channel-id",
                        "guild_id": channel_guild_id,
                        "name": channel_name,
                        "type": 0,
                    }
                ),
                status=200,
            )
        if method == "GET" and path == "/guilds/guild-id/roles":
            return Response(
                json.dumps(
                    [
                        {
                            "id": role_id,
                            "name": role_name,
                            "mentionable": role_mentionable,
                        }
                    ]
                ),
                status=200,
            )

        event_collection = "/guilds/guild-id/scheduled-events"
        if path == event_collection and method == "POST":
            event = {
                **payload,
                "id": "event-id",
                "guild_id": "guild-id",
                "status": 1,
            }
            events["event-id"] = event
            if control["lose_event_create_response"]:
                control["lose_event_create_response"] = False
                raise RuntimeError("response lost after create")
            return Response(json.dumps(event), status=200)
        if path == event_collection and method == "GET":
            visible_events = [] if control["hide_event_search_results"] else list(events.values())
            return Response(json.dumps(visible_events), status=200)
        if path.startswith(f"{event_collection}/"):
            event_id = path.rsplit("/", 1)[-1]
            if method == "GET":
                if event_id not in events:
                    return Response("", status=404)
                return Response(json.dumps(events[event_id]), status=200)
            if method == "PATCH":
                if event_id not in events:
                    return Response("", status=404)
                events[event_id].update(payload)
                return Response(json.dumps(events[event_id]), status=200)
            if method == "DELETE":
                statuses = control["event_delete_statuses"]
                status = statuses.pop(0) if statuses else 204
                if status < 300:
                    events.pop(event_id, None)
                return Response("", status=status)

        message_collection = "/channels/channel-id/messages"
        if path == message_collection and method == "POST":
            allowed_mentions = payload.get("allowed_mentions") or {}
            message = {
                "id": "message-id",
                "channel_id": "channel-id",
                "content": payload.get("content"),
                "nonce": payload.get("nonce"),
                "mention_roles": list(allowed_mentions.get("roles") or []),
                "mention_everyone": False,
                "reactions": [],
            }
            messages["message-id"] = message
            return Response(json.dumps(message), status=200)
        if path == message_collection and method == "GET":
            return Response(json.dumps(list(messages.values())), status=200)
        if path.startswith(f"{message_collection}/"):
            message_id = path.split("/messages/", 1)[1].split("/", 1)[0]
            if "/reactions/" in path and method == "PUT":
                if message_id not in messages:
                    return Response("", status=404)
                messages[message_id]["reactions"] = [
                    {"emoji": {"name": "✅"}, "me": True, "count": 1}
                ]
                return Response("", status=204)
            if method == "GET":
                if message_id not in messages:
                    return Response("", status=404)
                return Response(json.dumps(messages[message_id]), status=200)
            if method == "PATCH":
                if message_id not in messages:
                    return Response("", status=404)
                allowed_mentions = payload.get("allowed_mentions") or {}
                messages[message_id].update(
                    {
                        "content": payload.get("content"),
                        "mention_roles": list(allowed_mentions.get("roles") or []),
                        "mention_everyone": False,
                    }
                )
                return Response(json.dumps(messages[message_id]), status=200)
            if method == "DELETE":
                statuses = control["message_delete_statuses"]
                status = statuses.pop(0) if statuses else 204
                if status < 300:
                    messages.pop(message_id, None)
                return Response("", status=status)

        raise AssertionError(f"想定外のDiscord API呼び出し: {method} {path}")

    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    return events, messages, calls, control


def test_discord_crud_probe_cleans_resources_and_manifest(monkeypatch) -> None:
    events, messages, calls, _ = install_discord_api_stub(monkeypatch)
    env = make_env()

    result = run(e2e_discord_probe.run_discord_crud_probe(env, StateStore(env)))

    assert result["ok"] is True
    assert result["dirty"] is False
    assert re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", result["run_id"])
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"] == {
        "target_guild": 200,
        "target_channel": 200,
        "target_role": 200,
        "event_create": 200,
        "event_read": 200,
        "event_update": 200,
        "event_read_updated": 200,
        "message_create": 200,
        "message_read": 200,
        "message_update": 200,
        "message_reaction": 204,
        "message_read_updated": 200,
        "message_cleanup_verify": 200,
        "message_delete": 204,
        "event_cleanup_verify": 200,
        "event_delete": 204,
    }
    assert "rate_limit_retries" not in result
    assert events == {}
    assert messages == {}

    message_calls = [
        call
        for call in calls
        if call["method"] in ("POST", "PATCH")
        and call["path"].startswith("/channels/channel-id/messages")
    ]
    assert len(message_calls) == 2
    for call in message_calls:
        assert call["payload"]["allowed_mentions"] == {
            "parse": [],
            "roles": ["role-id"],
            "replied_user": False,
        }
    assert message_calls[0]["payload"]["enforce_nonce"] is True
    assert len(message_calls[0]["payload"]["nonce"]) == 24
    assert all(call["headers"]["Authorization"] == "Bot bot-token" for call in calls)
    assert all(
        call["headers"]["User-Agent"].startswith("DiscordBot (") for call in calls
    )

    manifest = run(StateStore(env).get_e2e_manifest("discord"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False
    assert manifest["last_run_id"] == result["run_id"]
    assert manifest["stages"] == result["stages"]
    assert manifest["started_at"]
    assert manifest["completed_at"]
    assert set(manifest["resource_fingerprints"]) == {
        "guild_id_sha256",
        "channel_id_sha256",
        "role_id_sha256",
        "discord_event_id_sha256",
        "discord_message_id_sha256",
    }
    assert "event_id" not in manifest
    assert "message_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_discord_crud_probe_reconciles_lost_create_response(monkeypatch) -> None:
    _, _, calls, _ = install_discord_api_stub(
        monkeypatch,
        lose_event_create_response=True,
    )
    env = make_env()

    result = run(e2e_discord_probe.run_discord_crud_probe(env, StateStore(env)))

    assert result["ok"] is True
    assert result["stages"]["event_create"] == 0
    assert result["stages"]["event_create_reconcile"] == 200
    event_posts = [
        call
        for call in calls
        if call["method"] == "POST"
        and call["path"] == "/guilds/guild-id/scheduled-events"
    ]
    assert len(event_posts) == 1


def test_discord_lost_create_with_empty_search_stays_dirty(monkeypatch) -> None:
    events, _, _, _ = install_discord_api_stub(
        monkeypatch,
        lose_event_create_response=True,
        hide_event_search_results=True,
    )
    env = make_env()
    state = StateStore(env)

    result = run(e2e_discord_probe.run_discord_crud_probe(env, state))

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "discord_event_ownership_unresolved"
    assert set(events) == {"event-id"}
    manifest = run(state.get_e2e_manifest("discord"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["create_attempted"]["event"] is True


def test_discord_crud_probe_accepts_renamed_targets_when_ids_match(monkeypatch) -> None:
    _, _, calls, _ = install_discord_api_stub(
        monkeypatch,
        guild_name="Renamed E2E Guild",
        channel_name="renamed-e2e-channel",
        role_name="Renamed E2E Role",
    )
    env = make_env()

    result = run(e2e_discord_probe.run_discord_crud_probe(env, StateStore(env)))

    assert result["ok"] is True
    assert result["dirty"] is False
    assert any(call["method"] == "POST" for call in calls)


def test_discord_crud_probe_rejects_channel_outside_target_guild(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_discord_api_stub(
        monkeypatch,
        channel_guild_id="other-guild-id",
    )
    env = make_env()

    result = run(e2e_discord_probe.run_discord_crud_probe(env, StateStore(env)))

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "discord_target_channel_mismatch",
        "stages": {"target_guild": 200, "target_channel": 200},
    }
    assert [(call["method"], call["path"]) for call in calls] == [
        ("GET", "/guilds/guild-id"),
        ("GET", "/channels/channel-id"),
    ]
    assert run(StateStore(env).get_e2e_manifest("discord")) is None


def test_discord_crud_probe_requires_mentionable_role(monkeypatch) -> None:
    _, _, calls, _ = install_discord_api_stub(
        monkeypatch,
        role_mentionable=False,
    )
    env = make_env()

    result = run(e2e_discord_probe.run_discord_crud_probe(env, StateStore(env)))

    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["error"] == "discord_target_role_mismatch"
    assert not any(call["method"] == "POST" for call in calls)


def test_discord_cleanup_failure_blocks_next_run_and_recovers(monkeypatch) -> None:
    events, messages, calls, control = install_discord_api_stub(
        monkeypatch,
        event_delete_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)

    failed = run(e2e_discord_probe.run_discord_crud_probe(env, state))

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["cleanup"] == {"ok": False, "attempts": 4}
    assert messages == {}
    assert set(events) == {"event-id"}
    manifest = run(state.get_e2e_manifest("discord"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["event_id"] == "event-id"

    call_count = len(calls)
    blocked = run(e2e_discord_probe.run_discord_crud_probe(env, state))
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["event_delete_statuses"] = [204]
    recovered = run(e2e_discord_probe.cleanup_discord_crud_probe(env, state))

    assert recovered == {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
    assert events == {}
    clean_manifest = run(state.get_e2e_manifest("discord"))
    assert isinstance(clean_manifest, dict)
    assert clean_manifest["dirty"] is False
    assert "event_id" not in clean_manifest
    assert "message_id" not in clean_manifest


def test_confirmed_cleanup_rejects_run_marker_mismatch(monkeypatch) -> None:
    events, messages, calls, _ = install_discord_api_stub(monkeypatch)
    events["event-id"] = {
        "id": "event-id",
        "guild_id": "guild-id",
        "description": "[ie-event-bot-e2e:different-run]",
    }
    messages["message-id"] = {
        "id": "message-id",
        "channel_id": "channel-id",
        "content": "[ie-event-bot-e2e:different-run]",
    }
    env = make_env(
        manifests={
            "discord": {
                "version": 1,
                "kind": "discord_event_message",
                "dirty": True,
                "run_id": ROUTE_RUN_ID,
                "target_fingerprints": {
                    "guild_id_sha256": sha256(b"guild-id").hexdigest(),
                    "channel_id_sha256": sha256(b"channel-id").hexdigest(),
                },
                "event_id": "event-id",
                "message_id": "message-id",
            }
        }
    )

    result = run(e2e_discord_probe.cleanup_discord_crud_probe(env, StateStore(env)))

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "cleanup_target_mismatch"
    cleanup_calls = calls[-2:]
    assert [call["method"] for call in cleanup_calls] == ["GET", "GET"]
    assert not any(call["method"] == "DELETE" for call in cleanup_calls)
    assert "event-id" in events
    assert "message-id" in messages


def test_confirmed_cleanup_rejects_target_fingerprint_mismatch() -> None:
    env = make_env(
        manifests={
            "discord": {
                "version": 1,
                "kind": "discord_event_message",
                "dirty": True,
                "run_id": "run-id",
                "target_fingerprints": {
                    "guild_id_sha256": sha256(b"other-guild").hexdigest(),
                    "channel_id_sha256": sha256(b"other-channel").hexdigest(),
                },
            }
        }
    )

    result = run(e2e_discord_probe.cleanup_discord_crud_probe(env, StateStore(env)))

    assert result == {
        "ok": False,
        "dirty": True,
        "error": "dirty_manifest_target_mismatch",
    }


def test_discord_request_waits_for_retry_after(monkeypatch) -> None:
    responses = [
        Response(
            json.dumps({"retry_after": 0.25}),
            status=429,
            headers={"Retry-After": "0.25"},
        ),
        Response(json.dumps({"ok": True}), status=200),
    ]
    sleep_calls: list[float] = []
    request_options: list[dict] = []

    async def fake_fetch(url, options=None):
        request_options.append(dict(options or {}))
        return responses.pop(0)

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_discord_probe.asyncio, "sleep", fake_sleep)
    env = SimpleNamespace(DISCORD_TOKEN="bot-token")

    status, data, retries = run(
        e2e_discord_probe._discord_request(env, "GET", "/users/@me")
    )

    assert status == 200
    assert data == {"ok": True}
    assert retries == 1
    assert sleep_calls == [0.25]
    assert len(request_options) == 2
    assert request_options[0]["headers"]["User-Agent"].startswith("DiscordBot (")


def test_discord_request_rejects_excessive_retry_after(monkeypatch) -> None:
    async def fake_fetch(url, options=None):
        return Response(
            json.dumps({"retry_after": 3600}),
            status=429,
            headers={"Retry-After": "3600"},
        )

    async def fail_sleep(seconds: float):
        raise AssertionError("過大なRetry-Afterではsleepしない")

    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_discord_probe.asyncio, "sleep", fail_sleep)

    status, _, retries = run(
        e2e_discord_probe._discord_request(
            SimpleNamespace(DISCORD_TOKEN="bot-token"),
            "GET",
            "/users/@me",
        )
    )

    assert status == 429
    assert retries == 0


def test_discord_e2e_route_requires_auth_and_post(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        assert isinstance(run_id, str)
        calls.append(run_id)
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_discord_crud_probe", fake_probe)
    env = SimpleNamespace(
        E2E_DISCORD_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )
    worker = make_worker(env)

    unauthorized = run(
        worker.fetch(Request("https://bot.test/admin/e2e/discord-crud", method="POST"))
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-crud",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-crud",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert unauthorized.status == 401
    assert wrong_method.status == 405
    assert missing_run_id.status == 400
    assert response_json(missing_run_id) == {"ok": False, "error": "invalid_run_id"}
    assert success.status == 200
    assert response_json(success) == {"ok": True, "dirty": False}
    assert calls == [ROUTE_RUN_ID]


def test_discord_e2e_route_is_hidden_when_disabled() -> None:
    worker = make_worker(
        SimpleNamespace(
            E2E_DISCORD_CRUD_ENABLED="false",
            INTERNAL_API_TOKEN="test-token",
        )
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response) == {"ok": False, "error": "not_found"}
