"""Discord差分E2Eの所有境界と失敗回収を外部通信なしで検証する。"""

import json
from copy import deepcopy

import pytest
from workers import Response

import discord_notion_sync
import e2e_discord_delta_probe as probe
import e2e_discord_probe
import e2e_entry
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_discord_notion_probe import (
    DISCORD_EVENT_ID,
    GUILD_ID,
    PAGE_ID,
    ROUTE_HEADERS,
    RUN_ID,
    install_api_stub,
    make_env,
    response_json,
    run,
)


@pytest.mark.parametrize("hide_canceled", [False, True])
def test_delta_lists_updates_cancels_and_deletes_owned_event(monkeypatch, hide_canceled):
    events, pages, calls, _ = install_api_stub(
        monkeypatch, hide_canceled_events=hide_canceled,
    )
    foreign = {"id": "foreign-id", "guild_id": GUILD_ID, "name": "Other run"}
    events["foreign-id"] = deepcopy(foreign)
    env = make_env()
    env.EVENT_CREATE_CHANNEL_ID = "must-not-send"
    state = StateStore(env)

    result = run(probe.run_discord_delta_probe(env, state, RUN_ID))

    assert result["ok"] is True
    assert result["dirty"] is False
    for phase in (
        "delta_create", "delta_unchanged", "delta_update", "delta_cancel",
        "delta_delete", "delta_deleted_unchanged",
    ):
        assert result["stages"][f"{phase}_list"] == 200
        assert result["stages"][f"{phase}_diff"] == 200
        assert result["stages"][f"{phase}_checkpoint_read"] == 200
        assert result["stages"][f"{phase}_checkpoint_write"] == 200
    assert result["stages"]["delta_state_isolated"] == 200
    assert result["stages"]["delta_state_persisted"] == 200
    assert events == {"foreign-id": foreign}
    assert pages[PAGE_ID]["archived"] is True
    assert "E2E delta update" in json.dumps(pages[PAGE_ID])
    assert sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 1
    assert sum(method == "PATCH" and path.endswith(f"/pages/{PAGE_ID}")
               for method, path in calls) == (3 if hide_canceled else 4)
    branch = "delta_cancel_missing" if hide_canceled else "delta_cancel_listed"
    assert result["stages"][branch] == 200
    assert result["stages"]["delta_delete_source_read"] == 404
    assert result["stages"]["delta_after_delete_read"] == 200
    # cleanupがarchiveする前に共通差分処理でpageを削除できている。
    assert "notion_archive" not in result["stages"]
    assert not any("/channels/" in path or "googleapis.com" in path for _, path in calls)
    assert env.STATE_KV.put_calls == []
    manifest = run(state.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "discord_delta_sync"
    assert manifest["outcome"] == "passed"
    assert run(state.get_e2e_manifest("discord_notion")) is None
    for raw_value in (DISCORD_EVENT_ID, PAGE_ID, GUILD_ID, "bot-token"):
        assert raw_value not in json.dumps(manifest)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "wrong_marker", "list_error"])
def test_delta_rejects_invalid_owned_list_before_apply(monkeypatch, failure):
    _, pages, _, _ = install_api_stub(monkeypatch)
    original_list = discord_notion_sync._list_discord_scheduled_events

    async def bad_list(env):
        events, error = await original_list(env)
        assert isinstance(events, list)
        if failure == "missing":
            return [], None
        if failure == "duplicate":
            return events + events, None
        if failure == "wrong_marker":
            events[0]["description"] = "not owned"
            return events, None
        return None, "upstream_error_with_private_data"

    monkeypatch.setattr(probe, "_list_discord_scheduled_events", bad_list)
    env = make_env()
    result = run(probe.run_discord_delta_probe(env, StateStore(env), RUN_ID))

    assert result["ok"] is False
    assert pages == {}
    assert "upstream_error_with_private_data" not in json.dumps(result)
    assert env.STATE_KV.put_calls == []


def test_delta_recovers_lost_update_response_without_recreating(monkeypatch):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    original_fetch = e2e_discord_probe.fetch

    async def lose_update(url, options=None):
        response = await original_fetch(url, options)
        if (options or {}).get("method") == "PATCH":
            raise RuntimeError("private response detail")
        return response

    monkeypatch.setattr(e2e_discord_probe, "fetch", lose_update)
    env = make_env()
    state = StateStore(env)
    result = run(probe.run_discord_delta_probe(env, state, RUN_ID))

    assert result["ok"] is False
    assert result["dirty"] is False
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert "private response detail" not in json.dumps(result)
    manifest = run(state.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "failed_clean"
    assert sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 1


@pytest.mark.parametrize("query_result", [None, {"id": "another-page"}])
def test_delta_update_query_failure_never_creates_another_page(monkeypatch, query_result):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    original_query = discord_notion_sync._notion_query_by_message_id
    query_calls = []

    async def fail_update_query(env, database_id, event_id):
        query_calls.append(event_id)
        if len(query_calls) == 2:
            return query_result
        return await original_query(env, database_id, event_id)

    monkeypatch.setattr(discord_notion_sync, "_notion_query_by_message_id", fail_update_query)
    env = make_env()
    state = StateStore(env)
    result = run(probe.run_discord_delta_probe(env, state, RUN_ID))

    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["stages"]["delta_update_diff"] == 500
    assert len(query_calls) == 2
    assert sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 1
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert env.STATE_KV.put_calls == []


def test_delta_cleanup_retains_dirty_then_checks_run_and_target(monkeypatch):
    events, pages, calls, control = install_api_stub(
        monkeypatch, notion_archive_statuses=[500] * 5,
    )
    env = make_env()
    state = StateStore(env)
    failed = run(probe.run_discord_delta_probe(env, state, RUN_ID))
    assert failed["dirty"] is True
    dirty_manifest = run(state.get_e2e_manifest("discord_delta"))
    assert isinstance(dirty_manifest, dict)
    assert json.loads(dirty_manifest["delta_checkpoint"]["snapshot"][DISCORD_EVENT_ID])["_pending_sync"] == {"id": DISCORD_EVENT_ID, "op": "delete"}
    assert dirty_manifest["delta_checkpoint"]["queue"] == [{"id": DISCORD_EVENT_ID, "op": "delete"}]
    count = len(calls)
    blocked = run(probe.run_discord_delta_probe(env, state, RUN_ID))
    assert blocked["error"] == "environment_dirty"
    wrong_run = run(probe.cleanup_discord_delta_probe(
        env, state, "E2E-20260911T000000Z-aaaaaaaa",
    ))
    assert wrong_run["error"] == "cleanup_run_id_mismatch"
    env.DISCORD_GUILD_ID = "another-guild"
    wrong_target = run(probe.cleanup_discord_delta_probe(env, state, RUN_ID))
    assert wrong_target["error"] == "dirty_manifest_target_mismatch"
    assert len(calls) == count

    env.DISCORD_GUILD_ID = GUILD_ID
    control["notion_archive_statuses"] = [200]
    recovered = run(probe.cleanup_discord_delta_probe(env, state, RUN_ID))
    assert recovered["ok"] is True
    assert recovered["dirty"] is False
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    manifest = run(state.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "discord_delta_sync"
    assert manifest["outcome"] == "recovered"
    assert "delta_checkpoint" not in manifest


@pytest.mark.parametrize("query_result", [None, {"id": "another-page"}])
def test_delta_delete_query_mismatch_stops_before_archive(monkeypatch, query_result):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    query = discord_notion_sync._notion_query_by_message_id
    count = 0

    async def fail_delete_query(env, database_id, event_id):
        nonlocal count
        count += 1
        if count == 4:
            return query_result
        return await query(env, database_id, event_id)

    monkeypatch.setattr(discord_notion_sync, "_notion_query_by_message_id", fail_delete_query)
    env = make_env()
    result = run(probe.run_discord_delta_probe(env, StateStore(env), RUN_ID))
    assert count == 4
    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["stages"]["delta_delete_diff"] == 500
    assert result["stages"]["notion_archive"] == 200
    assert not any("another-page" in path for _, path in calls)
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True


def test_delta_requires_archive_readback_before_cleanup(monkeypatch):
    _, pages, _, _ = install_api_stub(monkeypatch)

    async def ineffective_archive(env, page_id):
        return True

    monkeypatch.setattr(discord_notion_sync, "_notion_archive_page", ineffective_archive)
    env = make_env()
    state = StateStore(env)
    result = run(probe.run_discord_delta_probe(env, state, RUN_ID))
    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["stages"]["delta_delete_diff"] == 200
    assert result["stages"]["scenario_verification"] == 500
    assert result["stages"]["notion_archive"] == 200
    assert pages[PAGE_ID]["archived"] is True
    manifest = run(state.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "failed_clean"


@pytest.mark.parametrize("operation", ["cancel", "delete"])
def test_delta_lost_terminal_response_cleans_without_claiming_success(monkeypatch, operation):
    events, pages, _, _ = install_api_stub(monkeypatch)
    original_fetch = e2e_discord_probe.fetch
    lost = False

    async def lose_response(url, options=None):
        nonlocal lost
        request = options or {}
        payload = json.loads(request.get("body") or "{}")
        response = await original_fetch(url, options)
        terminal = (
            request.get("method") == "DELETE" if operation == "delete"
            else request.get("method") == "PATCH" and payload.get("status") == 4
        )
        if terminal and not lost:
            lost = True
            raise RuntimeError("private terminal response")
        return response

    monkeypatch.setattr(e2e_discord_probe, "fetch", lose_response)
    env = make_env()
    result = run(probe.run_discord_delta_probe(env, StateStore(env), RUN_ID))
    assert lost is True
    assert result["ok"] is False
    assert result["dirty"] is False
    assert "private terminal response" not in json.dumps(result)
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True


def test_delta_source_read_error_never_triggers_deletion_diff(monkeypatch):
    install_api_stub(monkeypatch)
    original_fetch = e2e_discord_probe.fetch
    deleted = False

    async def fail_confirmation(url, options=None):
        nonlocal deleted
        method = (options or {}).get("method")
        if method == "GET" and deleted:
            deleted = False
            return Response("", status=403)
        response = await original_fetch(url, options)
        if method == "DELETE":
            deleted = True
        return response

    monkeypatch.setattr(e2e_discord_probe, "fetch", fail_confirmation)
    env = make_env()
    result = run(probe.run_discord_delta_probe(env, StateStore(env), RUN_ID))
    assert result["ok"] is False
    assert result["stages"]["delta_delete_source_read"] == 403
    assert "delta_delete_diff" not in result["stages"]


def test_delta_delete_failure_retains_retry_in_snapshot_and_queue(monkeypatch):
    env = probe._DeltaEnv(make_env())
    state = probe._DeltaState(DISCORD_EVENT_ID)
    state.snapshot = {DISCORD_EVENT_ID: json.dumps({"status": "4"})}
    calls = []

    async def delete(env, event_id, token):
        calls.append(event_id)
        return len(calls) > 1

    monkeypatch.setattr(discord_notion_sync, "_sync_discord_event_delete", delete)
    first = run(discord_notion_sync._apply_discord_event_diff(env, state, []))
    assert first["ok"] is False
    assert json.loads(state.snapshot[DISCORD_EVENT_ID])["_pending_sync"] == {"id": DISCORD_EVENT_ID, "op": "delete"}
    assert state.queue == [{"op": "delete", "id": DISCORD_EVENT_ID}]
    second = run(discord_notion_sync._apply_discord_event_diff(env, state, []))
    assert second["ok"] is True
    assert second["deleted"] == 0
    assert second["processed_changes"] == 1
    assert state.queue == []
    assert calls == [DISCORD_EVENT_ID, DISCORD_EVENT_ID]


@pytest.mark.parametrize("status,deleted", [("1", 1), ("3", 0), ("4", 1)])
def test_disappeared_completed_event_is_preserved(monkeypatch, status, deleted):
    env = probe._DeltaEnv(make_env())
    state = probe._DeltaState(DISCORD_EVENT_ID)
    state.snapshot = {DISCORD_EVENT_ID: json.dumps({"status": status})}
    calls = []

    async def delete(env, event_id, token):
        calls.append(event_id)
        return True

    monkeypatch.setattr(discord_notion_sync, "_sync_discord_event_delete", delete)
    result = run(discord_notion_sync._apply_discord_event_diff(env, state, []))
    assert result["ok"] is True
    assert result["deleted"] == deleted
    assert result["processed_changes"] == deleted
    assert calls == [DISCORD_EVENT_ID] * deleted


def test_delta_queue_retries_failed_owned_update_with_unchanged_snapshot(monkeypatch):
    env = make_env()
    # 外部失敗後のqueue再試行は、通常処理に対するローカル回帰として確認する。
    event = {
        "id": DISCORD_EVENT_ID, "guild_id": GUILD_ID, "status": 1,
        "name": "queue fixture", "description": "fixture",
    }
    state = probe._DeltaState(DISCORD_EVENT_ID)
    attempts = []

    async def upsert(env, event, token):
        attempts.append(event["id"])
        return len(attempts) > 1

    monkeypatch.setattr(discord_notion_sync, "_sync_discord_event_upsert", upsert)
    first = run(discord_notion_sync._apply_discord_event_diff(
        probe._DeltaEnv(env), state, [event],
    ))
    assert first["ok"] is False
    assert state.queue == [{"op": "upsert", "id": DISCORD_EVENT_ID}]
    second = run(discord_notion_sync._apply_discord_event_diff(
        probe._DeltaEnv(env), state, [event],
    ))
    assert second["ok"] is True
    assert second["created"] == second["updated"] == 0
    assert second["processed_changes"] == 1
    assert state.queue == []
    assert attempts == [DISCORD_EVENT_ID, DISCORD_EVENT_ID]
    assert env.STATE_KV.put_calls == []


@pytest.mark.parametrize("suffix", ["", "/cleanup", "/prepare", "/advance", "/resume"])
@pytest.mark.parametrize("case,status", [
    ("disabled", 404), ("unauthorized", 401), ("get", 405),
    ("missing_run", 400), ("valid", 200), ("exception", 409),
])
def test_delta_routes_enforce_auth_method_run_id(monkeypatch, suffix, case, status):
    calls = []

    async def fake_probe(env, state, run_id=None, expected_run_id=None, prepare_only=False, pause_after_update=False):
        calls.append(run_id or expected_run_id)
        if case == "exception":
            raise RuntimeError("private exception")
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_discord_delta_probe", fake_probe)
    monkeypatch.setattr(e2e_entry, "cleanup_discord_delta_probe", fake_probe)
    monkeypatch.setattr(e2e_entry, "resume_discord_delta_probe", fake_probe)
    worker = e2e_entry.Default()
    worker.env = make_env()
    worker.env.E2E_DISCORD_DELTA_ENABLED = "false" if case == "disabled" else "true"
    worker.env.INTERNAL_API_TOKEN = "test-token"
    worker.env.SYNC_DO_LOCK_ENABLED = "false"
    headers = dict(ROUTE_HEADERS)
    if case == "unauthorized":
        headers.pop("Authorization")
    if case == "missing_run":
        headers.pop("X-E2E-Run-ID")
    response = run(worker.fetch(Request(
        f"https://bot.test/admin/e2e/discord-delta-sync{suffix}",
        method="GET" if case == "get" else "POST", headers=headers,
    )))
    assert response.status == status
    assert calls == ([RUN_ID] if case in ("valid", "exception") else [])
    assert "private exception" not in run(response.text())


@pytest.mark.parametrize("dirty", [False, True])
def test_delta_status_exposes_only_masked_manifest(monkeypatch, dirty):
    install_api_stub(monkeypatch, notion_archive_statuses=[500] * 5 if dirty else None)
    env = make_env()
    state = StateStore(env)
    result = run(probe.run_discord_delta_probe(env, state, RUN_ID))
    assert result["ok"] is not dirty
    worker = e2e_entry.Default()
    worker.env = env
    env.INTERNAL_API_TOKEN = "test-token"
    env.E2E_DISCORD_DELTA_ENABLED = "true"
    env.SYNC_DO_LOCK_ENABLED = "false"
    response = run(worker.fetch(Request(
        "https://bot.test/admin/e2e/status", headers=ROUTE_HEADERS,
    )))
    assert response.status == 200
    data = response_json(response)
    assert data["scenario_routes_enabled"]["discord_delta"] is True
    assert data["scenarios"]["discord_delta"]["dirty"] is dirty
    if not dirty:
        assert data["scenarios"]["discord_delta"]["outcome"] == "passed"
    for raw in (DISCORD_EVENT_ID, PAGE_ID, GUILD_ID, "delta_checkpoint", "snapshot", "bot-token"):
        assert raw not in json.dumps(data)


def test_delta_checkpoint_failure_stops_later_updates_and_cleans(monkeypatch):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)

    async def fail_checkpoint(*args):
        raise RuntimeError("private storage failure")

    monkeypatch.setattr(state, "put_e2e_delta_checkpoint", fail_checkpoint)
    result = run(probe.run_discord_delta_probe(env, state, RUN_ID))
    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["stages"]["delta_create_checkpoint_write"] == 409
    assert "delta_unchanged_diff" not in result["stages"]
    assert "private storage failure" not in json.dumps(result)
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert not any(method == "PATCH" and "/scheduled-events/" in path for method, path in calls)


def test_delta_list_request_supplies_discord_user_agent(monkeypatch):
    _, _, _, _ = install_api_stub(monkeypatch)
    original = discord_notion_sync.fetch

    async def require_user_agent(url, options=None):
        if "scheduled-events?" in url:
            assert (options or {})["headers"]["User-Agent"].startswith("DiscordBot (")
        return await original(url, options)

    monkeypatch.setattr(discord_notion_sync, "fetch", require_user_agent)
    env = make_env()
    assert run(probe.run_discord_delta_probe(env, StateStore(env), RUN_ID))["ok"] is True


@pytest.mark.parametrize("delay,expected_calls", [(0.1, 4), (11, 1), (-1, 1), (None, 1)])
def test_discord_list_retries_only_bounded_retry_after(monkeypatch, delay, expected_calls):
    calls, waits = [], []

    async def limited(url, options=None):
        calls.append(url)
        return Response(json.dumps({"retry_after": delay}), status=429)

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(discord_notion_sync, "fetch", limited)
    monkeypatch.setattr(discord_notion_sync.asyncio, "sleep", sleep)
    events, error = run(discord_notion_sync._list_discord_scheduled_events(make_env()))
    assert events is None and error == "discord_list_failed:429"
    assert len(calls) == expected_calls
    assert len(waits) == expected_calls - 1


def test_discord_list_succeeds_after_rate_limit(monkeypatch):
    calls = []

    async def limited(url, options=None):
        calls.append(url)
        return Response('{"retry_after":0}', status=429) if len(calls) == 1 else Response('[]', status=200)

    monkeypatch.setattr(discord_notion_sync, "fetch", limited)
    assert run(discord_notion_sync._list_discord_scheduled_events(make_env())) == ([], None)
    assert len(calls) == 2


def test_list_failure_before_notion_apply_is_failed_clean(monkeypatch):
    events, pages, _, _ = install_api_stub(monkeypatch)

    async def blocked(env):
        return None, "discord_list_failed:403"

    monkeypatch.setattr(probe, "_list_discord_scheduled_events", blocked)
    env = make_env()
    result = run(probe.run_discord_delta_probe(env, StateStore(env), RUN_ID))
    assert result["ok"] is False
    assert result["dirty"] is False
    assert result["stages"]["delta_create_list"] == 403
    assert events == pages == {}


@pytest.mark.parametrize("apply_started", [False, True])
def test_legacy_list_failure_cleanup_requires_preapply_evidence(monkeypatch, apply_started):
    install_api_stub(monkeypatch)
    env = make_env()
    state = StateStore(env)
    stages = {"delta_create_list": 500, "application_apply": 500}
    if apply_started:
        stages["delta_create_checkpoint_read"] = 200
    manifest = {
        "version": 1, "kind": "discord_delta_sync", "dirty": True, "run_id": RUN_ID,
        "stage": "cleanup_failed", "stages": stages,
        "discord_event_id": DISCORD_EVENT_ID,
        "target_fingerprints": probe._target_fingerprints(env.DISCORD_GUILD_ID, env.NOTION_EVENT_INTERNAL_ID),
        "create_attempted": {"discord_event": True, "notion_page": True},
    }
    run(state.put_e2e_manifest("discord_delta", manifest))
    result = run(probe.cleanup_discord_delta_probe(env, state, RUN_ID))
    assert result["ok"] is not apply_started
    assert result["dirty"] is apply_started
