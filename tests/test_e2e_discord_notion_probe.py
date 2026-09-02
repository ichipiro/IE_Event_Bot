"""Discord→Notion E2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import discord_notion_sync
import e2e_discord_notion_probe
import e2e_discord_probe
import e2e_entry
import e2e_notion_probe
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


GUILD_ID = "guild-id"
EVENT_DATABASE_ID = "11111111-1111-4111-8111-111111111111"
DISCORD_EVENT_ID = "discord-event-id"
PAGE_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "E2E-20260902T000000Z-1234abcd"
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
        DISCORD_TO_GOOGLE_SYNC_ENABLED="false",
        NOTION_TOKEN="notion-token",
        NOTION_EVENT_INTERNAL_ID=EVENT_DATABASE_ID,
        NOTION_EVENT_ID="",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(manifests),
    )


def _schema(properties: dict[str, str]) -> dict[str, dict]:
    return {
        name: {
            "id": f"property-{index}",
            "name": name,
            "type": property_type,
            property_type: {},
        }
        for index, (name, property_type) in enumerate(properties.items(), start=1)
    }


def _property_text(page: dict, property_name: str, property_type: str) -> str:
    prop = (page.get("properties") or {}).get(property_name) or {}
    nodes = prop.get(property_type) or []
    if not nodes:
        return ""
    node = nodes[0] or {}
    return str(node.get("plain_text") or ((node.get("text") or {}).get("content") or ""))


def install_api_stub(
    monkeypatch,
    *,
    lose_discord_create_response: bool = False,
    lose_notion_create_response: bool = False,
    hide_notion_query_results: bool = False,
    discord_delete_statuses: list[int] | None = None,
    notion_archive_statuses: list[int] | None = None,
):
    discord_events: dict[str, dict] = {}
    notion_pages: dict[str, dict] = {}
    calls: list[tuple[str, str]] = []
    control = {
        "lose_discord_create_response": lose_discord_create_response,
        "lose_notion_create_response": lose_notion_create_response,
        "hide_notion_query_results": hide_notion_query_results,
        "discord_delete_statuses": list(discord_delete_statuses or [204]),
        "notion_archive_statuses": list(notion_archive_statuses or [200]),
    }

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        payload = json.loads(str(request.get("body") or "{}"))
        calls.append((method, f"{parsed.netloc}{parsed.path}"))

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
                    raise RuntimeError("response lost after create")
                return Response(json.dumps(event), status=200)
            if path == collection and method == "GET":
                return Response(json.dumps(list(discord_events.values())), status=200)
            if path.startswith(f"{collection}/"):
                event_id = path.rsplit("/", 1)[-1]
                if method == "GET":
                    event = discord_events.get(event_id)
                    status = 404 if event is None else 200
                    return Response(
                        "" if event is None else json.dumps(event),
                        status=status,
                    )
                if method == "DELETE":
                    statuses = control["discord_delete_statuses"]
                    status = statuses.pop(0) if statuses else 204
                    if status < 300:
                        discord_events.pop(event_id, None)
                    return Response("", status=status)
            raise AssertionError(f"想定外のDiscord API呼び出し: {method} {path}")

        if parsed.netloc == "api.notion.com":
            path = parsed.path.removeprefix("/v1")
            if method == "GET" and path == f"/databases/{EVENT_DATABASE_ID}":
                return Response(
                    json.dumps(
                        {
                            "object": "database",
                            "id": EVENT_DATABASE_ID,
                            "properties": _schema(e2e_notion_probe._EVENT_SCHEMA),
                        }
                    ),
                    status=200,
                )
            if method == "POST" and path == f"/databases/{EVENT_DATABASE_ID}/query":
                filter_data = payload.get("filter") or {}
                property_name = str(filter_data.get("property") or "")
                rich_text = filter_data.get("rich_text") or {}
                expected = str(rich_text.get("equals") or rich_text.get("contains") or "")
                matches = [
                    page
                    for page in notion_pages.values()
                    if not page.get("archived")
                    and expected in _property_text(page, property_name, "rich_text")
                ]
                if control["hide_notion_query_results"]:
                    matches = []
                return Response(
                    json.dumps({"object": "list", "results": matches, "has_more": False}),
                    status=200,
                )
            if method == "POST" and path == "/pages":
                page = {
                    "object": "page",
                    "id": PAGE_ID,
                    "parent": {"type": "database_id", "database_id": EVENT_DATABASE_ID},
                    "archived": False,
                    "properties": json.loads(json.dumps(payload.get("properties") or {})),
                }
                notion_pages[PAGE_ID] = page
                if control["lose_notion_create_response"]:
                    control["lose_notion_create_response"] = False
                    raise RuntimeError("response lost after create")
                return Response(json.dumps(page), status=200)
            if path == f"/pages/{PAGE_ID}" and method == "GET":
                page = notion_pages.get(PAGE_ID)
                return Response(
                    "" if page is None else json.dumps(page),
                    status=404 if page is None else 200,
                )
            if path == f"/pages/{PAGE_ID}" and method == "PATCH":
                page = notion_pages.get(PAGE_ID)
                if page is None:
                    return Response("", status=404)
                if payload.get("archived") is True:
                    statuses = control["notion_archive_statuses"]
                    status = statuses.pop(0) if statuses else 200
                    if status >= 300:
                        return Response(
                            json.dumps({"code": "internal_server_error"}),
                            status=status,
                        )
                    page["archived"] = True
                    return Response(json.dumps(page), status=status)
                page["properties"].update(
                    json.loads(json.dumps(payload.get("properties") or {}))
                )
                return Response(json.dumps(page), status=200)
            raise AssertionError(f"想定外のNotion API呼び出し: {method} {path}")

        raise AssertionError(f"想定外の外部API呼び出し: {method} {parsed.netloc}")

    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    monkeypatch.setattr(discord_notion_sync, "fetch", fake_fetch)
    return discord_events, notion_pages, calls, control


def test_discord_notion_probe_applies_and_cleans_both_resources(monkeypatch) -> None:
    discord_events, notion_pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)

    result = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(env, state, RUN_ID)
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"] == {
        "target_discord_guild": 200,
        "target_notion_database": 200,
        "discord_precheck": 200,
        "discord_create": 200,
        "discord_read": 200,
        "notion_precheck": 200,
        "application_apply": 200,
        "notion_find": 200,
        "notion_read": 200,
        "notion_cleanup_verify": 200,
        "notion_archive": 200,
        "discord_cleanup_verify": 200,
        "discord_delete": 204,
    }
    assert discord_events == {}
    assert notion_pages[PAGE_ID]["archived"] is True
    assert env.STATE_KV.put_calls == []
    assert not any("googleapis.com" in path for _, path in calls)

    manifest = run(state.get_e2e_manifest("discord_notion"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "discord_notion_sync"
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "guild_id_sha256",
        "notion_database_id_sha256",
        "discord_event_id_sha256",
        "notion_page_id_sha256",
    }
    assert "discord_event_id" not in manifest
    assert "notion_page_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_discord_notion_probe_rejects_unmanaged_destinations_before_io(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    env.DISCORD_TO_GOOGLE_SYNC_ENABLED = "true"

    google = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert google == {
        "ok": False,
        "dirty": False,
        "error": "discord_to_google_sync_must_be_disabled",
    }
    assert calls == []

    env.DISCORD_TO_GOOGLE_SYNC_ENABLED = "false"
    env.NOTION_EVENT_ID = "external-database"
    external = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert external == {
        "ok": False,
        "dirty": False,
        "error": "external_notion_database_must_be_disabled",
    }
    assert calls == []

    env.NOTION_EVENT_ID = ""
    env.NOTION_PROP_TITLE = "custom-title"
    override = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert override == {
        "ok": False,
        "dirty": False,
        "error": "notion_property_overrides_forbidden",
    }
    assert calls == []


def test_discord_notion_probe_reconciles_lost_discord_create_response(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_api_stub(
        monkeypatch,
        lose_discord_create_response=True,
    )
    env = make_env()

    result = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(
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
        for method, path in calls
    ) == 1


def test_discord_notion_unresolved_notion_create_stays_dirty_and_recovers(
    monkeypatch,
) -> None:
    discord_events, notion_pages, calls, control = install_api_stub(
        monkeypatch,
        lose_notion_create_response=True,
        hide_notion_query_results=True,
    )
    env = make_env()
    state = StateStore(env)

    failed = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(env, state, RUN_ID)
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "notion_page_ownership_unresolved"
    assert discord_events == {}
    assert notion_pages[PAGE_ID]["archived"] is False
    manifest = run(state.get_e2e_manifest("discord_notion"))
    assert isinstance(manifest, dict)
    assert manifest["create_attempted"]["notion_page"] is True

    call_count = len(calls)
    blocked = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(env, state, RUN_ID)
    )
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["hide_notion_query_results"] = False
    recovered = run(
        e2e_discord_notion_probe.cleanup_discord_notion_sync_probe(
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
    assert notion_pages[PAGE_ID]["archived"] is True


def test_discord_notion_cleanup_rejects_notion_title_mismatch(monkeypatch) -> None:
    _, notion_pages, calls, control = install_api_stub(
        monkeypatch,
        notion_archive_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    notion_pages[PAGE_ID]["properties"]["イベント名"] = {
        "title": [{"text": {"content": "different owner"}}],
    }
    control["notion_archive_statuses"] = [200]
    call_count = len(calls)
    cleanup = run(
        e2e_discord_notion_probe.cleanup_discord_notion_sync_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert cleanup["ok"] is False
    assert cleanup["dirty"] is True
    assert cleanup["error"] == "cleanup_target_mismatch"
    assert notion_pages[PAGE_ID]["archived"] is False
    cleanup_calls = calls[call_count:]
    assert not any(
        method == "PATCH" and path.endswith(f"/pages/{PAGE_ID}")
        for method, path in cleanup_calls
    )


def test_discord_notion_cleanup_rejects_discord_name_mismatch(monkeypatch) -> None:
    discord_events, _, _, control = install_api_stub(
        monkeypatch,
        discord_delete_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_discord_notion_probe.run_discord_notion_sync_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    discord_events[DISCORD_EVENT_ID]["name"] = "different owner"
    control["discord_delete_statuses"] = [204]
    cleanup = run(
        e2e_discord_notion_probe.cleanup_discord_notion_sync_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert cleanup["ok"] is False
    assert cleanup["dirty"] is True
    assert cleanup["error"] == "cleanup_target_mismatch"
    assert set(discord_events) == {DISCORD_EVENT_ID}


def test_discord_notion_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_discord_notion_sync_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_DISCORD_NOTION_SYNC_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-notion-sync",
                method="POST",
            )
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-notion-sync",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-notion-sync",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-notion-sync",
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


def test_discord_notion_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_DISCORD_NOTION_SYNC_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-notion-sync",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response)["error"] == "not_found"
