"""run状態の永続化、競合拒否、回収を実DOロジックとメモリstorageで確認する。"""

import json
from copy import deepcopy

import pytest

import discord_notion_sync
import e2e_discord_delta_probe as probe
from state import StateStore
from sync_lock_do import SyncCoordinator
from tests.test_e2e_discord_notion_probe import DISCORD_EVENT_ID, RUN_ID, make_env, run


def setup_state():
    env = make_env()
    store = StateStore(env)
    owner = {
        "run_id": RUN_ID, "discord_event_id": DISCORD_EVENT_ID,
        "target_fingerprints": {"guild_id_sha256": "a" * 64, "notion_database_id_sha256": "b" * 64},
    }
    manifest = {"kind": "discord_delta_sync", "version": 1, "dirty": True, **owner}
    run(store.put_e2e_manifest("discord_delta", manifest))
    return env, store, owner, manifest


def checkpoint(revision=1):
    return {"revision": revision, "snapshot": {}, "queue": [{"id": DISCORD_EVENT_ID, "op": "delete"}]}


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_retry_survives_new_state_store_and_coordinator(monkeypatch, operation):
    env, store, owner, _ = setup_state()
    state = probe._DeltaState(DISCORD_EVENT_ID, store=store, owner=owner)
    event = {"id": DISCORD_EVENT_ID, "status": 4, "name": "owned fixture"}
    run(state.restore())
    if operation == "delete":
        fingerprint = discord_notion_sync._fingerprint(event)
        assert isinstance(fingerprint, str)
        state.snapshot = {DISCORD_EVENT_ID: fingerprint}
        run(state.checkpoint())
    calls = []

    async def apply(*args):
        calls.append(operation)
        return len(calls) > 1

    monkeypatch.setattr(discord_notion_sync, f"_sync_discord_event_{operation}", apply)
    events = [event] if operation == "upsert" else []
    first = run(discord_notion_sync._apply_discord_event_diff(probe._DeltaEnv(env), state, events))
    assert first["ok"] is False
    run(state.checkpoint())
    manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    saved = manifest["delta_checkpoint"]
    assert saved["queue"] == [{"id": DISCORD_EVENT_ID, "op": operation}]

    # Pythonオブジェクトを作り直し、同じ保存領域だけを引き継ぐ。
    stub = env.SYNC_COORDINATOR.stub
    restarted = SyncCoordinator()
    restarted.ctx, restarted.env = stub.durable_object.ctx, stub.durable_object.env
    stub.durable_object = restarted
    restored = probe._DeltaState(DISCORD_EVENT_ID, store=StateStore(env), owner=owner)
    run(restored.restore())
    assert restored.snapshot == saved["snapshot"]
    assert restored.queue == saved["queue"]
    second = run(discord_notion_sync._apply_discord_event_diff(probe._DeltaEnv(env), restored, events))
    assert second["ok"] is True
    assert second["created"] == second["updated"] == second["deleted"] == 0
    assert second["processed_changes"] == 1
    run(restored.checkpoint())
    manifest = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(manifest, dict)
    assert manifest["delta_checkpoint"]["queue"] == []
    assert calls == [operation, operation]
    assert env.STATE_KV.put_calls == []
    assert set(restarted.ctx.storage.data) == {"e2e:manifest:discord_delta"}


@pytest.mark.parametrize("failure", [
    "run", "target", "event", "revision", "bool_revision", "foreign_snapshot",
    "foreign_fingerprint", "invalid_fingerprint", "foreign_queue", "extra_queue_field",
    "oversize_queue", "unknown_op", "oversize", "extra_field",
])
def test_rejected_checkpoint_preserves_previous_pair(failure):
    env, store, owner, _ = setup_state()
    run(store.put_e2e_delta_checkpoint(owner, checkpoint()))
    before = deepcopy(env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data)
    value = checkpoint(2)
    if failure == "run":
        owner["run_id"] = "E2E-20260911T120000Z-aaaaaaaa"
    elif failure == "target":
        owner["target_fingerprints"]["guild_id_sha256"] = "c" * 64
    elif failure == "event":
        owner["discord_event_id"] = "other"
        value["queue"] = []
    elif failure == "revision":
        value["revision"] = 1
    elif failure == "bool_revision":
        value["revision"] = True
    elif failure == "foreign_snapshot":
        value["snapshot"] = {"other": json.dumps({"id": "other"})}
    elif failure == "foreign_fingerprint":
        value["snapshot"] = {DISCORD_EVENT_ID: json.dumps({"id": "other"})}
    elif failure == "invalid_fingerprint":
        value["snapshot"] = {DISCORD_EVENT_ID: "invalid"}
    elif failure == "foreign_queue":
        value["queue"][0]["id"] = "other"
    elif failure == "extra_queue_field":
        value["queue"][0]["token"] = "forbidden"
    elif failure == "oversize_queue":
        value["queue"] *= 2
    elif failure == "unknown_op":
        value["queue"][0]["op"] = "unknown"
    elif failure == "oversize":
        value["snapshot"] = {DISCORD_EVENT_ID: json.dumps({"id": DISCORD_EVENT_ID, "name": "x" * 32768})}
    elif failure == "extra_field":
        value["extra"] = "forbidden"
    with pytest.raises(RuntimeError, match="e2e_delta_checkpoint_write_failed"):
        run(store.put_e2e_delta_checkpoint(owner, value))
    assert env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data == before


def test_lifecycle_preserves_checkpoint_until_owned_cleanup():
    env, store, owner, manifest = setup_state()
    run(store.put_e2e_delta_checkpoint(owner, checkpoint()))
    # fixtureの古いローカルmanifestで、新しいcheckpointを上書きしない。
    manifest["stage"] = "notion_page_found"
    run(store.put_e2e_manifest("discord_delta", manifest))
    saved = run(store.get_e2e_manifest("discord_delta"))
    assert isinstance(saved, dict)
    assert saved["delta_checkpoint"] == checkpoint()
    clean = {
        "kind": "discord_delta_sync", "version": 1, "dirty": False,
        "last_run_id": RUN_ID, "resource_fingerprints": owner["target_fingerprints"],
    }
    for invalid in (
        {**manifest, "run_id": "E2E-20260911T120000Z-aaaaaaaa"},
        {**manifest, "discord_event_id": "other"},
        {**manifest, "delta_checkpoint": checkpoint(2)},
        {**clean, "resource_fingerprints": {}},
    ):
        with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
            run(store.put_e2e_manifest("discord_delta", invalid))
    run(store.put_e2e_manifest("discord_delta", clean))
    assert run(store.get_e2e_manifest("discord_delta")) == clean
    with pytest.raises(RuntimeError, match="e2e_delta_checkpoint_write_failed"):
        run(store.put_e2e_delta_checkpoint(owner, checkpoint(2)))
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(store.put_e2e_manifest("discord_delta", manifest))
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(store.put_e2e_manifest("discord_delta", {
            **clean, "last_run_id": "E2E-20260911T120000Z-aaaaaaaa",
        }))
    assert run(store.get_e2e_manifest("discord_delta")) == clean
    assert DISCORD_EVENT_ID not in json.dumps(env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data)


def test_stale_adapter_and_corrupt_record_fail_before_apply(monkeypatch):
    _, store, owner, manifest = setup_state()
    adapter = probe._DeltaState(DISCORD_EVENT_ID, store=store, owner=owner)
    run(adapter.restore())
    run(store.put_e2e_delta_checkpoint(owner, checkpoint()))
    with pytest.raises(ValueError, match="revision_mismatch"):
        run(adapter.restore())

    async def corrupted_manifest(service):
        return {**manifest, "delta_checkpoint": {"revision": 1}}

    monkeypatch.setattr(store, "get_e2e_manifest", corrupted_manifest)
    with pytest.raises(ValueError, match="invalid_checkpoint"):
        run(adapter.restore())


def test_storage_failure_does_not_fall_back_to_shared_kv(monkeypatch):
    env, store, owner, _ = setup_state()
    run(store.put_e2e_delta_checkpoint(owner, checkpoint()))
    storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage
    before = deepcopy(storage.data)

    async def fail_put(*args):
        raise RuntimeError("fixture storage unavailable")

    monkeypatch.setattr(storage, "put", fail_put)
    with pytest.raises(RuntimeError):
        run(store.put_e2e_delta_checkpoint(owner, checkpoint(2)))
    assert storage.data == before
    env.SYNC_COORDINATOR = None
    with pytest.raises(RuntimeError, match="durable_object_required"):
        run(StateStore(env).put_e2e_delta_checkpoint(owner, checkpoint(2)))
    assert env.STATE_KV.put_calls == []
