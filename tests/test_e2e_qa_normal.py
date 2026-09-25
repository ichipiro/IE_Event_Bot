"""通常Q&Aの実API境界を代替し、共有状態・通知・所有外拒否を検証する。"""

import json
from urllib.parse import urlparse

import pytest
from workers import Response

import e2e_entry
import e2e_qa_normal_probe as normal
import jobs
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_qa_notification_probe import (
    CHANNEL_ID, GUILD_ID, QA_DATABASE_ID, RUN_ID, _response_properties,
    _schema, make_env, run,
)


def install(monkeypatch):
    pages, messages, calls = {}, {}, []
    tick = [0]

    async def fetch(url, options=None):
        options = options or {}
        method = options.get("method", "GET")
        path = urlparse(url).path.removeprefix("/v1").removeprefix("/api/v10")
        data = json.loads(options.get("body") or "{}")
        calls.append((method, path, data))
        status, result = 200, {}
        if path == f"/databases/{QA_DATABASE_ID}" and method == "GET":
            result = {"id": QA_DATABASE_ID, "object": "database", "properties": _schema(normal.qa._QA_SCHEMA)}
        elif path == f"/databases/{QA_DATABASE_ID}/query":
            rows = [p for p in pages.values() if not p.get("archived")]
            start = int(data.get("start_cursor", "0"))
            end = start + data.get("page_size", 100)
            result = {"results": rows[start:end], "has_more": end < len(rows),
                      "next_cursor": str(end) if end < len(rows) else None}
        elif path == "/pages" and method == "POST":
            page_id = f"44444444-4444-4444-8444-{len(pages) + 1:012d}"
            result = {"id": page_id, "parent": {"database_id": QA_DATABASE_ID},
                      "properties": _response_properties(data["properties"]), "archived": False,
                      "created_time": f"2026-09-25T00:00:0{len(pages)}.000Z"}
            pages[page_id] = result
        elif path.startswith("/pages/"):
            result = pages[path.rsplit("/", 1)[1]]
            if method == "PATCH":
                result["properties"].update(_response_properties(data.get("properties", {})))
                result.update({k: v for k, v in data.items() if k != "properties"})
        elif path == f"/guilds/{GUILD_ID}":
            result = {"id": GUILD_ID}
        elif path == f"/channels/{CHANNEL_ID}":
            result = {"id": CHANNEL_ID, "guild_id": GUILD_ID, "type": 0}
        elif path == f"/channels/{CHANNEL_ID}/messages":
            if method == "GET":
                result = list(messages.values())
            else:
                message_id = str(len(messages) + 1)
                result = {"id": message_id, "channel_id": CHANNEL_ID, "content": data["content"],
                          "mention_everyone": False, "mention_roles": []}
                messages[message_id] = result
        elif path.startswith(f"/channels/{CHANNEL_ID}/messages/"):
            message_id = path.rsplit("/", 1)[1]
            if method == "DELETE":
                messages.pop(message_id, None)
                status = 204
            elif message_id in messages:
                result = messages[message_id]
            else:
                status = 404
        else:
            raise AssertionError((method, path))
        if path.startswith("/pages") and method in ("POST", "PATCH"):
            assert isinstance(result, dict)
            tick[0] += 1
            result["last_edited_time"] = f"2026-09-25T00:{tick[0]:02d}:00.000Z"
        return Response(json.dumps(result), status=status)

    monkeypatch.setattr(jobs, "fetch", fetch)
    import e2e_notion_probe
    import e2e_discord_probe
    monkeypatch.setattr(e2e_notion_probe, "fetch", fetch)
    monkeypatch.setattr(e2e_discord_probe, "fetch", fetch)
    return pages, messages, calls


def setup(monkeypatch):
    data = install(monkeypatch)
    env = make_env()
    async def delete(key):
        env.STATE_KV.data.pop(key, None)
    env.STATE_KV.delete = delete
    env.INTERNAL_API_TOKEN = "test-token"
    env.CF_VERSION_METADATA = {"tag": RUN_ID, "id": "qa-version"}
    env.E2E_QA_NOTIFICATION_ENABLED = "true"
    env.SYNC_DO_LOCK_ENABLED = "true"
    headers = {"Authorization": "Bearer test-token", "X-E2E-Run-ID": RUN_ID,
               "X-E2E-Version-Tag": RUN_ID}
    request = Request("https://e2e.invalid/admin/e2e/qa-normal/prepare", method="POST", headers=headers)
    return env, request, data


def phase(env, request, name):
    return run(normal.run(env, StateStore(env), RUN_ID, name, request))


def test_full_job_shared_cache_numbers_notifications_and_cleanup(monkeypatch):
    env, request, (pages, messages, calls) = setup(monkeypatch)
    for name in normal.PHASES:
        assert phase(env, request, name)["ok"]
        assert phase(env, request, "verify")["ok"]
        if name == "first":
            assert not messages
            assert {normal._property_number(p, "質問番号") for p in pages.values()} == {41, 42, 43}
        if name in ("notify", "duplicate"):
            assert len(messages) == 2
    # 各通常ジョブは採番用と通知用で全件queryを実行する（filterによる対象加工なし）。
    assert len([c for c in calls if c[1].endswith("/query") and c[2] == {}]) == 6
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    assert all(p["archived"] for p in pages.values()) and not messages
    assert all(run(StateStore(env).get_text(k)) is None for k in normal.KEYS)
    manifest = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert manifest is not None
    assert manifest["outcome"] == "passed" and manifest["dirty"] is False


@pytest.mark.parametrize("key", normal.KEYS)
def test_existing_shared_state_is_never_overwritten(monkeypatch, key):
    env, request, (pages, _, _) = setup(monkeypatch)
    run(env.STATE_KV.put(key, "foreign"))
    with pytest.raises(RuntimeError, match="shared_state_not_empty"):
        phase(env, request, "prepare")
    assert not pages and run(StateStore(env).get_text(key)) == "foreign"


def test_foreign_page_prevents_normal_job(monkeypatch):
    env, request, (pages, _, _) = setup(monkeypatch)
    phase(env, request, "prepare")
    phase(env, request, "verify")
    pages["foreign"] = {"id": "foreign", "properties": {}}
    with pytest.raises(RuntimeError, match="foreign_input"):
        phase(env, request, "first")
    assert run(StateStore(env).get_text("qa_cache")) is None
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    assert not pages["foreign"].get("archived")


def test_cleanup_preserves_foreign_kv_and_wrong_run(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="run_id_mismatch"):
        run(normal.cleanup(env, StateStore(env), "E2E-20260925T000000Z-ffffffff"))
    run(env.STATE_KV.put("qa_cache", "foreign"))
    with pytest.raises(RuntimeError, match="foreign_kv"):
        run(normal.cleanup(env, StateStore(env), RUN_ID))
    assert run(StateStore(env).get_text("qa_cache")) == "foreign"
    manifest = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert manifest and manifest["dirty"] is True


def test_unverified_or_replayed_phase_rejected(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="previous_unverified"):
        phase(env, request, "first")
    phase(env, request, "verify")
    phase(env, request, "first")
    with pytest.raises(RuntimeError, match="phase_mismatch"):
        phase(env, request, "first")


def test_route_requires_matching_revision(monkeypatch):
    env, request, _ = setup(monkeypatch)
    env.CF_VERSION_METADATA = {"tag": "old"}
    app = e2e_entry.Default()
    app.env = env
    response = run(app.fetch(request))
    assert response.status == 409
    assert json.loads(run(response.text()))["error"] == "worker_version_mismatch"


def test_all_phases_and_cleanup_via_authenticated_routes(monkeypatch):
    env, request, (_, messages, _) = setup(monkeypatch)
    app = e2e_entry.Default()
    app.env = env
    for name in normal.PHASES:
        for step in (name, "verify"):
            request.url = f"https://e2e.invalid/admin/e2e/qa-normal/{step}"
            response = run(app.fetch(request))
            assert response.status == 200, run(response.text())
    request.url = "https://e2e.invalid/admin/e2e/qa-notification/cleanup"
    response = run(app.fetch(request))
    assert response.status == 200, run(response.text())
    assert not messages


def test_other_scenario_cannot_claim_during_shared_qa(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(StateStore(env).put_e2e_manifest("notion", {
            "version": 1, "kind": "notion_pages", "dirty": True, "run_id": RUN_ID,
        }))
    run(normal.cleanup(env, StateStore(env), RUN_ID))
    run(StateStore(env).put_e2e_manifest("notion", {
        "version": 1, "kind": "notion_pages", "dirty": True, "run_id": RUN_ID,
    }))
