"""前日リマインドE2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import e2e_discord_probe
import e2e_entry
import e2e_reminder_probe
import jobs
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


GUILD_ID = "guild-id"
CHANNEL_ID = "reminder-channel-id"
ROLE_ID = "reminder-role-id"
EVENT_ID = "reminder-event-id"
MESSAGE_ID = "reminder-message-id"
RUN_ID = "E2E-20260902T070000Z-1234abcd"
ROUTE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-E2E-Run-ID": RUN_ID,
}


def run(coroutine):
    return asyncio.run(coroutine)


def response_json(response: Response) -> dict:
    return json.loads(run(response.text()))


def make_env(*, manifests: dict[str, dict] | None = None):
    return SimpleNamespace(
        DISCORD_TOKEN="bot-token",
        DISCORD_GUILD_ID=GUILD_ID,
        REMINDER_CHANNEL_ID=CHANNEL_ID,
        REMINDER_ROLE_ID=ROLE_ID,
        REMINDER_WINDOW_MINUTES="15",
        CRON_ENABLE_REMINDER="false",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(manifests),
    )


def install_discord_api_stub(
    monkeypatch,
    *,
    lose_event_create_response: bool = False,
    lose_message_create_response: bool = False,
    hide_event_search_results: bool = False,
    hide_message_search_results: bool = False,
):
    events: dict[str, dict] = {}
    messages: dict[str, dict] = {}
    calls: list[dict] = []
    control = {
        "lose_event_create_response": lose_event_create_response,
        "lose_message_create_response": lose_message_create_response,
        "hide_event_search_results": hide_event_search_results,
        "hide_message_search_results": hide_message_search_results,
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
            }
        )

        if method == "GET" and path == f"/guilds/{GUILD_ID}":
            return Response(json.dumps({"id": GUILD_ID}), status=200)
        if method == "GET" and path == f"/channels/{CHANNEL_ID}":
            return Response(
                json.dumps(
                    {
                        "id": CHANNEL_ID,
                        "guild_id": GUILD_ID,
                        "type": 0,
                    }
                ),
                status=200,
            )
        if method == "GET" and path == f"/guilds/{GUILD_ID}/roles":
            return Response(
                json.dumps(
                    [
                        {
                            "id": ROLE_ID,
                            "mentionable": True,
                        }
                    ]
                ),
                status=200,
            )

        event_collection = f"/guilds/{GUILD_ID}/scheduled-events"
        if method == "GET" and path == event_collection:
            visible = [] if control["hide_event_search_results"] else list(events.values())
            return Response(json.dumps(visible), status=200)
        if method == "POST" and path == event_collection:
            event = {
                **payload,
                "id": EVENT_ID,
                "guild_id": GUILD_ID,
                "status": 1,
            }
            events[EVENT_ID] = event
            if control["lose_event_create_response"]:
                control["lose_event_create_response"] = False
                raise RuntimeError("response lost after event create")
            return Response(json.dumps(event), status=200)
        if path.startswith(f"{event_collection}/"):
            event_id = path.rsplit("/", 1)[-1]
            event = events.get(event_id)
            if method == "GET":
                return Response(
                    "" if event is None else json.dumps(event),
                    status=404 if event is None else 200,
                )
            if method == "DELETE":
                events.pop(event_id, None)
                return Response("", status=204)

        message_collection = f"/channels/{CHANNEL_ID}/messages"
        if method == "GET" and path == message_collection:
            visible = [] if control["hide_message_search_results"] else list(messages.values())
            return Response(json.dumps(visible), status=200)
        if method == "POST" and path == message_collection:
            allowed_mentions = payload.get("allowed_mentions") or {}
            message = {
                "id": MESSAGE_ID,
                "channel_id": CHANNEL_ID,
                "content": str(payload.get("content") or ""),
                "mention_roles": list(allowed_mentions.get("roles") or []),
                "mention_everyone": False,
            }
            messages[MESSAGE_ID] = message
            if control["lose_message_create_response"]:
                control["lose_message_create_response"] = False
                raise RuntimeError("response lost after message create")
            return Response(json.dumps(message), status=200)
        if path.startswith(f"{message_collection}/"):
            message_id = path.rsplit("/", 1)[-1]
            message = messages.get(message_id)
            if method == "GET":
                return Response(
                    "" if message is None else json.dumps(message),
                    status=404 if message is None else 200,
                )
            if method == "DELETE":
                messages.pop(message_id, None)
                return Response("", status=204)

        raise AssertionError(f"想定外のDiscord API呼び出し: {method} {path}")

    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(jobs, "fetch", fake_fetch)
    return events, messages, calls, control


def test_reminder_probe_notifies_once_and_cleans_owned_resources(monkeypatch) -> None:
    events, messages, calls, _ = install_discord_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)

    result = run(e2e_reminder_probe.run_reminder_probe(env, state, RUN_ID))

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"]["job_notify"] == 200
    assert result["stages"]["job_duplicate_suppression"] == 200
    assert result["stages"]["discord_message_read"] == 200
    assert events == {}
    assert messages == {}
    assert env.STATE_KV.data == {}
    assert env.STATE_KV.put_calls == []

    event_posts = [
        call
        for call in calls
        if call["method"] == "POST"
        and call["path"] == f"/guilds/{GUILD_ID}/scheduled-events"
    ]
    assert len(event_posts) == 1
    event_payload = event_posts[0]["payload"]
    assert event_payload["channel_id"] is None
    assert event_payload["privacy_level"] == 2
    assert event_payload["entity_type"] == 3
    assert event_payload["entity_metadata"] == {"location": "E2E reminder fixture"}
    assert f"[ie-event-bot-e2e:{RUN_ID}]" in event_payload["name"]
    assert f"[ie-event-bot-e2e:{RUN_ID}]" in event_payload["description"]

    message_posts = [
        call
        for call in calls
        if call["method"] == "POST"
        and call["path"] == f"/channels/{CHANNEL_ID}/messages"
    ]
    assert len(message_posts) == 1
    assert message_posts[0]["payload"]["allowed_mentions"] == {
        "parse": [],
        "roles": [ROLE_ID],
        "replied_user": False,
    }
    assert message_posts[0]["payload"]["content"].startswith(
        f"<@&{ROLE_ID}>\n🔔 明日開催のイベントがあります"
    )
    assert f"[ie-event-bot-e2e:{RUN_ID}]" in message_posts[0]["payload"]["content"]

    manifest = run(state.get_e2e_manifest("reminder"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "day_before_reminder"
    assert manifest["dirty"] is False
    assert manifest["last_run_id"] == RUN_ID
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


def test_reminder_probe_reconciles_lost_event_create_response(monkeypatch) -> None:
    _, _, calls, _ = install_discord_api_stub(
        monkeypatch,
        lose_event_create_response=True,
    )
    env = make_env()

    result = run(
        e2e_reminder_probe.run_reminder_probe(env, StateStore(env), RUN_ID)
    )

    assert result["ok"] is True
    assert result["stages"]["discord_event_create"] == 0
    assert result["stages"]["discord_event_create_reconcile"] == 200
    assert len(
        [
            call
            for call in calls
            if call["method"] == "POST"
            and call["path"] == f"/guilds/{GUILD_ID}/scheduled-events"
        ]
    ) == 1


def test_reminder_unresolved_message_stays_dirty_and_recovers(monkeypatch) -> None:
    events, messages, _, control = install_discord_api_stub(
        monkeypatch,
        lose_message_create_response=True,
        hide_message_search_results=True,
    )
    env = make_env()
    state = StateStore(env)

    failed = run(e2e_reminder_probe.run_reminder_probe(env, state, RUN_ID))

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "discord_message_ownership_unresolved"
    assert set(messages) == {MESSAGE_ID}
    assert events == {}
    manifest = run(state.get_e2e_manifest("reminder"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["create_attempted"]["message"] is True

    blocked = run(e2e_reminder_probe.run_reminder_probe(env, state, RUN_ID))
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }

    control["hide_message_search_results"] = False
    recovered = run(
        e2e_reminder_probe.cleanup_reminder_probe(
            env,
            state,
            expected_run_id=RUN_ID,
        )
    )
    assert recovered == {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
    assert messages == {}
    clean_manifest = run(state.get_e2e_manifest("reminder"))
    assert isinstance(clean_manifest, dict)
    assert clean_manifest["dirty"] is False


def test_reminder_cleanup_rejects_message_owner_mismatch(monkeypatch) -> None:
    _, messages, calls, _ = install_discord_api_stub(monkeypatch)
    messages[MESSAGE_ID] = {
        "id": MESSAGE_ID,
        "channel_id": CHANNEL_ID,
        "content": "[ie-event-bot-e2e:different-run]",
        "mention_roles": [ROLE_ID],
        "mention_everyone": False,
    }
    env = make_env(
        manifests={
            "reminder": {
                "version": 1,
                "kind": "day_before_reminder",
                "dirty": True,
                "run_id": RUN_ID,
                "target_fingerprints": {
                    "guild_id_sha256": sha256(GUILD_ID.encode()).hexdigest(),
                    "channel_id_sha256": sha256(CHANNEL_ID.encode()).hexdigest(),
                    "role_id_sha256": sha256(ROLE_ID.encode()).hexdigest(),
                },
                "create_attempted": {"event": False, "message": True},
                "message_id": MESSAGE_ID,
                "stages": {},
            }
        }
    )

    result = run(
        e2e_reminder_probe.cleanup_reminder_probe(
            env,
            StateStore(env),
            expected_run_id=RUN_ID,
        )
    )

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "cleanup_target_mismatch"
    assert MESSAGE_ID in messages
    cleanup_calls = calls[-1:]
    assert [call["method"] for call in cleanup_calls] == ["GET"]
    assert not any(call["method"] == "DELETE" for call in cleanup_calls)


def test_reminder_probe_rejects_enabled_cron_before_io(monkeypatch) -> None:
    _, _, calls, _ = install_discord_api_stub(monkeypatch)
    env = make_env()
    env.CRON_ENABLE_REMINDER = "true"

    result = run(
        e2e_reminder_probe.run_reminder_probe(env, StateStore(env), RUN_ID)
    )

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "cron_reminder_must_be_disabled",
    }
    assert calls == []


def test_reminder_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False, "run_id": run_id}

    monkeypatch.setattr(e2e_entry, "run_reminder_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_REMINDER_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(Request("https://bot.test/admin/e2e/reminder", method="POST"))
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/reminder",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/reminder",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/reminder",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert unauthorized.status == 401
    assert wrong_method.status == 405
    assert missing_run_id.status == 400
    assert success.status == 200
    assert response_json(success)["run_id"] == RUN_ID
    assert calls == [RUN_ID]


def test_reminder_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_REMINDER_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/reminder",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
