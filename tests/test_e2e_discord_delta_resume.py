"""HTTP間の準備・続行と、続行中断後の重複防止をローカルで確認する。"""

from copy import deepcopy

import pytest

import e2e_discord_delta_probe as probe
import e2e_entry
from state import StateStore
from sync_lock_do import SyncCoordinator
from tests.fakes import Request
from tests.test_e2e_discord_notion_probe import (
    DISCORD_EVENT_ID, PAGE_ID, ROUTE_HEADERS, RUN_ID,
    install_api_stub, make_env, response_json, run,
)


def request(env, suffix):
    worker = e2e_entry.Default()
    worker.env = env
    env.INTERNAL_API_TOKEN = "test-token"
    env.E2E_DISCORD_DELTA_ENABLED = "true"
    env.SYNC_DO_LOCK_ENABLED = "true"
    return run(worker.fetch(Request(
        f"https://bot.test/admin/e2e/discord-delta-sync/{suffix}",
        method="POST", headers=ROUTE_HEADERS,
    )))


@pytest.mark.parametrize("hide_canceled", [False, True])
def test_prepare_and_resume_across_http_and_recreated_objects(monkeypatch, hide_canceled):
    events, pages, calls, _ = install_api_stub(monkeypatch, hide_canceled_events=hide_canceled)
    env = make_env()
    prepared = request(env, "prepare")
    assert prepared.status == 200
    assert response_json(prepared)["status"] == "prepared"
    assert response_json(prepared)["dirty"] is True
    assert len(events) == len(pages) == 1
    assert pages[PAGE_ID].get("archived") is not True
    assert not any(method == "DELETE" or (method == "PATCH" and "/scheduled-events/" in path)
                   for method, path in calls)
    manifest = run(StateStore(env).get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["stage"] == "delta_prepared"
    assert manifest["delta_checkpoint"]["revision"] == 1

    # Workerはrequestごとに新規作成。DOも保存領域だけ引き継いで作り直す。
    stub = env.SYNC_COORDINATOR.stub
    coordinator = SyncCoordinator()
    coordinator.ctx, coordinator.env = stub.durable_object.ctx, stub.durable_object.env
    stub.durable_object = coordinator
    resumed = request(env, "resume")
    assert resumed.status == 200
    assert response_json(resumed)["ok"] is True
    assert response_json(resumed)["dirty"] is False
    assert response_json(resumed)["stages"]["delta_http_resume"] == 200
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 1
    before = list(calls)
    replay = request(env, "resume")
    assert response_json(replay)["status"] == "already_completed"
    assert calls == before
    assert env.STATE_KV.put_calls == []
    manifest = run(StateStore(env).get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "passed"
    assert "delta_checkpoint" not in manifest


@pytest.mark.parametrize("failure", ["run", "target", "google", "phase", "revision", "queue"])
def test_resume_rejects_invalid_record_before_external_io(monkeypatch, failure):
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    store = StateStore(env)
    manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    before = list(calls)
    run_id = RUN_ID
    if failure == "run":
        run_id = "E2E-20260911T000000Z-aaaaaaaa"
    elif failure == "target":
        env.DISCORD_GUILD_ID = "other"
    elif failure == "google":
        env.DISCORD_TO_GOOGLE_SYNC_ENABLED = "true"
    else:
        if failure == "phase":
            manifest["stage"] = "cleanup_failed"
        elif failure == "revision":
            manifest["delta_checkpoint"]["revision"] = 2
        else:
            manifest["delta_checkpoint"]["queue"] = [{"id": DISCORD_EVENT_ID, "op": "upsert"}]

        async def invalid_record(service):
            return deepcopy(manifest)

        monkeypatch.setattr(store, "get_e2e_manifest", invalid_record)
    result = run(probe.resume_discord_delta_probe(env, store, run_id))
    assert result["ok"] is False
    assert calls == before


@pytest.mark.parametrize("failure", ["source_changed", "source_missing", "page_changed", "page_archived"])
def test_resume_verifies_owned_resources_before_writes(monkeypatch, failure):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    if failure == "source_changed":
        events[DISCORD_EVENT_ID]["description"] += " changed"
    elif failure == "source_missing":
        events.clear()
    elif failure == "page_changed":
        pages[PAGE_ID]["parent"] = {"database_id": "other"}
    else:
        pages[PAGE_ID]["archived"] = True
    before = len(calls)
    response = request(env, "resume")
    assert response.status == 409
    assert response_json(response)["error"] == "delta_resume_resource_mismatch"
    assert all(method == "GET" or path.endswith("/query") for method, path in calls[before:])


def test_claim_is_single_use_and_interrupted_resume_requires_cleanup(monkeypatch):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200

    class Interrupted(BaseException):
        pass

    async def interrupt(*args, **kwargs):
        raise Interrupted()

    store = StateStore(env)
    prepared_manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(prepared_manifest, dict)
    assert run(store.claim_e2e_delta_resume({**prepared_manifest, "notion_page_id": "other"})) is False
    monkeypatch.setattr(probe, "_verify_delta_changes", interrupt)
    with pytest.raises(Interrupted):
        request(env, "resume")
    before = list(calls)
    assert request(env, "resume").status == 409
    assert calls == before
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(store.put_e2e_manifest("discord_delta", prepared_manifest))
    manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["stage"] == "delta_resuming"
    assert run(store.claim_e2e_delta_resume(manifest)) is False
    assert request(env, "cleanup").status == 200
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert request(env, "resume").status == 500


def test_failed_prepare_can_be_cleaned_without_resume(monkeypatch):
    events, pages, _, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    assert request(env, "prepare").status == 409
    assert request(env, "cleanup").status == 200
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
