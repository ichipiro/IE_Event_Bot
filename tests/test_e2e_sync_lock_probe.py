"""実ロック実装とHTTP経路を使い、同期本体・KVだけを代替する。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import e2e_sync_lock_probe as probe
from e2e_entry import Default
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace

RUN = "E2E-20260914T120000Z-1234abcd"
OTHER = "E2E-20260914T120001Z-1234abcd"
PATH = "/admin/e2e/sync-lock"


class KV(MemoryKV):
    fail_put = False
    fail_delete = False

    async def put(self, key, value):
        if self.fail_put:
            raise RuntimeError("private failure details")
        await super().put(key, value)

    async def delete(self, key):
        if self.fail_delete:
            raise RuntimeError("private failure details")
        self.data.pop(key, None)


class Namespace:
    def __init__(self):
        self.names = {}

    def getByName(self, name):
        if name not in self.names:
            self.names[name] = make_sync_coordinator_namespace()
        return self.names[name].getByName(name)


def environment():
    return SimpleNamespace(
        STATE_KV=KV(
            {"unrelated": "preserved", "result:sync_discord_notion": "previous"}
        ),
        SYNC_COORDINATOR=Namespace(),
        E2E_SYNC_LOCK_ENABLED="true",
        E2E_STATE_SCOPE="test-lock",
        SYNC_DO_LOCK_ENABLED="true",
        KV_SYNC_COOLDOWN_ENABLED="false",
        SYNC_ALL_INCLUDE_DISCORD_NOTION="true",
        INTERNAL_API_TOKEN="test-token",
        CF_VERSION_METADATA=SimpleNamespace(tag=RUN, id="test-version"),
    )


async def request(
    env, suffix="", *, token="test-token", run_id=RUN, method="POST", version=RUN
):
    worker = Default()
    worker.env = env
    response = await worker.fetch(
        Request(
            "https://bot.test" + PATH + suffix,
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


def run(coro):
    return asyncio.run(coro)


def manifest(env):
    value = run(StateStore(env).get_e2e_manifest(probe.SERVICE))
    assert isinstance(value, dict)
    return value


def test_six_rounds_reject_all_contenders_readback_and_cleanup():
    env = environment()
    original = dict(env.STATE_KV.data)
    assert run(request(env)) == (
        200,
        {"ok": True, "dirty": True, "status": "prepared", "run_id": RUN},
    )
    owner = manifest(env)
    assert len(owner["result_hashes"]) == 6
    assert len(owner["stages"]) == 6
    assert len(env.STATE_KV.data) == len(original) + 8
    assert run(request(env, "/verify"))[0] == 200
    assert run(request(env, "/cleanup"))[0] == 200
    assert run(request(env, "/cleanup", version=OTHER))[0] == 200
    assert env.STATE_KV.data == original
    clean = manifest(env)
    assert clean["outcome"] == "passed" and clean["dirty"] is False
    assert "scope_id" not in clean and "result_hashes" not in clean
    assert not run(probe._lock_state(StateStore(env))).get("owner")
    assert run(request(env))[0] == 503  # 同runの再作成を拒否


@pytest.mark.parametrize(
    "args,status",
    [
        ({"token": "wrong"}, 401),
        ({"method": "GET"}, 405),
        ({"run_id": "bad"}, 400),
        ({"version": OTHER}, 409),
    ],
)
def test_request_rejected_before_side_effects(args, status):
    env = environment()
    assert run(request(env, **args))[0] == status
    assert run(StateStore(env).get_e2e_manifest(probe.SERVICE)) is None
    assert len(env.STATE_KV.data) == 2


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("E2E_SYNC_LOCK_ENABLED", "false", 404),
        ("SYNC_DO_LOCK_ENABLED", "false", 503),
        ("KV_SYNC_COOLDOWN_ENABLED", "true", 503),
        ("E2E_STATE_SCOPE", "", 503),
        ("STATE_KV", None, 503),
        ("SYNC_COORDINATOR", None, 503),
    ],
)
def test_required_gates(field, value, status):
    env = environment()
    setattr(env, field, value)
    assert run(request(env))[0] == status


@pytest.mark.parametrize("mutation", ["run_id", "scope_id", "target_fingerprints"])
def test_do_rejects_owner_replacement(mutation):
    env = environment()
    assert run(request(env))[0] == 200
    owner = manifest(env)
    changed = deepcopy(owner)
    changed[mutation] = (
        {"state_scope_sha256": "a" * 64}
        if mutation == "target_fingerprints"
        else OTHER
        if mutation == "run_id"
        else "a" * 32
    )
    with pytest.raises(RuntimeError, match="write_failed"):
        run(StateStore(env).put_e2e_manifest(probe.SERVICE, changed))
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_reverify_failure_cancels_success_and_recovers_cleanly():
    env = environment()
    assert run(request(env))[0] == 200
    assert run(request(env, "/verify"))[0] == 200
    key = next(k for k in env.STATE_KV.data if k.startswith("e2e:"))
    env.STATE_KV.data[key] = "stale value"
    assert run(request(env, "/verify"))[1]["error"] == "sync_lock_not_ready"
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_save_failure_releases_lock_and_retains_cleanup_ownership():
    env = environment()
    env.STATE_KV.fail_put = True
    status, body = run(request(env))
    assert status == 409 and body["error"] == "sync_lock_probe_failed"
    assert manifest(env)["dirty"] is True
    assert not run(probe._lock_state(StateStore(env))).get("owner")
    env.STATE_KV.fail_put = False
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_cleanup_failure_and_target_mismatch_preserve_ownership():
    env = environment()
    assert run(request(env))[0] == 200
    assert run(request(env, "/verify"))[0] == 200
    original = dict(env.STATE_KV.data)
    env.E2E_STATE_SCOPE = "changed"
    assert run(request(env, "/cleanup"))[1]["error"] == "sync_lock_owner_mismatch"
    assert env.STATE_KV.data == original
    env.E2E_STATE_SCOPE = "test-lock"
    env.STATE_KV.fail_delete = True
    assert run(request(env, "/cleanup"))[0] == 409
    assert manifest(env)["dirty"] is True
    env.STATE_KV.fail_delete = False
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "passed"


def test_rejected_path_cannot_write_results(monkeypatch):
    env = environment()
    original = Default._invoke_lock_probe

    async def broken(self, source, state, body):
        result, status = await original(self, source, state, body)
        if status == 409:
            await state.put_text("result:sync_discord_notion", "corrupt")
        return result, status

    monkeypatch.setattr(Default, "_invoke_lock_probe", broken)
    assert run(request(env))[1]["error"] == "sync_lock_rejection_failed"
    assert not run(probe._lock_state(StateStore(env))).get("owner")
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_control_lock_prevents_concurrent_prepare():
    env = environment()
    store = StateStore(env)
    stub = env.SYNC_COORDINATOR.getByName(probe.CONTROL_NAME)
    run(store._sync_do_rpc(stub, "acquire", {"owner": "other", "ttl_seconds": 120}))
    assert run(request(env))[1]["error"] == "sync_lock_probe_busy"
    assert run(StateStore(env).get_e2e_manifest(probe.SERVICE)) is None
    status = run(store._sync_do_rpc(stub, "status"))
    assert isinstance(status, dict)
    assert status["lock"]["owner"] == "other"


def test_global_release_failure_is_detected_and_never_forced(monkeypatch):
    env = environment()

    async def failed_release(self, owner):
        return

    monkeypatch.setattr(Default, "_release_sync_lock", failed_release)
    assert run(request(env))[1]["error"] == "sync_lock_release_failed"
    held = run(probe._lock_state(StateStore(env)))["owner"]
    assert held
    assert run(request(env, "/cleanup"))[1]["error"] == "sync_lock_still_held"
    assert run(probe._lock_state(StateStore(env)))["owner"] == held
    store = StateStore(env)
    run(
        store._sync_do_rpc(
            store._sync_do_stub(env.SYNC_COORDINATOR), "release", {"owner": held}
        )
    )
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


def test_control_release_failure_does_not_mark_cleanup_clean(monkeypatch):
    env = environment()
    assert run(request(env))[0] == 200
    assert run(request(env, "/verify"))[0] == 200
    stub = env.SYNC_COORDINATOR.getByName(probe.CONTROL_NAME)
    original = stub.sync_state

    async def broken(payload):
        if json.loads(payload)["action"] == "release":
            return json.dumps({"ok": True})  # 応答だけ成功し実際には解放しない
        return await original(payload)

    monkeypatch.setattr(stub, "sync_state", broken)
    assert (
        run(request(env, "/cleanup"))[1]["error"] == "sync_lock_control_release_failed"
    )
    assert manifest(env)["dirty"] is True
    monkeypatch.setattr(stub, "sync_state", original)
    held = run(StateStore(env)._sync_do_rpc(stub, "status"))
    assert held
    run(StateStore(env)._sync_do_rpc(stub, "release", {"owner": held["lock"]["owner"]}))
    assert run(request(env, "/cleanup"))[0] == 200
    assert manifest(env)["outcome"] == "passed"


def test_probe_timeout_awaits_owner_finally(monkeypatch):
    env = environment()
    original = Default._invoke_lock_probe
    monkeypatch.setattr(probe, "ROUND_TIMEOUT", 0.01)

    async def delayed(self, source, state, body):
        async def wait_forever():
            await asyncio.Event().wait()

        return await original(self, source, state, wait_forever)

    monkeypatch.setattr(Default, "_invoke_lock_probe", delayed)
    assert run(request(env))[1]["error"] == "sync_lock_probe_failed"
    assert not run(probe._lock_state(StateStore(env))).get("owner")
    assert run(request(env, "/cleanup"))[0] == 200


def test_hash_replacement_and_clean_reopening_are_rejected():
    env = environment()
    assert run(request(env))[0] == 200
    owner = manifest(env)
    changed = deepcopy(owner)
    changed["result_hashes"][0][probe.KEYS[0]] = "a" * 64
    with pytest.raises(RuntimeError, match="write_failed"):
        run(StateStore(env).put_e2e_manifest(probe.SERVICE, changed))
    adapter = probe.OwnedResultKV(StateStore(env), owner, 0)
    with pytest.raises(probe.ProbeError, match="owner_mismatch"):
        run(adapter.put("unrelated", "forbidden"))
    assert run(request(env, "/cleanup"))[0] == 200
    with pytest.raises(probe.ProbeError, match="owner_mismatch"):
        run(adapter.put(probe.KEYS[0], "{}"))
    owner["stage"] = "lock_testing"
    owner["result_hashes"] = []
    with pytest.raises(RuntimeError, match="write_failed"):
        run(StateStore(env).put_e2e_manifest(probe.SERVICE, owner))
