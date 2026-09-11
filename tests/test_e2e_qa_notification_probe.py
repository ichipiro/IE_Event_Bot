"""QA通知E2Eシナリオを外部通信なしで検証する。"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import urlparse

from workers import Response

import e2e_discord_probe
import e2e_entry
import e2e_notion_probe
import e2e_qa_notification_probe
import jobs
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


QA_DATABASE_ID = "22222222-2222-4222-8222-222222222222"
QA_PAGE_ID = "44444444-4444-4444-8444-444444444444"
GUILD_ID = "guild-id"
CHANNEL_ID = "qa-channel-id"
MESSAGE_ID = "message-id"
RUN_ID = "E2E-20260902T060000Z-1234abcd"
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
        NOTION_QA_ID=QA_DATABASE_ID,
        DISCORD_TOKEN="bot-token",
        DISCORD_GUILD_ID=GUILD_ID,
        QA_CHANNEL_ID=CHANNEL_ID,
        CRON_ENABLE_QA="false",
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
    lose_notion_create_response: bool = False,
    lose_discord_create_response: bool = False,
    hide_notion_query_results: bool = False,
    hide_discord_message_results: bool = False,
    round_notion_timestamps: bool = False,
    notion_archive_statuses: list[int] | None = None,
    discord_delete_statuses: list[int] | None = None,
):
    pages: dict[str, dict] = {}
    messages: dict[str, dict] = {}
    calls: list[dict] = []
    edit_counter = [0]
    control = {
        "lose_notion_create_response": lose_notion_create_response,
        "lose_discord_create_response": lose_discord_create_response,
        "hide_notion_query_results": hide_notion_query_results,
        "hide_discord_message_results": hide_discord_message_results,
        "round_notion_timestamps": round_notion_timestamps,
        "notion_archive_statuses": list(notion_archive_statuses or [200]),
        "discord_delete_statuses": list(discord_delete_statuses or [204]),
    }

    def next_edited_at() -> str:
        edit_counter[0] += 1
        if control["round_notion_timestamps"]:
            return "2026-09-02T06:00:00.000Z"
        return f"2026-09-02T06:00:0{edit_counter[0]}.000Z"

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET").upper()
        parsed = urlparse(url)
        payload = json.loads(str(request.get("body") or "{}"))
        path = parsed.path
        calls.append(
            {
                "method": method,
                "host": parsed.netloc,
                "path": path,
                "payload": payload,
            }
        )

        if parsed.netloc == "api.notion.com":
            notion_path = path.removeprefix("/v1")
            if method == "GET" and notion_path == f"/databases/{QA_DATABASE_ID}":
                return Response(
                    json.dumps(
                        {
                            "object": "database",
                            "id": QA_DATABASE_ID,
                            "properties": _schema(e2e_notion_probe._QA_SCHEMA),
                        }
                    ),
                    status=200,
                )
            if (
                method == "POST"
                and notion_path == f"/databases/{QA_DATABASE_ID}/query"
            ):
                filter_data = payload.get("filter") or {}
                contains = str(((filter_data.get("title") or {}).get("contains") or ""))
                matches = [
                    page
                    for page in pages.values()
                    if not page.get("archived")
                    and contains in _property_text(page, "質問", "title")
                ]
                if control["hide_notion_query_results"]:
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
            if method == "POST" and notion_path == "/pages":
                page = {
                    "object": "page",
                    "id": QA_PAGE_ID,
                    "parent": {
                        "type": "database_id",
                        "database_id": QA_DATABASE_ID,
                    },
                    "archived": False,
                    "last_edited_time": next_edited_at(),
                    "properties": _response_properties(payload.get("properties") or {}),
                }
                pages[QA_PAGE_ID] = page
                if control["lose_notion_create_response"]:
                    control["lose_notion_create_response"] = False
                    raise RuntimeError("response lost after Notion create")
                return Response(json.dumps(page), status=200)
            if notion_path.startswith("/pages/"):
                page_id = notion_path.split("/pages/", 1)[1]
                page = pages.get(page_id)
                if method == "GET":
                    return Response(
                        "" if page is None else json.dumps(page),
                        status=404 if page is None else 200,
                    )
                if method == "PATCH" and page is not None:
                    if payload.get("archived") is True:
                        statuses = control["notion_archive_statuses"]
                        status = statuses.pop(0) if statuses else 200
                        if status >= 300:
                            return Response("{}", status=status)
                        page["archived"] = True
                        page["last_edited_time"] = next_edited_at()
                        return Response(json.dumps(page), status=status)
                    page["properties"].update(
                        _response_properties(payload.get("properties") or {})
                    )
                    page["last_edited_time"] = next_edited_at()
                    return Response(json.dumps(page), status=200)
            raise AssertionError(f"想定外のNotion API呼び出し: {method} {notion_path}")

        if parsed.netloc == "discord.com":
            discord_path = path.removeprefix("/api/v10")
            if method == "GET" and discord_path == f"/guilds/{GUILD_ID}":
                return Response(json.dumps({"id": GUILD_ID}), status=200)
            if method == "GET" and discord_path == f"/channels/{CHANNEL_ID}":
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
            collection = f"/channels/{CHANNEL_ID}/messages"
            if method == "GET" and discord_path == collection:
                values = [] if control["hide_discord_message_results"] else list(messages.values())
                return Response(json.dumps(values), status=200)
            if method == "POST" and discord_path == collection:
                message = {
                    "id": MESSAGE_ID,
                    "channel_id": CHANNEL_ID,
                    "content": str(payload.get("content") or ""),
                    "mention_roles": [],
                    "mention_everyone": False,
                    "author": {"id": "bot-id", "bot": True},
                }
                messages[MESSAGE_ID] = message
                if control["lose_discord_create_response"]:
                    control["lose_discord_create_response"] = False
                    raise RuntimeError("response lost after Discord create")
                return Response(json.dumps(message), status=200)
            if discord_path.startswith(f"{collection}/"):
                message_id = discord_path.rsplit("/", 1)[-1]
                message = messages.get(message_id)
                if method == "GET":
                    return Response(
                        "" if message is None else json.dumps(message),
                        status=404 if message is None else 200,
                    )
                if method == "DELETE":
                    statuses = control["discord_delete_statuses"]
                    status = statuses.pop(0) if statuses else 204
                    if status < 300:
                        messages.pop(message_id, None)
                    return Response("", status=status)
            raise AssertionError(f"想定外のDiscord API呼び出し: {method} {discord_path}")

        raise AssertionError(f"想定外の外部API呼び出し: {method} {parsed.netloc}")

    monkeypatch.setattr(e2e_notion_probe, "fetch", fake_fetch)
    monkeypatch.setattr(e2e_discord_probe, "fetch", fake_fetch)
    monkeypatch.setattr(jobs, "fetch", fake_fetch)
    return pages, messages, calls, control


def test_qa_notification_probe_notifies_and_cleans_owned_resources(monkeypatch) -> None:
    pages, messages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()

    result = run(
        e2e_qa_notification_probe.run_qa_notification_probe(
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
        "target_notion_qa_database": 200,
        "target_discord_guild": 200,
        "target_discord_qa_channel": 200,
        "notion_precheck": 200,
        "discord_precheck": 200,
        "notion_create": 200,
        "notion_read_initial": 200,
        "job_first_run": 200,
        "discord_first_run_verify": 200,
        "notion_update": 200,
        "notion_read_updated": 200,
        "qa_cache_miss_prepare": 200,
        "job_notify": 200,
        "discord_message_find": 200,
        "discord_message_read": 200,
        "qa_cache_verify": 200,
        "message_cleanup_verify": 200,
        "message_delete": 204,
        "notion_cleanup_verify": 200,
        "notion_archive": 200,
    }
    assert pages[QA_PAGE_ID]["archived"] is True
    assert messages == {}
    assert env.STATE_KV.put_calls == []
    assert all(
        call["payload"].get("filter", {}).get("property") == "質問"
        for call in calls
        if call["host"] == "api.notion.com" and call["path"].endswith("/query")
    )

    manifest = run(StateStore(env).get_e2e_manifest("qa_notification"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "qa_notification_job"
    assert manifest["dirty"] is False
    assert manifest["outcome"] == "passed"
    assert manifest["last_run_id"] == RUN_ID
    assert set(manifest["resource_fingerprints"]) == {
        "notion_qa_database_id_sha256",
        "discord_guild_id_sha256",
        "discord_channel_id_sha256",
        "notion_page_id_sha256",
        "discord_message_id_sha256",
    }
    assert "notion_page_id" not in manifest
    assert "discord_message_id" not in manifest
    assert "target_fingerprints" not in manifest


def test_qa_notification_probe_handles_rounded_notion_timestamps(
    monkeypatch,
) -> None:
    pages, messages, _, _ = install_api_stub(
        monkeypatch,
        round_notion_timestamps=True,
    )
    env = make_env()

    result = run(
        e2e_qa_notification_probe.run_qa_notification_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["stages"]["qa_cache_miss_prepare"] == 200
    assert pages[QA_PAGE_ID]["archived"] is True
    assert messages == {}


def test_qa_notification_probe_reconciles_lost_notion_create_response(
    monkeypatch,
) -> None:
    _, _, calls, _ = install_api_stub(
        monkeypatch,
        lose_notion_create_response=True,
    )
    env = make_env()

    result = run(
        e2e_qa_notification_probe.run_qa_notification_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result["ok"] is True
    assert result["stages"]["notion_create"] == 0
    assert result["stages"]["notion_create_reconcile"] == 200
    assert sum(
        call["method"] == "POST" and call["path"] == "/v1/pages"
        for call in calls
    ) == 1


def test_qa_notification_unresolved_message_stays_dirty_and_recovers(
    monkeypatch,
) -> None:
    pages, messages, calls, control = install_api_stub(
        monkeypatch,
        lose_discord_create_response=True,
        hide_discord_message_results=True,
    )
    env = make_env()
    state = StateStore(env)

    failed = run(
        e2e_qa_notification_probe.run_qa_notification_probe(env, state, RUN_ID)
    )

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["error"] == "discord_message_ownership_unresolved"
    assert pages[QA_PAGE_ID]["archived"] is True
    assert set(messages) == {MESSAGE_ID}
    manifest = run(state.get_e2e_manifest("qa_notification"))
    assert isinstance(manifest, dict)
    assert manifest["create_attempted"]["discord_message"] is True

    call_count = len(calls)
    blocked = run(
        e2e_qa_notification_probe.run_qa_notification_probe(env, state, RUN_ID)
    )
    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(calls) == call_count

    control["hide_discord_message_results"] = False
    recovered = run(
        e2e_qa_notification_probe.cleanup_qa_notification_probe(
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
    assert messages == {}


def test_qa_notification_cleanup_rejects_message_owner_mismatch(monkeypatch) -> None:
    _, messages, _, control = install_api_stub(
        monkeypatch,
        discord_delete_statuses=[500, 500, 500, 500],
    )
    env = make_env()
    state = StateStore(env)
    failed = run(
        e2e_qa_notification_probe.run_qa_notification_probe(env, state, RUN_ID)
    )
    assert failed["dirty"] is True

    messages[MESSAGE_ID]["content"] = "different owner"
    control["discord_delete_statuses"] = [204]
    cleanup = run(
        e2e_qa_notification_probe.cleanup_qa_notification_probe(
            env,
            state,
            RUN_ID,
        )
    )

    assert cleanup["ok"] is False
    assert cleanup["dirty"] is True
    assert cleanup["error"] == "cleanup_target_mismatch"
    assert set(messages) == {MESSAGE_ID}


def test_qa_notification_probe_rejects_enabled_cron_before_io(monkeypatch) -> None:
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    env.CRON_ENABLE_QA = "true"

    result = run(
        e2e_qa_notification_probe.run_qa_notification_probe(
            env,
            StateStore(env),
            RUN_ID,
        )
    )

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "cron_qa_must_be_disabled",
    }
    assert calls == []


def test_qa_notification_route_requires_auth_post_and_run_id(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        calls.append(str(run_id or ""))
        return {"ok": True, "dirty": False, "run_id": run_id}

    monkeypatch.setattr(e2e_entry, "run_qa_notification_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_QA_NOTIFICATION_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    unauthorized = run(
        worker.fetch(
            Request("https://bot.test/admin/e2e/qa-notification", method="POST")
        )
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/qa-notification",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/qa-notification",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/qa-notification",
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


def test_qa_notification_route_is_hidden_when_disabled() -> None:
    worker = e2e_entry.Default()
    worker.env = SimpleNamespace(
        E2E_QA_NOTIFICATION_ENABLED="false",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        SYNC_DO_LOCK_ENABLED="false",
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/qa-notification",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
