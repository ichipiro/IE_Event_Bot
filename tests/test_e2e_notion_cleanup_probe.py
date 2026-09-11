"""Notion cleanup E2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import e2e_entry
import e2e_notion_cleanup_probe
import e2e_notion_probe
import jobs
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


DATABASE_ID = "11111111-1111-4111-8111-111111111111"
DUE_PAGE_ID = "22222222-2222-4222-8222-222222222222"
FUTURE_PAGE_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "E2E-20260902T080000Z-1234abcd"
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
        NOTION_TOKEN="notion-token",
        NOTION_EVENT_INTERNAL_ID=DATABASE_ID,
        CLEANUP_INTERVAL_SECONDS="86400",
        CRON_ENABLE_AUTO_CLEAN="false",
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


def _response_properties(raw: dict) -> dict:
    properties = json.loads(json.dumps(raw))
    for prop in properties.values():
        if not isinstance(prop, dict):
            continue
        date_value = prop.get("date")
        if isinstance(date_value, dict):
            for key in ("start", "end"):
                value = date_value.get(key)
                if isinstance(value, str) and value.endswith("+00:00"):
                    date_value[key] = f"{value[:16]}:00Z"
        for property_type in ("title", "rich_text"):
            nodes = prop.get(property_type)
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if isinstance(node, dict):
                    node["plain_text"] = str(
                        ((node.get("text") or {}).get("content") or "")
                    )
    return properties


def _property_text(page: dict, name: str, property_type: str) -> str:
    prop = (page.get("properties") or {}).get(name) or {}
    nodes = prop.get(property_type) or []
    if not nodes:
        return ""
    node = nodes[0] or {}
    return str(
        node.get("plain_text")
        or ((node.get("text") or {}).get("content") or "")
    )


def install_notion_api_stub(
    monkeypatch,
    *,
    lose_create_for: str = "",
    hide_query_results: bool = False,
):
    pages: dict[str, dict] = {}
    calls: list[dict] = []
    control = {
        "lose_create_for": lose_create_for,
        "hide_query_results": hide_query_results,
    }

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        payload = json.loads(str(request.get("body") or "{}"))
        path = parsed.path.removeprefix("/v1")
        calls.append({"method": method, "path": path, "payload": payload})

        if parsed.netloc != "api.notion.com":
            raise AssertionError(f"想定外の外部API呼び出し: {parsed.netloc}")
        if method == "GET" and path == f"/databases/{DATABASE_ID}":
            return Response(
                json.dumps(
                    {
                        "object": "database",
                        "id": DATABASE_ID,
                        "properties": _schema(e2e_notion_probe._EVENT_SCHEMA),
                    }
                ),
                status=200,
            )
        if method == "POST" and path == f"/databases/{DATABASE_ID}/query":
            filter_data = payload.get("filter") or {}
            contains = str(
                ((filter_data.get("rich_text") or {}).get("contains") or "")
            )
            matches = [
                page
                for page in pages.values()
                if not page.get("archived")
                and contains
                in _property_text(page, "GoogleイベントID", "rich_text")
            ]
            if control["hide_query_results"]:
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
            properties = _response_properties(payload.get("properties") or {})
            marker = _property_text(
                {"properties": properties},
                "GoogleイベントID",
                "rich_text",
            )
            page_kind = "due" if marker.endswith(":due") else "future"
            page_id = DUE_PAGE_ID if page_kind == "due" else FUTURE_PAGE_ID
            page = {
                "object": "page",
                "id": page_id,
                "parent": {
                    "type": "database_id",
                    "database_id": DATABASE_ID,
                },
                "archived": False,
                "in_trash": False,
                "properties": properties,
            }
            pages[page_id] = page
            if control["lose_create_for"] == page_kind:
                control["lose_create_for"] = ""
                raise RuntimeError("response lost after Notion create")
            return Response(json.dumps(page), status=200)
        if path.startswith("/pages/"):
            page_id = path.split("/pages/", 1)[1]
            page = pages.get(page_id)
            if method == "GET":
                return Response(
                    "" if page is None else json.dumps(page),
                    status=404 if page is None else 200,
                )
            if method == "PATCH" and page is not None:
                if payload.get("archived") is True:
                    page["archived"] = True
                    page["in_trash"] = True
                    return Response(json.dumps(page), status=200)
        raise AssertionError(f"想定外のNotion API呼び出し: {method} {path}")

    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    monkeypatch.setattr(jobs, "fetch", fake_fetch)
    return pages, calls, control


def test_notion_cleanup_payload_uses_notion_minute_precision() -> None:
    payload, expected = e2e_notion_cleanup_probe._page_payload(
        DATABASE_ID,
        RUN_ID,
        "due",
        datetime(2026, 9, 2, 8, 12, 34, tzinfo=timezone.utc),
    )

    date_value = payload["properties"]["日時"]["date"]
    assert date_value == {
        "start": "2026-09-02T07:12:00+00:00",
        "end": "2026-09-02T08:11:00+00:00",
    }
    assert expected["start"] == date_value["start"]
    assert expected["end"] == date_value["end"]


def test_notion_cleanup_probe_archives_only_due_page_then_cleans_both(
    monkeypatch,
) -> None:
    pages, calls, _ = install_notion_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)

    result = run(
        e2e_notion_cleanup_probe.run_notion_cleanup_probe(env, state, RUN_ID)
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert result["run_id"] == RUN_ID
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"]["job_cleanup"] == 200
    assert result["stages"]["job_interval_guard"] == 200
    assert result["stages"]["due_job_verify"] == 200
    assert result["stages"]["future_job_verify"] == 200
    assert pages[DUE_PAGE_ID]["archived"] is True
    assert pages[FUTURE_PAGE_ID]["archived"] is True
    assert env.STATE_KV.data == {}
    assert env.STATE_KV.put_calls == []

    page_creates = [
        call
        for call in calls
        if call["method"] == "POST" and call["path"] == "/pages"
    ]
    assert len(page_creates) == 2
    archive_calls = [
        call
        for call in calls
        if call["method"] == "PATCH"
        and call["payload"].get("archived") is True
    ]
    assert [call["path"] for call in archive_calls] == [
        f"/pages/{DUE_PAGE_ID}",
        f"/pages/{FUTURE_PAGE_ID}",
    ]
    assert all(
        call["payload"].get("filter", {}).get("property")
        == "GoogleイベントID"
        for call in calls
        if call["path"].endswith("/query")
    )

    manifest = run(state.get_e2e_manifest("notion_cleanup"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "notion_cleanup_job"
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "notion_event_database_id_sha256",
        "due_page_id_sha256",
        "future_page_id_sha256",
    }
    assert "due_page_id" not in manifest
    assert "future_page_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_notion_cleanup_probe_reconciles_lost_create_response(monkeypatch) -> None:
    _, calls, _ = install_notion_api_stub(
        monkeypatch,
        lose_create_for="due",
    )
    env = make_env()

    result = run(
        e2e_notion_cleanup_probe.run_notion_cleanup_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["stages"]["due_create"] == 0
    assert result["stages"]["due_create_reconcile"] == 200
    assert len(
        [
            call
            for call in calls
            if call["method"] == "POST" and call["path"] == "/pages"
        ]
    ) == 2


def test_notion_cleanup_unresolved_create_stays_dirty_and_recovers(
    monkeypatch,
) -> None:
    pages, _, control = install_notion_api_stub(
        monkeypatch,
        lose_create_for="future",
        hide_query_results=True,
    )
    env = make_env()
    state = StateStore(env)

    failed = run(
        e2e_notion_cleanup_probe.run_notion_cleanup_probe(env, state, RUN_ID)
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "notion_future_ownership_unresolved"
    assert pages[DUE_PAGE_ID]["archived"] is True
    assert pages[FUTURE_PAGE_ID]["archived"] is False
    manifest = run(state.get_e2e_manifest("notion_cleanup"))
    assert isinstance(manifest, dict)
    assert manifest["create_attempted"]["future_page"] is True

    blocked = run(
        e2e_notion_cleanup_probe.run_notion_cleanup_probe(env, state, RUN_ID)
    )
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }

    control["hide_query_results"] = False
    recovered = run(
        e2e_notion_cleanup_probe.cleanup_notion_cleanup_probe(
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
    assert pages[DUE_PAGE_ID]["archived"] is True
    assert pages[FUTURE_PAGE_ID]["archived"] is True


def test_notion_cleanup_rejects_page_owner_mismatch(monkeypatch) -> None:
    pages, calls, _ = install_notion_api_stub(monkeypatch)
    pages[FUTURE_PAGE_ID] = {
        "object": "page",
        "id": FUTURE_PAGE_ID,
        "parent": {"type": "database_id", "database_id": DATABASE_ID},
        "archived": False,
        "in_trash": False,
        "properties": {
            "GoogleイベントID": {
                "rich_text": [
                    {"plain_text": "[ie-event-bot-e2e:different-run]:future"}
                ]
            }
        },
    }
    database_fingerprint = sha256(
        DATABASE_ID.replace("-", "").encode()
    ).hexdigest()
    env = make_env(
        manifests={
            "notion_cleanup": {
                "version": 1,
                "kind": "notion_cleanup_job",
                "dirty": True,
                "run_id": RUN_ID,
                "target_fingerprints": {
                    "notion_event_database_id_sha256": database_fingerprint,
                },
                "create_attempted": {
                    "due_page": False,
                    "future_page": True,
                },
                "future_page_id": FUTURE_PAGE_ID,
                "stages": {},
            }
        }
    )

    result = run(
        e2e_notion_cleanup_probe.cleanup_notion_cleanup_probe(
            env,
            StateStore(env),
            expected_run_id=RUN_ID,
        )
    )

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "cleanup_target_mismatch"
    assert pages[FUTURE_PAGE_ID]["archived"] is False
    assert not any(call["method"] == "PATCH" for call in calls)


def test_notion_cleanup_probe_rejects_enabled_cron_before_io(monkeypatch) -> None:
    _, calls, _ = install_notion_api_stub(monkeypatch)
    env = make_env()
    env.CRON_ENABLE_AUTO_CLEAN = "true"

    result = run(
        e2e_notion_cleanup_probe.run_notion_cleanup_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "cron_auto_clean_must_be_disabled",
    }
    assert calls == []


def test_notion_cleanup_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False, "run_id": run_id}

    monkeypatch.setattr(e2e_entry, "run_notion_cleanup_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_NOTION_CLEANUP_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(
            Request("https://bot.test/admin/e2e/notion-cleanup", method="POST")
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-cleanup",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-cleanup",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-cleanup",
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


def test_notion_cleanup_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_NOTION_CLEANUP_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/notion-cleanup",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
