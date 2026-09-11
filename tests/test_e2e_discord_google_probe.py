"""Discord→Google E2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse

from workers import Response

import discord_notion_sync
import e2e_discord_google_probe
import e2e_discord_probe
import e2e_entry
import e2e_google_probe
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


CALENDAR_ID = "e2e-calendar@example.com"
GUILD_ID = "guild-id"
DISCORD_EVENT_ID = "discord-event-id"
GOOGLE_EVENT_ID = "google-event-id"
RUN_ID = "E2E-20260902T040000Z-1234abcd"
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
        GOOGLE_API_BEARER_TOKEN="google-token",
        GOOGLE_CALENDAR_ID=CALENDAR_ID,
        DISCORD_TOKEN="bot-token",
        DISCORD_GUILD_ID=GUILD_ID,
        DISCORD_TO_GOOGLE_SYNC_ENABLED="false",
        DISCORD_SYNC_ENABLED="false",
        NOTION_TOKEN="notion-token",
        NOTION_EVENT_INTERNAL_ID="internal-database-must-not-be-used",
        NOTION_EVENT_ID="external-database-must-not-be-used",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(manifests),
    )


def install_api_stub(
    monkeypatch,
    *,
    lose_discord_create_response: bool = False,
    lose_google_create_response: bool = False,
    hide_google_query_results: bool = False,
    discord_delete_statuses: list[int] | None = None,
    google_delete_statuses: list[int] | None = None,
):
    discord_events: dict[str, dict] = {}
    google_events: dict[str, dict] = {}
    calls: list[tuple[str, str, str, str]] = []
    control = {
        "lose_discord_create_response": lose_discord_create_response,
        "lose_google_create_response": lose_google_create_response,
        "hide_google_query_results": hide_google_query_results,
        "discord_delete_statuses": list(discord_delete_statuses or [204]),
        "google_delete_statuses": list(google_delete_statuses or [204]),
    }
    encoded_calendar = quote(CALENDAR_ID, safe="")

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        payload = json.loads(str(request.get("body") or "{}"))
        calls.append((method, parsed.netloc, parsed.path, parsed.query))

        if parsed.netloc == "discord.com":
            path = parsed.path.removeprefix("/api/v10")
            if method == "GET" and path == f"/guilds/{GUILD_ID}":
                return Response(json.dumps({"id": GUILD_ID}), status=200)

            collection = f"/guilds/{GUILD_ID}/scheduled-events"
            if path == collection and method == "POST":
                event = {
                    **json.loads(json.dumps(payload)),
                    "id": DISCORD_EVENT_ID,
                    "guild_id": GUILD_ID,
                    "creator_id": "creator-id",
                    "status": 1,
                }
                discord_events[DISCORD_EVENT_ID] = event
                if control["lose_discord_create_response"]:
                    control["lose_discord_create_response"] = False
                    raise RuntimeError("response lost after Discord create")
                return Response(json.dumps(event), status=200)
            if path == collection and method == "GET":
                return Response(json.dumps(list(discord_events.values())), status=200)
            if path.startswith(f"{collection}/"):
                event_id = path.rsplit("/", 1)[-1]
                if method == "GET":
                    event = discord_events.get(event_id)
                    return Response(
                        "" if event is None else json.dumps(event),
                        status=404 if event is None else 200,
                    )
                if method == "DELETE":
                    statuses = control["discord_delete_statuses"]
                    status = statuses.pop(0) if statuses else 204
                    if status < 300:
                        discord_events.pop(event_id, None)
                    return Response("", status=status)
            raise AssertionError(f"想定外のDiscord API呼び出し: {method} {path}")

        if parsed.netloc == "www.googleapis.com":
            calendar_path = f"/calendar/v3/calendars/{encoded_calendar}"
            collection = f"{calendar_path}/events"
            if method == "GET" and parsed.path == calendar_path:
                return Response(json.dumps({"id": CALENDAR_ID}), status=200)
            if parsed.path == collection and method == "POST":
                event = {
                    **json.loads(json.dumps(payload)),
                    "id": GOOGLE_EVENT_ID,
                    "status": "confirmed",
                }
                google_events[GOOGLE_EVENT_ID] = event
                if control["lose_google_create_response"]:
                    control["lose_google_create_response"] = False
                    raise RuntimeError("response lost after Google create")
                return Response(json.dumps(event), status=200)
            if parsed.path == collection and method == "GET":
                query = parse_qs(parsed.query)
                marker = str((query.get("privateExtendedProperty") or [""])[0])
                expected_source = marker.removeprefix("ie_discord_event_id=")
                matches = [
                    event
                    for event in google_events.values()
                    if event.get("status") != "cancelled"
                    and str(
                        ((event.get("extendedProperties") or {}).get("private") or {}).get(
                            "ie_discord_event_id"
                        )
                        or ""
                    )
                    == expected_source
                ]
                if control["hide_google_query_results"]:
                    matches = []
                return Response(
                    json.dumps({"kind": "calendar#events", "items": matches}),
                    status=200,
                )
            if parsed.path.startswith(f"{collection}/"):
                event_id = parsed.path.rsplit("/", 1)[-1]
                if method == "GET":
                    event = google_events.get(event_id)
                    return Response(
                        "" if event is None else json.dumps(event),
                        status=404 if event is None else 200,
                    )
                if method == "DELETE":
                    statuses = control["google_delete_statuses"]
                    status = statuses.pop(0) if statuses else 204
                    if status < 300:
                        google_events.pop(event_id, None)
                    return Response("", status=status)
            raise AssertionError(
                f"想定外のGoogle API呼び出し: {method} {parsed.path}"
            )

        if parsed.netloc == "api.notion.com":
            raise AssertionError("Discord→Google scenarioはNotionを呼び出してはならない")
        raise AssertionError(f"想定外の外部API呼び出し: {method} {parsed.netloc}")

    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_google_probe, "fetch", fake_fetch)
    monkeypatch.setattr(discord_notion_sync, "fetch", fake_fetch)
    return discord_events, google_events, calls, control


def test_discord_google_probe_applies_and_cleans_both_resources(monkeypatch) -> None:
    discord_events, google_events, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)

    result = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(env, state, RUN_ID)
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"] == {
        "target_discord_guild": 200,
        "target_google_calendar": 200,
        "discord_precheck": 200,
        "discord_create": 200,
        "discord_read": 200,
        "google_precheck": 200,
        "application_apply": 200,
        "google_find": 200,
        "google_read": 200,
        "google_cleanup_verify": 200,
        "google_delete": 204,
        "discord_cleanup_verify": 200,
        "discord_delete": 204,
    }
    assert discord_events == {}
    assert google_events == {}
    assert env.STATE_KV.put_calls == []
    assert not any(host == "api.notion.com" for _, host, _, _ in calls)

    manifest = run(state.get_e2e_manifest("discord_google"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "discord_google_sync"
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "guild_id_sha256",
        "discord_event_id_sha256",
        "google_event_id_sha256",
    }
    assert "discord_event_id" not in manifest
    assert "google_event_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_discord_google_probe_rejects_unsafe_configuration_before_io(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    env.DISCORD_TO_GOOGLE_SYNC_ENABLED = "true"

    enabled = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert enabled == {
        "ok": False,
        "dirty": False,
        "error": "discord_to_google_sync_must_be_disabled_before_probe",
    }
    assert calls == []

    env.DISCORD_TO_GOOGLE_SYNC_ENABLED = "false"
    env.GOOGLE_CALENDAR_ID = "primary"
    primary = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(
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


def test_discord_google_probe_reconciles_lost_discord_create_response(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_api_stub(
        monkeypatch,
        lose_discord_create_response=True,
    )
    env = make_env()

    result = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["stages"]["discord_create"] == 0
    assert result["stages"]["discord_create_reconcile"] == 200
    assert sum(
        method == "POST" and path.endswith(f"/guilds/{GUILD_ID}/scheduled-events")
        for method, _, path, _ in calls
    ) == 1


def test_discord_google_unresolved_google_create_stays_dirty_and_recovers(
    monkeypatch,
) -> None:
    discord_events, google_events, calls, control = install_api_stub(
        monkeypatch,
        lose_google_create_response=True,
        hide_google_query_results=True,
    )
    env = make_env()
    state = StateStore(env)

    failed = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(env, state, RUN_ID)
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "google_event_ownership_unresolved"
    assert discord_events == {}
    assert set(google_events) == {GOOGLE_EVENT_ID}
    manifest = run(state.get_e2e_manifest("discord_google"))
    assert isinstance(manifest, dict)
    assert manifest["create_attempted"]["google_event"] is True

    call_count = len(calls)
    blocked = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(env, state, RUN_ID)
    )
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["hide_google_query_results"] = False
    recovered = run(
        e2e_discord_google_probe.cleanup_discord_google_sync_probe(
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
    assert google_events == {}


def test_discord_google_cleanup_rejects_google_summary_mismatch(monkeypatch) -> None:
    _, google_events, calls, control = install_api_stub(
        monkeypatch,
        google_delete_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    google_events[GOOGLE_EVENT_ID]["summary"] = "different owner"
    control["google_delete_statuses"] = [204]
    call_count = len(calls)
    cleanup = run(
        e2e_discord_google_probe.cleanup_discord_google_sync_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert cleanup["ok"] is False
    assert cleanup["dirty"] is True
    assert cleanup["error"] == "cleanup_target_mismatch"
    assert set(google_events) == {GOOGLE_EVENT_ID}
    cleanup_calls = calls[call_count:]
    assert not any(
        method == "DELETE" and path.endswith(f"/events/{GOOGLE_EVENT_ID}")
        for method, _, path, _ in cleanup_calls
    )


def test_discord_google_cleanup_rejects_discord_name_mismatch(monkeypatch) -> None:
    discord_events, _, _, control = install_api_stub(
        monkeypatch,
        discord_delete_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_discord_google_probe.run_discord_google_sync_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    discord_events[DISCORD_EVENT_ID]["name"] = "different owner"
    control["discord_delete_statuses"] = [204]
    cleanup = run(
        e2e_discord_google_probe.cleanup_discord_google_sync_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert cleanup["ok"] is False
    assert cleanup["dirty"] is True
    assert cleanup["error"] == "cleanup_target_mismatch"
    assert set(discord_events) == {DISCORD_EVENT_ID}


def test_discord_google_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_discord_google_sync_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_DISCORD_GOOGLE_SYNC_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-google-sync",
                method="POST",
            )
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-google-sync",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-google-sync",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-google-sync",
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


def test_discord_google_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_DISCORD_GOOGLE_SYNC_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-google-sync",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response)["error"] == "not_found"
