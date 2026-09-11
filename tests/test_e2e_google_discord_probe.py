"""Google→Discord E2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from workers import Response

import e2e_discord_probe
import e2e_entry
import e2e_google_discord_probe
import e2e_google_probe
import google_apply_sync
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


CALENDAR_ID = "calendar@example.test"
RUN_ID = "E2E-20260901T000000Z-1234abcd"
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
        GOOGLE_CALENDAR_ID=CALENDAR_ID,
        DISCORD_TOKEN="bot-token",
        DISCORD_GUILD_ID="guild-id",
        DISCORD_SYNC_ENABLED="false",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(manifests),
    )


def install_api_stub(
    monkeypatch,
    *,
    lose_discord_create_response: bool = False,
    hide_discord_search_results: bool = False,
    discord_delete_statuses: list[int] | None = None,
):
    google_events: dict[str, dict] = {}
    discord_events: dict[str, dict] = {}
    calls: list[tuple[str, str]] = []
    control = {
        "lose_discord_create_response": lose_discord_create_response,
        "hide_discord_search_results": hide_discord_search_results,
        "discord_delete_statuses": list(discord_delete_statuses or [204]),
    }

    async def fake_access_token(env, state):
        return "access-token"

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        payload = json.loads(str(request.get("body") or "{}"))
        calls.append((method, f"{parsed.netloc}{parsed.path}"))

        if parsed.netloc == "www.googleapis.com":
            if method == "POST":
                event = json.loads(json.dumps(payload))
                google_events[str(event["id"])] = event
                return Response(json.dumps(event), status=200)
            event_id = unquote(parsed.path.split("/events/", 1)[1])
            if method == "GET":
                event = google_events.get(event_id)
                status = 404 if event is None else 200
                return Response("" if event is None else json.dumps(event), status=status)
            if method == "DELETE":
                google_events.pop(event_id, None)
                return Response("", status=204)
            raise AssertionError(f"想定外のGoogle API呼び出し: {method} {parsed.path}")

        path = parsed.path.removeprefix("/api/v10")
        if method == "GET" and path == "/guilds/guild-id":
            return Response(json.dumps({"id": "guild-id"}), status=200)

        collection = "/guilds/guild-id/scheduled-events"
        if path == collection and method == "POST":
            event = {
                **json.loads(json.dumps(payload)),
                "id": "discord-event-id",
                "guild_id": "guild-id",
                "status": 1,
            }
            discord_events["discord-event-id"] = event
            if control["lose_discord_create_response"]:
                control["lose_discord_create_response"] = False
                raise RuntimeError("response lost after create")
            return Response(json.dumps(event), status=200)
        if path == collection and method == "GET":
            events = (
                []
                if control["hide_discord_search_results"]
                else list(discord_events.values())
            )
            return Response(json.dumps(events), status=200)
        if path.startswith(f"{collection}/"):
            event_id = path.rsplit("/", 1)[-1]
            if method == "GET":
                event = discord_events.get(event_id)
                status = 404 if event is None else 200
                return Response("" if event is None else json.dumps(event), status=status)
            if method == "DELETE":
                statuses = control["discord_delete_statuses"]
                status = statuses.pop(0) if statuses else 204
                if status < 300:
                    discord_events.pop(event_id, None)
                return Response("", status=status)

        raise AssertionError(f"想定外のDiscord API呼び出し: {method} {path}")

    monkeypatch.setattr(e2e_google_discord_probe, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(e2e_google_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(google_apply_sync, "fetch", fake_fetch)
    return google_events, discord_events, calls, control


def test_google_discord_probe_applies_and_cleans_both_resources(monkeypatch) -> None:
    google_events, discord_events, calls, _ = install_api_stub(monkeypatch)
    env = make_env()

    result = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"] == {
        "target_discord_guild": 200,
        "discord_precheck": 200,
        "google_create": 200,
        "google_read": 200,
        "application_apply": 200,
        "discord_read": 200,
        "discord_cleanup_verify": 200,
        "discord_delete": 204,
        "google_delete": 204,
    }
    assert google_events == {}
    assert discord_events == {}
    assert env.STATE_KV.put_calls == []
    assert sum(
        method == "POST" and path.endswith("/guilds/guild-id/scheduled-events")
        for method, path in calls
    ) == 1

    manifest = run(StateStore(env).get_e2e_manifest("google_discord"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "guild_id_sha256",
        "google_event_id_sha256",
        "discord_event_id_sha256",
    }
    assert "google_event_id" not in manifest
    assert "discord_event_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_google_discord_probe_rejects_unsafe_configuration_before_io(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    env.DISCORD_SYNC_ENABLED = "true"

    enabled = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert enabled == {
        "ok": False,
        "dirty": False,
        "error": "discord_sync_must_be_disabled",
    }
    assert calls == []

    env.DISCORD_SYNC_ENABLED = "false"
    env.GOOGLE_CALENDAR_ID = "primary"
    primary = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert primary == {
        "ok": False,
        "dirty": False,
        "error": "primary_calendar_forbidden",
    }
    assert calls == []


def test_google_discord_probe_reconciles_lost_create_response(monkeypatch) -> None:
    _, _, calls, _ = install_api_stub(
        monkeypatch,
        lose_discord_create_response=True,
    )
    env = make_env()

    result = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["stages"]["application_apply"] == 0
    assert result["stages"]["discord_create_reconcile"] == 200
    assert sum(
        method == "POST" and path.endswith("/guilds/guild-id/scheduled-events")
        for method, path in calls
    ) == 1


def test_google_discord_unresolved_create_stays_dirty_and_recovers(
    monkeypatch,
) -> None:
    google_events, discord_events, calls, control = install_api_stub(
        monkeypatch,
        lose_discord_create_response=True,
        hide_discord_search_results=True,
    )
    env = make_env()
    state = StateStore(env)

    failed = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(env, state, RUN_ID)
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "discord_event_ownership_unresolved"
    assert google_events == {}
    assert set(discord_events) == {"discord-event-id"}
    manifest = run(state.get_e2e_manifest("google_discord"))
    assert isinstance(manifest, dict)
    assert manifest["create_attempted"]["discord_event"] is True

    call_count = len(calls)
    blocked = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(env, state, RUN_ID)
    )
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["hide_discord_search_results"] = False
    recovered = run(
        e2e_google_discord_probe.cleanup_google_discord_sync_probe(
            env,
            state,
            RUN_ID,
        )
    )
    assert recovered == {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
    assert discord_events == {}


def test_google_discord_cleanup_rejects_event_name_mismatch(monkeypatch) -> None:
    _, discord_events, _, control = install_api_stub(
        monkeypatch,
        discord_delete_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_google_discord_probe.run_google_discord_sync_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    discord_events["discord-event-id"]["name"] = "different owner"
    control["discord_delete_statuses"] = [204]
    cleanup = run(
        e2e_google_discord_probe.cleanup_google_discord_sync_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert cleanup["ok"] is False
    assert cleanup["dirty"] is True
    assert cleanup["error"] == "cleanup_target_mismatch"
    assert set(discord_events) == {"discord-event-id"}


def test_google_discord_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_discord_sync_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_GOOGLE_DISCORD_SYNC_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-discord-sync",
                method="POST",
            )
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-discord-sync",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-discord-sync",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-discord-sync",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert unauthorized.status == 401
    assert wrong_method.status == 405
    assert missing_run_id.status == 400
    assert success.status == 200
    assert response_json(success)["ok"] is True
    assert calls == [RUN_ID]


def test_google_discord_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_GOOGLE_DISCORD_SYNC_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-discord-sync",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response)["error"] == "not_found"
