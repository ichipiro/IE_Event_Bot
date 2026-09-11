"""HTTP間の準備・続行と、続行中断後の重複防止をローカルで確認する。"""

import asyncio
import json
from copy import deepcopy
from hashlib import sha256

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
    assert "delta_claim_revision" not in manifest


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


@pytest.mark.parametrize("phase", ["prepare", "advance", "resume"])
def test_delta_version_gate_rejects_stale_worker_before_io(monkeypatch, phase):
    _, _, calls, _ = install_api_stub(monkeypatch)
    worker = e2e_entry.Default()
    worker.env = make_env()
    worker.env.INTERNAL_API_TOKEN = "test-token"
    worker.env.E2E_DISCORD_DELTA_ENABLED = "true"
    headers = {**ROUTE_HEADERS, "X-E2E-Version-Tag": RUN_ID}
    response = run(worker.fetch(Request(
        f"https://bot.test/admin/e2e/discord-delta-sync/{phase}", method="POST", headers=headers,
    )))
    assert response.status == 409
    assert response_json(response)["error"] == "worker_version_mismatch"
    assert calls == []


@pytest.mark.parametrize("phase", ["prepare", "advance", "resume"])
@pytest.mark.parametrize("expected", ["old-version", "invalid", "missing-tag"])
def test_same_tag_version_id_mismatch_rejected_before_io(monkeypatch, phase, expected):
    _, _, calls, _ = install_api_stub(monkeypatch)
    worker = e2e_entry.Default()
    worker.env = make_env()
    worker.env.INTERNAL_API_TOKEN = "test-token"
    worker.env.E2E_DISCORD_DELTA_ENABLED = "true"
    worker.env.CF_VERSION_METADATA = {"id": "new-version", "tag": RUN_ID}
    digest = sha256(b"old-version").hexdigest() if expected == "old-version" else "invalid"
    headers = {**ROUTE_HEADERS, "X-E2E-Version-ID-SHA256": digest}
    if expected != "missing-tag":
        headers["X-E2E-Version-Tag"] = RUN_ID
    response = run(worker.fetch(Request(
        f"https://bot.test/admin/e2e/discord-delta-sync/{phase}", method="POST", headers=headers,
    )))
    assert response.status == 409
    assert response_json(response)["error"] == "worker_version_mismatch"
    assert calls == []


def test_version_id_gate_allows_continuation_after_metadata_change(monkeypatch):
    events, pages, _, _ = install_api_stub(monkeypatch)
    env = make_env()
    env.INTERNAL_API_TOKEN = "test-token"
    env.E2E_DISCORD_DELTA_ENABLED = "true"
    env.SYNC_DO_LOCK_ENABLED = "true"
    for phase, version in [("prepare", "first"), ("advance", "first"),
                           ("advance", "second"), ("resume", "second"), ("resume", "second")]:
        worker = e2e_entry.Default()
        worker.env = env
        env.CF_VERSION_METADATA = {"id": version, "tag": RUN_ID}
        headers = {**ROUTE_HEADERS, "X-E2E-Version-Tag": RUN_ID,
                   "X-E2E-Version-ID-SHA256": sha256(version.encode()).hexdigest()}
        response = run(worker.fetch(Request(
            f"https://bot.test/admin/e2e/discord-delta-sync/{phase}", method="POST", headers=headers,
        )))
        assert response.status == 200
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True


@pytest.mark.parametrize("hide_canceled", [False, True])
def test_update_checkpoint_survives_lost_response_and_recreated_objects(monkeypatch, hide_canceled):
    events, pages, calls, _ = install_api_stub(monkeypatch, hide_canceled_events=hide_canceled)
    env = make_env()
    assert request(env, "prepare").status == 200
    store = StateStore(env)
    prepared = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(prepared, dict)
    advanced = request(env, "advance")
    assert advanced.status == 200
    assert response_json(advanced)["status"] == "updated"
    assert response_json(advanced)["dirty"] is True
    manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["stage"] == "delta_updated"
    assert manifest["delta_checkpoint"]["revision"] == 3
    assert manifest["delta_checkpoint"]["queue"] == []
    assert events[DISCORD_EVENT_ID]["description"].endswith("\nE2E delta update")
    assert pages[PAGE_ID].get("archived") is not True
    assert not any(method == "DELETE" for method, _ in calls)

    stub = env.SYNC_COORDINATOR.stub
    restarted = SyncCoordinator()
    restarted.ctx, restarted.env = stub.durable_object.ctx, stub.durable_object.env
    stub.durable_object = restarted
    # 応答を受け取れず再送しても、更新操作は繰り返さない。
    before = len(calls)
    replay = request(env, "advance")
    assert response_json(replay)["status"] == "updated"
    assert all(method == "GET" or path.endswith("/query") for method, path in calls[before:])
    # 読戻し済みでも古い準備段階のclaimでは新しい段階を取得できない。
    assert run(store.claim_e2e_delta_resume({**prepared, "revision": 1})) is False
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(store.put_e2e_manifest("discord_delta", prepared))

    resumed = request(env, "resume")
    assert resumed.status == 200
    result = response_json(resumed)
    assert result["dirty"] is False
    for key in ("delta_http_advance", "delta_http_resume", "delta_state_persisted", "delta_state_isolated"):
        assert result["stages"][key] == 200
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 1
    assert sum(method == "PATCH" and "/scheduled-events/" in path for method, path in calls) == 2
    assert env.STATE_KV.put_calls == []
    manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "passed"
    assert "delta_checkpoint" not in manifest
    before = list(calls)
    assert response_json(request(env, "advance"))["status"] == "already_completed"
    assert calls == before


@pytest.mark.parametrize("failure", ["source_changed", "page_changed", "revision", "stage"])
def test_updated_checkpoint_rejects_changed_resources_and_state(monkeypatch, failure):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    assert request(env, "advance").status == 200
    if failure == "source_changed":
        events[DISCORD_EVENT_ID]["description"] += " changed"
    elif failure == "page_changed":
        pages[PAGE_ID]["parent"] = {"database_id": "other"}
    else:
        storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage
        manifest = run(StateStore(env).get_e2e_manifest("discord_delta"))
        assert isinstance(manifest, dict)
        if failure == "revision":
            manifest["delta_checkpoint"]["revision"] = 2
        else:
            manifest["stage"] = "delta_resuming"
        storage.data["e2e:manifest:discord_delta"] = manifest
    before = len(calls)
    assert request(env, "resume").status == 409
    assert all(method == "GET" or path.endswith("/query") for method, path in calls[before:])
    if failure in ("revision", "stage"):
        assert len(calls) == before


def test_interrupted_update_stays_claimed_until_cleanup(monkeypatch):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200

    class Interrupted(BaseException):
        pass

    async def interrupt(*args, **kwargs):
        raise Interrupted()

    monkeypatch.setattr(StateStore, "pause_e2e_delta_resume", interrupt)
    with pytest.raises(Interrupted):
        request(env, "advance")
    manifest = run(StateStore(env).get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["stage"] == "delta_resuming"
    assert manifest["delta_checkpoint"]["revision"] == 3
    before = list(calls)
    assert request(env, "advance").status == 409
    assert request(env, "resume").status == 409
    assert calls == before
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(StateStore(env).put_e2e_manifest("discord_delta", {**manifest, "stage": "delta_updated"}))
    assert request(env, "cleanup").status == 200
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True


def test_old_pause_cannot_release_next_request_claim(monkeypatch):
    install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    store = StateStore(env)
    prepared = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(prepared, dict)
    assert request(env, "advance").status == 200
    updated = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(updated, dict)
    old_owner = {**prepared, "revision": 1}
    new_owner = {**updated, "revision": 3}
    assert run(store.claim_e2e_delta_resume(new_owner)) is True
    storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage
    before = deepcopy(storage.data)
    # 遅れて届いた前段階の保存要求では、次段階のclaimを解除しない。
    with pytest.raises(RuntimeError, match="e2e_delta_pause_failed"):
        run(store.pause_e2e_delta_resume(old_owner, updated["stages"], {}))
    stale_checkpoint = {**updated["delta_checkpoint"], "revision": 4}
    with pytest.raises(RuntimeError, match="e2e_delta_checkpoint_write_failed"):
        run(store.put_e2e_delta_checkpoint(old_owner, stale_checkpoint))
    assert storage.data == before
    assert run(store.claim_e2e_delta_resume(new_owner)) is False
    assert request(env, "cleanup").status == 200


@pytest.mark.parametrize("failure", ["write", "verification"])
def test_failed_advance_cleans_up_without_claiming_success(monkeypatch, failure):
    events, pages, _, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    if failure == "write":
        async def failed_pause(*args, **kwargs):
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(StateStore, "pause_e2e_delta_resume", failed_pause)
    else:
        original = probe._verify_owned_page

        async def failed_read(env, event, page_id, run_id, stages, retries, phase, **kwargs):
            if phase == "delta_after_update":
                return False
            return await original(env, event, page_id, run_id, stages, retries, phase, **kwargs)

        monkeypatch.setattr(probe, "_verify_owned_page", failed_read)
    response = request(env, "advance")
    assert response_json(response)["ok"] is False
    assert response_json(response)["dirty"] is False
    assert response_json(response)["stages"]["delta_http_advance"] == 500
    manifest = run(StateStore(env).get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["outcome"] == "failed_clean"
    assert "delta_checkpoint" not in manifest
    assert "delta_claim_revision" not in manifest
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True


@pytest.mark.parametrize("http_entry", [True, False])
def test_overlapping_resumes_allow_only_one_writer(monkeypatch, http_entry):
    """HTTP入口ロックと、入口を介さないDO claimを別々に競合させる。"""
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = make_env()
    assert request(env, "prepare").status == 200
    assert request(env, "advance").status == 200
    original_changes = probe._verify_delta_changes

    async def scenario():
        claimed = asyncio.Event()
        release = asyncio.Event()

        async def held_changes(*args, **kwargs):
            claimed.set()
            await release.wait()
            return await original_changes(*args, **kwargs)

        monkeypatch.setattr(probe, "_verify_delta_changes", held_changes)
        store = StateStore(env)
        manifest = await store.get_e2e_manifest("discord_delta")
        original_read = store.get_e2e_manifest
        if not http_entry:
            # 両要求が同じ準備済みrevisionを読み取った状況からDO claimを競合させる。
            async def stale_read(service):
                return deepcopy(manifest)
            monkeypatch.setattr(store, "get_e2e_manifest", stale_read)

        async def invoke():
            if not http_entry:
                return await probe.resume_discord_delta_probe(env, store, RUN_ID)
            worker = e2e_entry.Default()
            worker.env = env
            response = await worker.fetch(Request(
                "https://bot.test/admin/e2e/discord-delta-sync/resume",
                method="POST", headers=ROUTE_HEADERS,
            ))
            return {**json.loads(await response.text()), "http_status": response.status}

        winner = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(claimed.wait(), timeout=2)
            before = list(calls)
            rejected = await asyncio.wait_for(invoke(), timeout=2)
            assert rejected["ok"] is False
            if http_entry:
                assert rejected["http_status"] == 409
                assert rejected["error"] == "e2e_lock_unavailable"
                assert calls == before
            else:
                assert rejected["error"] == "delta_resume_conflict"
                assert all(method == "GET" or path.endswith("/query") for method, path in calls[len(before):])
                monkeypatch.setattr(store, "get_e2e_manifest", original_read)
        finally:
            release.set()
        return await asyncio.wait_for(winner, timeout=2)

    assert run(scenario())["ok"] is True
    assert events == {}
    assert pages[PAGE_ID]["archived"] is True
    assert sum(method == "DELETE" and "/scheduled-events/" in path for method, path in calls) == 1
    assert sum(method == "PATCH" and "/scheduled-events/" in path for method, path in calls) == 2
    assert sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 1
