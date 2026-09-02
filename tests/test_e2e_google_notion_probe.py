"""Google→Notion E2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from workers import Response

import e2e_entry
import e2e_google_notion_probe
import e2e_google_probe
import e2e_notion_probe
import google_apply_sync
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


CALENDAR_ID = "calendar@example.test"
EVENT_DATABASE_ID = "11111111-1111-4111-8111-111111111111"
PAGE_ID = "33333333-3333-4333-8333-333333333333"
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
        NOTION_TOKEN="notion-token",
        NOTION_EVENT_INTERNAL_ID=EVENT_DATABASE_ID,
        NOTION_EVENT_ID="",
        DISCORD_SYNC_ENABLED="false",
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
    notion_archive_statuses: list[int] | None = None,
    hide_notion_query_results: bool = False,
):
    google_events: dict[str, dict] = {}
    notion_pages: dict[str, dict] = {}
    calls: list[tuple[str, str]] = []
    control = {
        "notion_archive_statuses": list(notion_archive_statuses or [200]),
        "hide_notion_query_results": hide_notion_query_results,
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
                return Response("" if event is None else json.dumps(event), status=404 if event is None else 200)
            if method == "DELETE":
                google_events.pop(event_id, None)
                return Response("", status=204)
            raise AssertionError(f"想定外のGoogle API呼び出し: {method} {parsed.path}")

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
            return Response(json.dumps(page), status=200)
        if path == f"/pages/{PAGE_ID}" and method == "GET":
            page = notion_pages.get(PAGE_ID)
            return Response("" if page is None else json.dumps(page), status=404 if page is None else 200)
        if path == f"/pages/{PAGE_ID}" and method == "PATCH":
            page = notion_pages.get(PAGE_ID)
            if page is None:
                return Response("", status=404)
            if payload.get("archived") is True:
                statuses = control["notion_archive_statuses"]
                status = statuses.pop(0) if statuses else 200
                if status >= 300:
                    return Response(json.dumps({"code": "internal_server_error"}), status=status)
                page["archived"] = True
                return Response(json.dumps(page), status=status)
            page["properties"].update(json.loads(json.dumps(payload.get("properties") or {})))
            return Response(json.dumps(page), status=200)
        raise AssertionError(f"想定外のNotion API呼び出し: {method} {path}")

    monkeypatch.setattr(e2e_google_notion_probe, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(e2e_google_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    monkeypatch.setattr(google_apply_sync, "fetch", fake_fetch)
    return google_events, notion_pages, calls, control


def test_google_notion_probe_applies_and_cleans_both_resources(monkeypatch) -> None:
    google_events, notion_pages, _, _ = install_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)

    result = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(env, state, RUN_ID)
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"]["google_create"] == 200
    assert result["stages"]["google_read"] == 200
    assert result["stages"]["application_apply"] == 200
    assert result["stages"]["notion_find"] == 200
    assert result["stages"]["notion_read"] == 200
    assert result["stages"]["notion_archive"] == 200
    assert result["stages"]["google_delete"] == 204
    assert google_events == {}
    assert notion_pages[PAGE_ID]["archived"] is True

    manifest = run(state.get_e2e_manifest("google_notion"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "google_notion_sync"
    assert manifest["dirty"] is False
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "notion_database_id_sha256",
        "google_event_id_sha256",
        "notion_page_id_sha256",
    }
    assert "google_event_id" not in manifest
    assert "notion_page_id" not in manifest
    assert env.STATE_KV.put_calls == []


def test_google_notion_probe_rejects_unmanaged_destinations_before_io(monkeypatch) -> None:
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    env.NOTION_EVENT_ID = "external-database"

    external = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(
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
    env.DISCORD_SYNC_ENABLED = "true"
    discord = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )
    assert discord == {
        "ok": False,
        "dirty": False,
        "error": "discord_sync_must_be_disabled",
    }
    assert calls == []

    env.DISCORD_SYNC_ENABLED = "false"
    env.NOTION_PROP_TITLE = "custom-title"
    override = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(
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


def test_google_notion_cleanup_failure_blocks_next_run_and_recovers(monkeypatch) -> None:
    google_events, notion_pages, calls, control = install_api_stub(
        monkeypatch,
        notion_archive_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)

    failed = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(env, state, RUN_ID)
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["cleanup"] == {"ok": False, "attempts": 4}
    assert google_events == {}
    assert notion_pages[PAGE_ID]["archived"] is False
    manifest = run(state.get_e2e_manifest("google_notion"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["notion_page_id"] == PAGE_ID

    call_count = len(calls)
    blocked = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(env, state, RUN_ID)
    )
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["notion_archive_statuses"] = [200]
    recovered = run(
        e2e_google_notion_probe.cleanup_google_notion_sync_probe(
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


def test_google_notion_cleanup_keeps_dirty_when_notion_ownership_is_unresolved(
    monkeypatch,
) -> None:
    _, notion_pages, _, _ = install_api_stub(
        monkeypatch,
        hide_notion_query_results=True,
    )
    env = make_env()
    state = StateStore(env)

    result = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(env, state, RUN_ID)
    )

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "notion_page_ownership_unresolved"
    assert notion_pages[PAGE_ID]["archived"] is False
    manifest = run(state.get_e2e_manifest("google_notion"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["create_attempted"]["notion_page"] is True


def test_google_notion_cleanup_rejects_notion_title_ownership_mismatch(
    monkeypatch,
) -> None:
    _, notion_pages, calls, control = install_api_stub(
        monkeypatch,
        notion_archive_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_google_notion_probe.run_google_notion_sync_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    notion_pages[PAGE_ID]["properties"]["イベント名"] = {
        "title": [{"text": {"content": "different owner"}}],
    }
    control["notion_archive_statuses"] = [200]
    call_count = len(calls)
    cleanup = run(
        e2e_google_notion_probe.cleanup_google_notion_sync_probe(
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


def test_google_notion_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_notion_sync_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_GOOGLE_NOTION_SYNC_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-notion-sync",
                method="POST",
            )
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-notion-sync",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-notion-sync",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-notion-sync",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert unauthorized.status == 401
    assert wrong_method.status == 405
    assert response_json(wrong_method) == {
        "ok": False,
        "error": "method_not_allowed",
    }
    assert missing_run_id.status == 400
    assert response_json(missing_run_id) == {"ok": False, "error": "invalid_run_id"}
    assert success.status == 200
    assert response_json(success) == {"ok": True, "dirty": False}
    assert calls == [RUN_ID]


def test_google_notion_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_GOOGLE_NOTION_SYNC_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-notion-sync",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response) == {"ok": False, "error": "not_found"}
