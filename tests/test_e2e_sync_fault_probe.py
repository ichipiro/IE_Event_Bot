"""障害注入・所有権・別HTTP読戻し・失敗後の回収を検証する。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import e2e_sync_fault_probe as probe
import sync_lock_do
from e2e_entry import Default
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_sync_lock_probe import OTHER, RUN, environment


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(monkeypatch):
    env = environment()
    env.E2E_SYNC_FAULTS_ENABLED = "true"
    now = [1000.0]
    monkeypatch.setattr(sync_lock_do, "time", SimpleNamespace(time=lambda: now[0]))

    async def expire(store):
        now[0] += 11

    async def no_delay():
        pass

    monkeypatch.setattr(probe, "_wait_write", no_delay)
    monkeypatch.setattr(probe, "_wait_expired", expire)
    return env


async def request(
    env, suffix="", *, run_id=RUN, version=RUN, token="test-token", method="POST"
):
    worker = Default()
    worker.env = env
    response = await worker.fetch(
        Request(
            "https://bot.test/admin/e2e/sync-faults" + suffix,
            method=method,
            headers={
                "Authorization": "Bearer " + token,
                "X-E2E-Run-ID": run_id,
                "X-E2E-Version-Tag": version,
            },
        )
    )
    text = await response.text()
    return response.status, json.loads(text) if text.startswith("{") else {
        "error": text
    }


def manifest(env):
    value = run(StateStore(env).get_e2e_manifest(probe.SERVICE))
    assert isinstance(value, dict)
    return value


def test_all_faults_and_expiry_readback_cleanup(env):
    original = dict(env.STATE_KV.data)
    assert run(request(env))[0] == 200
    owner = manifest(env)
    assert set(owner["hashes"]) == set(probe.CASES)
    evidence = {
        c: json.loads(
            env.STATE_KV.data[
                probe.FaultKV(StateStore(env), owner, c).prefix + "evidence"
            ]
        )
        for c in probe.CASES
    }
    assert evidence["stale_queue_loss"]["pending_lost"] is False
    assert evidence["queue_fail_before"]["apply_calls"] == 3
    assert evidence["snapshot_fail_after"]["apply_calls"] == 2
    assert all(e["external_api_called"] is False for e in evidence.values())
    assert run(request(env, "/verify"))[0] == 200
    assert run(request(env, "/cleanup"))[0] == 200
    assert run(request(env, "/cleanup", version=OTHER))[0] == 200
    assert env.STATE_KV.data == original
    assert manifest(env)["outcome"] == "passed"
    assert run(request(env))[0] == 409


@pytest.mark.parametrize(
    "args,status",
    [
        ({"token": "bad"}, 401),
        ({"run_id": "bad"}, 400),
        ({"version": OTHER}, 409),
        ({"method": "GET"}, 405),
    ],
)
def test_reject_before_effects(env, args, status):
    assert run(request(env, **args))[0] == status
    assert run(StateStore(env).get_e2e_manifest(probe.SERVICE)) is None


@pytest.mark.parametrize(
    "key,value,status",
    [
        ("E2E_SYNC_FAULTS_ENABLED", "false", 404),
        ("STATE_KV", None, 503),
        ("SYNC_COORDINATOR", None, 503),
        ("E2E_STATE_SCOPE", "", 503),
        ("SYNC_DO_LOCK_ENABLED", "false", 503),
        ("KV_SYNC_COOLDOWN_ENABLED", "true", 503),
    ],
)
def test_gates(env, key, value, status):
    setattr(env, key, value)
    assert run(request(env))[0] == status


def test_partial_write_failure_is_owned_and_cleanable(env):
    env.STATE_KV.fail_put = True
    assert run(request(env))[1]["error"] == "sync_faults_probe_failed"
    assert manifest(env)["dirty"] is True
    env.STATE_KV.fail_put = False
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_verify_invalidation_and_cleanup_retry(env):
    assert run(request(env))[0] == 200
    assert run(request(env, "/verify"))[0] == 200
    key = next(k for k in env.STATE_KV.data if k.startswith("e2e:sync_faults:"))
    env.STATE_KV.data[key] = "stale"
    assert run(request(env, "/verify"))[1]["error"] == "sync_faults_not_ready"
    env.STATE_KV.fail_delete = True
    assert run(request(env, "/cleanup"))[0] == 409
    assert manifest(env)["dirty"] is True
    env.STATE_KV.fail_delete = False
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize(
    "field", ["run_id", "scope_id", "target_fingerprints", "hashes"]
)
def test_manifest_owner_and_evidence_immutable(env, field):
    assert run(request(env))[0] == 200
    owner = manifest(env)
    changed = deepcopy(owner)
    changed[field] = (
        {"state_scope_sha256": "b" * 64}
        if field == "target_fingerprints"
        else {**owner["hashes"], "stale_snapshot": "b" * 64}
        if field == "hashes"
        else OTHER
        if field == "run_id"
        else "a" * 32
    )
    with pytest.raises(RuntimeError, match="write_failed"):
        run(StateStore(env).put_e2e_manifest(probe.SERVICE, changed))
    before = dict(env.STATE_KV.data)
    assert run(request(env, "/cleanup", run_id=OTHER))[0] == 409
    assert env.STATE_KV.data == before
    adapter = probe.FaultKV(StateStore(env), owner, probe.CASES[0])
    with pytest.raises(probe.ProbeError, match="owner_mismatch"):
        run(adapter.put("unrelated", "{}"))
    assert run(request(env, "/cleanup"))[0] == 200
    with pytest.raises(probe.ProbeError, match="owner_mismatch"):
        run(adapter.put(probe.SNAPSHOT, "{}"))


def test_control_lock_rejects_parallel_probe(env):
    store = StateStore(env)
    stub = env.SYNC_COORDINATOR.getByName(probe.CONTROL_NAME)
    run(store._sync_do_rpc(stub, "acquire", {"owner": "other", "ttl_seconds": 300}))
    assert run(request(env))[1]["error"] == "sync_faults_probe_busy"
    assert run(store.get_e2e_manifest(probe.SERVICE)) is None


def test_ttl_failure_awaits_old_finally(env, monkeypatch):
    async def failure(store):
        raise RuntimeError("private diagnostics")

    monkeypatch.setattr(probe, "_wait_expired", failure)
    assert run(request(env))[1]["error"] == "sync_faults_probe_failed"
    assert not run(probe._lock_state(StateStore(env))).get("owner")
    assert run(request(env, "/cleanup"))[0] == 200


def test_control_release_failure_keeps_dirty(env, monkeypatch):
    assert run(request(env))[0] == 200
    assert run(request(env, "/verify"))[0] == 200
    stub = env.SYNC_COORDINATOR.getByName(probe.CONTROL_NAME)
    original = stub.sync_state

    async def broken(payload):
        if json.loads(payload)["action"] == "release":
            return json.dumps({"ok": True})
        return await original(payload)

    monkeypatch.setattr(stub, "sync_state", broken)
    assert (
        run(request(env, "/cleanup"))[1]["error"]
        == "sync_faults_control_release_failed"
    )
    assert manifest(env)["dirty"] is True


def test_expiry_poll_uses_do_time_at_boundary(monkeypatch):
    from e2e_sync_fault_probe import _wait_expired

    clocks = iter(
        [
            {"owner": "mine", "expires_at": 11, "now": 10},
            {"owner": "mine", "expires_at": 11, "now": 11},
        ]
    )
    sleeps = []

    async def status(store):
        return next(clocks)

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(probe, "_lock_state", status)
    monkeypatch.setattr(probe.asyncio, "sleep", sleep)
    run(_wait_expired(None))
    assert sleeps == [0.2]


def test_fixed_key_write_spacing_and_postwrite_failure(env, monkeypatch):
    assert run(request(env))[0] == 200
    adapter = probe.FaultKV(StateStore(env), manifest(env), probe.CASES[0])
    waits = []

    async def waited():
        waits.append(True)

    monkeypatch.setattr(probe, "_wait_write", waited)
    run(adapter.put(probe.QUEUE, "[]"))
    adapter.failure = (probe.QUEUE, "after")
    with pytest.raises(probe.InjectedSaveFailure):
        run(adapter.put(probe.QUEUE, "[1]"))
    assert waits == [True] and adapter.latest[probe.QUEUE] == "[1]"
    assert env.STATE_KV.data[adapter.prefix + probe.QUEUE] == "[1]"
