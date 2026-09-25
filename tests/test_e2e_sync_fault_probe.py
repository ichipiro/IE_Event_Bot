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


async def prepare_all(env):
    result = await request(env)
    for _ in probe.CASES[1:]:
        if result[0] != 200:
            return result
        result = await request(env, "/advance")
    return result


def manifest(env):
    value = run(StateStore(env).get_e2e_manifest(probe.SERVICE))
    assert isinstance(value, dict)
    return value


def test_all_faults_and_expiry_readback_cleanup(env):
    original = dict(env.STATE_KV.data)
    assert run(prepare_all(env))[0] == 200
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
    assert run(prepare_all(env))[0] == 409


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
    assert run(prepare_all(env))[0] == status


def test_partial_write_failure_is_owned_and_cleanable(env):
    env.STATE_KV.fail_put = True
    assert run(prepare_all(env))[1]["error"] == "sync_faults_probe_failed"
    assert manifest(env)["dirty"] is True
    env.STATE_KV.fail_put = False
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_verify_invalidation_and_cleanup_retry(env):
    assert run(prepare_all(env))[0] == 200
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
    assert run(prepare_all(env))[0] == 200
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
    assert run(prepare_all(env))[1]["error"] == "sync_faults_probe_busy"
    assert run(store.get_e2e_manifest(probe.SERVICE)) is None


def test_ttl_failure_awaits_old_finally(env, monkeypatch):
    async def failure(store):
        raise RuntimeError("private diagnostics")

    monkeypatch.setattr(probe, "_wait_expired", failure)
    assert run(prepare_all(env))[1]["error"] == "sync_faults_probe_failed"
    assert not run(probe._lock_state(StateStore(env))).get("owner")
    assert run(request(env, "/cleanup"))[0] == 200


def test_control_release_failure_keeps_dirty(env, monkeypatch):
    assert run(prepare_all(env))[0] == 200
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
    assert run(prepare_all(env))[0] == 200
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


def test_each_request_runs_one_case_and_requires_all_before_verify(env):
    assert run(request(env))[0] == 200
    assert set(manifest(env)["hashes"]) == {probe.CASES[0]}
    before = dict(env.STATE_KV.data)
    assert run(request(env, "/verify"))[1]["error"] == "sync_faults_incomplete"
    assert env.STATE_KV.data == before
    for count in range(2, len(probe.CASES) + 1):
        assert run(request(env, "/advance"))[0] == 200
        assert set(manifest(env)["hashes"]) == set(probe.CASES[:count])
    assert run(request(env, "/advance"))[1]["error"] == "sync_faults_advance_forbidden"
    assert run(request(env, "/verify"))[0] == 200
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "passed"


def test_interrupted_case_cannot_be_replayed_or_skipped(env, monkeypatch):
    assert run(request(env))[0] == 200
    original = probe._kv_case
    calls = []

    async def interrupted(kv):
        calls.append(kv.case)
        await kv.put(probe.QUEUE, "[]")
        raise TimeoutError()

    monkeypatch.setattr(probe, "_kv_case", interrupted)
    status, result = run(request(env, "/advance"))
    assert status == 409 and result["error"] == "sync_faults_phase_timeout"
    monkeypatch.setattr(probe, "_kv_case", original)
    before = dict(env.STATE_KV.data)
    assert run(request(env, "/advance"))[1]["error"] == "sync_faults_advance_forbidden"
    assert env.STATE_KV.data == before
    assert calls == [probe.CASES[1]]
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_slow_cases_fit_separate_request_budgets(env, monkeypatch):
    # 7ケース各7秒とTTL 11秒をモデル化。合算60秒でも各要求は50秒以内。
    elapsed = [0]
    durations = []
    original_wait = asyncio.wait_for
    original_kv = probe._kv_case
    original_ttl = probe._ttl_case

    async def slow_kv(kv):
        result = await original_kv(kv)
        elapsed[0] += 7
        return result

    async def slow_ttl(kv, invoke):
        result = await original_ttl(kv, invoke)
        elapsed[0] += 11
        return result

    async def deadline(coro, timeout):
        start = elapsed[0]
        result = await original_wait(coro, timeout)
        if timeout == 50:
            duration = elapsed[0] - start
            durations.append(duration)
            if duration > timeout:
                raise TimeoutError()
        return result

    monkeypatch.setattr(probe, "_kv_case", slow_kv)
    monkeypatch.setattr(probe, "_ttl_case", slow_ttl)
    monkeypatch.setattr(probe.asyncio, "wait_for", deadline)
    assert run(prepare_all(env))[0] == 200
    assert durations == [7] * 7 + [11]
    assert run(request(env, "/verify"))[0] == 200
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "passed"


@pytest.mark.parametrize(
    "args", [{"run_id": OTHER}, {"version": OTHER}, {"token": "bad"}]
)
def test_advance_rejects_foreign_owner_version_or_token(env, args):
    assert run(request(env))[0] == 200
    before = deepcopy(manifest(env)), dict(env.STATE_KV.data)
    assert run(request(env, "/advance", **args))[0] in (401, 409)
    assert (manifest(env), env.STATE_KV.data) == before


def test_do_rejects_skipped_case_or_fake_completion(env):
    assert run(request(env))[0] == 200
    owner = manifest(env)
    for hashes, stage in [
        ({**owner["hashes"], probe.CASES[2]: "a" * 64}, "fault_partial"),
        (owner["hashes"], "fault_prepared"),
        ({**owner["hashes"], probe.CASES[1]: "a" * 64}, "fault_partial"),
    ]:
        changed = {**owner, "hashes": hashes, "stage": stage}
        with pytest.raises(RuntimeError, match="write_failed"):
            run(StateStore(env).put_e2e_manifest(probe.SERVICE, changed))
    assert manifest(env) == owner
