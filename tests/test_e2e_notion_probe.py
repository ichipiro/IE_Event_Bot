"""E2E専用Notion CRUDプローブを外部通信なしで検証する。"""

import asyncio
import json
import re
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import e2e_entry
import e2e_notion_probe
from e2e_entry import Default
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


EVENT_DATABASE_ID = "11111111-1111-4111-8111-111111111111"
QA_DATABASE_ID = "22222222-2222-4222-8222-222222222222"
EVENT_PAGE_ID = "33333333-3333-4333-8333-333333333333"
QA_PAGE_ID = "44444444-4444-4444-8444-444444444444"
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
        NOTION_TOKEN="notion-token",
        NOTION_EVENT_INTERNAL_ID=EVENT_DATABASE_ID,
        NOTION_QA_ID=QA_DATABASE_ID,
        STATE_KV=kv or MemoryKV(),
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


def install_notion_api_stub(
    monkeypatch,
    *,
    event_schema: dict[str, str] | None = None,
    event_archive_statuses: list[int] | None = None,
    lose_event_create_response: bool = False,
    hide_event_query_results: bool = False,
):
    event_schema = dict(event_schema or e2e_notion_probe._EVENT_SCHEMA)
    qa_schema = dict(e2e_notion_probe._QA_SCHEMA)
    pages: dict[str, dict] = {}
    calls: list[dict] = []
    control = {
        "event_archive_statuses": list(event_archive_statuses or [200]),
        "lose_event_create_response": lose_event_create_response,
        "hide_event_query_results": hide_event_query_results,
    }

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        path = parsed.path.removeprefix("/v1")
        payload = json.loads(str(request.get("body") or "{}"))
        calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "headers": dict(request.get("headers") or {}),
            }
        )

        if method == "GET" and path == f"/databases/{EVENT_DATABASE_ID}":
            return Response(
                json.dumps(
                    {
                        "object": "database",
                        "id": EVENT_DATABASE_ID,
                        "title": [{"plain_text": "Any renamed event database"}],
                        "properties": _schema(event_schema),
                    }
                ),
                status=200,
            )
        if method == "GET" and path == f"/databases/{QA_DATABASE_ID}":
            return Response(
                json.dumps(
                    {
                        "object": "database",
                        "id": QA_DATABASE_ID,
                        "title": [{"plain_text": "Any renamed Q&A database"}],
                        "properties": _schema(qa_schema),
                    }
                ),
                status=200,
            )

        if method == "POST" and path.startswith("/databases/") and path.endswith(
            "/query"
        ):
            database_id = path.split("/databases/", 1)[1].rsplit("/query", 1)[0]
            filter_data = payload.get("filter") or {}
            property_name = str(filter_data.get("property") or "")
            contains = str(((filter_data.get("rich_text") or {}).get("contains") or ""))
            matches = [
                page
                for page in pages.values()
                if str((page.get("parent") or {}).get("database_id") or "")
                == database_id
                and not page.get("archived")
                and contains in _property_text(page, property_name, "rich_text")
            ]
            if database_id == EVENT_DATABASE_ID and control["hide_event_query_results"]:
                matches = []
            return Response(
                json.dumps(
                    {
                        "object": "list",
                        "results": matches,
                        "has_more": False,
                        "next_cursor": None,
                    }
                ),
                status=200,
            )

        if method == "POST" and path == "/pages":
            database_id = str((payload.get("parent") or {}).get("database_id") or "")
            if database_id == EVENT_DATABASE_ID:
                page_id = EVENT_PAGE_ID
            elif database_id == QA_DATABASE_ID:
                page_id = QA_PAGE_ID
            else:
                return Response(json.dumps({"code": "object_not_found"}), status=404)
            page = {
                "object": "page",
                "id": page_id,
                "parent": {"type": "database_id", "database_id": database_id},
                "archived": False,
                "properties": json.loads(json.dumps(payload.get("properties") or {})),
            }
            pages[page_id] = page
            if database_id == EVENT_DATABASE_ID and control["lose_event_create_response"]:
                control["lose_event_create_response"] = False
                raise RuntimeError("response lost after create")
            return Response(json.dumps(page), status=200)

        if path.startswith("/pages/"):
            page_id = path.split("/pages/", 1)[1]
            if method == "GET":
                if page_id not in pages:
                    return Response("", status=404)
                return Response(json.dumps(pages[page_id]), status=200)
            if method == "PATCH":
                if page_id not in pages:
                    return Response("", status=404)
                if payload.get("archived") is True:
                    if page_id == EVENT_PAGE_ID:
                        statuses = control["event_archive_statuses"]
                        status = statuses.pop(0) if statuses else 200
                    else:
                        status = 200
                    if status >= 300:
                        return Response(json.dumps({"code": "internal_server_error"}), status=status)
                    pages[page_id]["archived"] = True
                    return Response(json.dumps(pages[page_id]), status=status)
                pages[page_id]["properties"].update(
                    json.loads(json.dumps(payload.get("properties") or {}))
                )
                return Response(json.dumps(pages[page_id]), status=200)

        raise AssertionError(f"想定外のNotion API呼び出し: {method} {path}")

    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    return pages, calls, control


def test_notion_crud_probe_cleans_resources_and_manifest(monkeypatch) -> None:
    pages, calls, _ = install_notion_api_stub(monkeypatch)
    env = make_env()

    result = run(e2e_notion_probe.run_notion_crud_probe(env, StateStore(env)))

    assert result["ok"] is True
    assert result["dirty"] is False
    assert re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", result["run_id"])
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"] == {
        "target_event_database": 200,
        "target_qa_database": 200,
        "event_create": 200,
        "event_read": 200,
        "event_update": 200,
        "event_read_updated": 200,
        "qa_create": 200,
        "qa_read": 200,
        "qa_update": 200,
        "qa_read_updated": 200,
        "qa_cleanup_verify": 200,
        "qa_archive": 200,
        "event_cleanup_verify": 200,
        "event_archive": 200,
    }
    assert pages[EVENT_PAGE_ID]["archived"] is True
    assert pages[QA_PAGE_ID]["archived"] is True
    assert all(call["headers"]["Authorization"] == "Bearer notion-token" for call in calls)
    assert all(call["headers"]["Notion-Version"] == "2022-06-28" for call in calls)

    manifest = run(StateStore(env).get_e2e_manifest("notion"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False
    assert manifest["last_run_id"] == result["run_id"]
    assert manifest["stages"] == result["stages"]
    assert set(manifest["resource_fingerprints"]) == {
        "event_database_id_sha256",
        "qa_database_id_sha256",
        "event_page_id_sha256",
        "qa_page_id_sha256",
    }
    assert "event_page_id" not in manifest
    assert "qa_page_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_notion_crud_probe_rejects_schema_mismatch_before_create(monkeypatch) -> None:
    invalid_schema = dict(e2e_notion_probe._EVENT_SCHEMA)
    invalid_schema["日時"] = "rich_text"
    _, calls, _ = install_notion_api_stub(
        monkeypatch,
        event_schema=invalid_schema,
    )
    env = make_env()

    result = run(e2e_notion_probe.run_notion_crud_probe(env, StateStore(env)))

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "notion_event_schema_mismatch",
        "stages": {"target_event_database": 200},
    }
    assert [(call["method"], call["path"]) for call in calls] == [
        ("GET", f"/databases/{EVENT_DATABASE_ID}")
    ]
    assert run(StateStore(env).get_e2e_manifest("notion")) is None


def test_notion_crud_probe_requires_distinct_database_ids(monkeypatch) -> None:
    _, calls, _ = install_notion_api_stub(monkeypatch)
    env = make_env()
    env.NOTION_QA_ID = EVENT_DATABASE_ID.replace("-", "")

    result = run(e2e_notion_probe.run_notion_crud_probe(env, StateStore(env)))

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "notion_database_ids_must_differ",
        "stages": {},
    }
    assert calls == []


def test_notion_crud_probe_reconciles_lost_create_response(monkeypatch) -> None:
    pages, calls, _ = install_notion_api_stub(
        monkeypatch,
        lose_event_create_response=True,
    )
    env = make_env()

    result = run(e2e_notion_probe.run_notion_crud_probe(env, StateStore(env)))

    assert result["ok"] is True
    assert result["stages"]["event_create"] == 0
    assert result["stages"]["event_create_reconcile"] == 200
    event_posts = [
        call
        for call in calls
        if call["method"] == "POST"
        and call["path"] == "/pages"
        and str((call["payload"].get("parent") or {}).get("database_id") or "")
        == EVENT_DATABASE_ID
    ]
    assert len(event_posts) == 1
    assert pages[EVENT_PAGE_ID]["archived"] is True


def test_notion_lost_create_with_empty_query_stays_dirty(monkeypatch) -> None:
    pages, _, _ = install_notion_api_stub(
        monkeypatch,
        lose_event_create_response=True,
        hide_event_query_results=True,
    )
    env = make_env()
    state = StateStore(env)

    result = run(e2e_notion_probe.run_notion_crud_probe(env, state))

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "notion_event_ownership_unresolved"
    assert pages[EVENT_PAGE_ID]["archived"] is False
    manifest = run(state.get_e2e_manifest("notion"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["create_attempted"]["event"] is True


def test_notion_cleanup_failure_blocks_next_run_and_recovers(monkeypatch) -> None:
    pages, calls, control = install_notion_api_stub(
        monkeypatch,
        event_archive_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)

    failed = run(e2e_notion_probe.run_notion_crud_probe(env, state))

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["cleanup"] == {"ok": False, "attempts": 4}
    assert pages[QA_PAGE_ID]["archived"] is True
    assert pages[EVENT_PAGE_ID]["archived"] is False
    manifest = run(state.get_e2e_manifest("notion"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["event_page_id"] == EVENT_PAGE_ID

    call_count = len(calls)
    blocked = run(e2e_notion_probe.run_notion_crud_probe(env, state))
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["event_archive_statuses"] = [200]
    recovered = run(e2e_notion_probe.cleanup_notion_crud_probe(env, state))

    assert recovered == {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
    assert pages[EVENT_PAGE_ID]["archived"] is True


def test_confirmed_notion_cleanup_rejects_run_marker_mismatch(monkeypatch) -> None:
    pages, calls, _ = install_notion_api_stub(monkeypatch)
    pages[EVENT_PAGE_ID] = {
        "object": "page",
        "id": EVENT_PAGE_ID,
        "parent": {"type": "database_id", "database_id": EVENT_DATABASE_ID},
        "archived": False,
        "properties": {
            "GoogleイベントID": e2e_notion_probe._rich_text("different-run"),
        },
    }
    env = make_env(
        manifests={
            "notion": {
                "version": 1,
                "kind": "notion_pages",
                "dirty": True,
                "run_id": ROUTE_RUN_ID,
                "target_fingerprints": e2e_notion_probe._target_fingerprints(
                    EVENT_DATABASE_ID,
                    QA_DATABASE_ID,
                ),
                "event_page_id": EVENT_PAGE_ID,
            }
        }
    )

    result = run(e2e_notion_probe.cleanup_notion_crud_probe(env, StateStore(env)))

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "cleanup_target_mismatch"
    assert not any(
        call["method"] == "PATCH" and call["path"] == f"/pages/{EVENT_PAGE_ID}"
        for call in calls
    )
    assert pages[EVENT_PAGE_ID]["archived"] is False


def test_confirmed_notion_cleanup_rejects_target_fingerprint_mismatch() -> None:
    env = make_env(
        manifests={
            "notion": {
                "version": 1,
                "kind": "notion_pages",
                "dirty": True,
                "run_id": "run-id",
                "target_fingerprints": {
                    "event_database_id_sha256": "wrong",
                    "qa_database_id_sha256": "wrong",
                },
            }
        }
    )

    result = run(e2e_notion_probe.cleanup_notion_crud_probe(env, StateStore(env)))

    assert result == {
        "ok": False,
        "dirty": True,
        "error": "dirty_manifest_target_mismatch",
    }


def test_notion_request_waits_for_retry_after(monkeypatch) -> None:
    responses = [
        Response(
            json.dumps({"code": "rate_limited"}),
            status=429,
            headers={"Retry-After": "1"},
        ),
        Response(json.dumps({"object": "database"}), status=200),
    ]
    sleep_calls: list[float] = []

    async def fake_fetch(url, options=None):
        return responses.pop(0)

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_notion_probe.asyncio, "sleep", fake_sleep)
    env = SimpleNamespace(NOTION_TOKEN="notion-token")

    status, data, retries = run(
        e2e_notion_probe._notion_request(env, "GET", f"/databases/{EVENT_DATABASE_ID}")
    )

    assert status == 200
    assert data == {"object": "database"}
    assert retries == 1
    assert sleep_calls == [1.0]


def test_notion_request_rejects_excessive_retry_after(monkeypatch) -> None:
    async def fake_fetch(url, options=None):
        return Response("{}", status=429, headers={"Retry-After": "3600"})

    async def fail_sleep(seconds: float):
        raise AssertionError("過大なRetry-Afterではsleepしない")

    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_notion_probe.asyncio, "sleep", fail_sleep)

    status, _, retries = run(
        e2e_notion_probe._notion_request(
            SimpleNamespace(NOTION_TOKEN="notion-token"),
            "GET",
            f"/databases/{EVENT_DATABASE_ID}",
        )
    )

    assert status == 429
    assert retries == 0


def test_notion_e2e_route_requires_auth_and_post(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        assert isinstance(run_id, str)
        calls.append(run_id)
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_notion_crud_probe", fake_probe)
    env = SimpleNamespace(
        E2E_NOTION_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )
    worker = make_worker(env)

    unauthorized = run(
        worker.fetch(Request("https://bot.test/admin/e2e/notion-crud", method="POST"))
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-crud",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-crud",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-crud",
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


def test_notion_e2e_route_is_hidden_when_disabled() -> None:
    worker = make_worker(
        SimpleNamespace(
            E2E_NOTION_CRUD_ENABLED="false",
            INTERNAL_API_TOKEN="test-token",
        )
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response) == {"ok": False, "error": "not_found"}
