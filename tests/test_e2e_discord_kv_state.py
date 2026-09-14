"""通常StateStoreのKV分離を、HTTP・実DOロジック・代替KVで検証する。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from e2e_discord_kv_state import KEYS, SERVICE, OwnedDiscordKV
from e2e_entry import Default
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


RUN = "E2E-20260911T150000Z-1234abcd"
OTHER = "E2E-20260911T150001Z-1234abcd"
PATH = "/admin/e2e/discord-state"


class KV(MemoryKV):
    fail_put = None
    fail_delete = None

    async def put(self, key, value):
        if self.fail_put and key.endswith(self.fail_put):
            raise RuntimeError("injected_put_failure")
        await super().put(key, value)

    async def delete(self, key):
        if self.fail_delete and key.endswith(self.fail_delete):
            raise RuntimeError("injected_delete_failure")
        self.data.pop(key, None)


def run(coro):
    return asyncio.run(coro)


def environment():
    return SimpleNamespace(
        STATE_KV=KV({KEYS[0]: "original snapshot", KEYS[1]: "original queue", "unrelated": "preserved"}),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(),
        E2E_STATE_SCOPE="test-discord-state", INTERNAL_API_TOKEN="test-token",
        CF_VERSION_METADATA=SimpleNamespace(tag=RUN, id="test-version"),
    )


async def request(env, phase="", *, run_id=RUN, token="test-token", method="POST", version=RUN):
    worker = Default()
    worker.env = env
    response = await worker.fetch(Request("https://bot.test" + PATH + phase, method=method, headers={
        "Authorization": "Bearer " + token, "X-E2E-Run-ID": run_id, "X-E2E-Version-Tag": version,
    }))
    text = await response.text()
    return response.status, json.loads(text) if text.startswith("{") else {"error": text}


def adapter(env):
    store = StateStore(env)
    owner = run(store.get_e2e_manifest(SERVICE))
    assert isinstance(owner, dict)
    return OwnedDiscordKV(store, owner)


def saved_manifest(env):
    value = run(StateStore(env).get_e2e_manifest(SERVICE))
    assert isinstance(value, dict)
    return value


def test_separate_requests_restore_and_cleanup_only_owned_keys():
    env = environment()
    original = dict(env.STATE_KV.data)
    assert run(request(env))[0] == 200
    owned = adapter(env)
    assert set(env.STATE_KV.data) == set(original) | {owned.prefix + key for key in KEYS}
    assert type(owned.state()) is StateStore
    assert run(request(env, "/verify")) == (200, {"ok": True, "dirty": True, "stage": "state_verified", "run_id": RUN})
    assert run(request(env, "/cleanup")) == (200, {"ok": True, "dirty": False, "run_id": RUN})
    assert env.STATE_KV.data == original
    clean = run(StateStore(env).get_e2e_manifest(SERVICE))
    assert clean and clean["outcome"] == "passed"
    assert "scope_id" not in clean and "event_ids" not in clean
    assert run(request(env, "/cleanup"))[0] == 200
    assert run(request(env))[0] == 409  # 同じrunをclean後に再利用しない。
    with pytest.raises(ValueError, match="owner_mismatch"):
        run(owned.state().set_discord_snapshot({}))


@pytest.mark.parametrize("kwargs,status", [
    ({"token": "wrong"}, 401), ({"method": "GET"}, 405),
    ({"run_id": "invalid"}, 400), ({"version": ""}, 409), ({"version": OTHER}, 409),
])
def test_ingress_rejects_before_writes(kwargs, status):
    env = environment()
    before = dict(env.STATE_KV.data)
    assert run(request(env, **kwargs))[0] == status
    assert env.STATE_KV.data == before and env.STATE_KV.put_calls == []


@pytest.mark.parametrize("binding", ["STATE_KV", "SYNC_COORDINATOR"])
def test_missing_binding_fails_closed(binding):
    env = environment()
    setattr(env, binding, None)
    assert run(request(env))[0] in (500, 503)


@pytest.mark.parametrize("phase", ["", "/verify", "/cleanup"])
def test_other_run_cannot_change_state(phase):
    env = environment()
    run(request(env))
    before = deepcopy(env.STATE_KV.data)
    env.CF_VERSION_METADATA.tag = OTHER
    assert run(request(env, phase, run_id=OTHER, version=OTHER))[0] == 409
    assert env.STATE_KV.data == before


@pytest.mark.parametrize("key,value", [
    ("gcal_watch_state", {}), ("result:sync_discord_notion", {}),
    (KEYS[0], {"foreign": '{}'}), (KEYS[0], {"foreign": 'invalid'}),
    (KEYS[1], [{"id": "foreign", "op": "delete"}]),
])
def test_forbidden_key_or_resource_cannot_be_written(key, value):
    env = environment()
    run(request(env))
    before = dict(env.STATE_KV.data)
    with pytest.raises(ValueError):
        run(adapter(env).state().put_json(key, value))
    assert env.STATE_KV.data == before


@pytest.mark.parametrize("key", KEYS)
def test_partial_save_failure_stays_dirty_and_can_be_cleaned(key):
    env = environment()
    original = dict(env.STATE_KV.data)
    env.STATE_KV.fail_put = key
    assert run(request(env))[0] == 409
    assert saved_manifest(env)["dirty"] is True
    assert run(request(env, "/verify"))[0] == 409
    assert run(request(env, "/cleanup"))[0] == 200
    assert env.STATE_KV.data == original
    assert saved_manifest(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize("key", KEYS)
def test_delete_failure_preserves_owner_for_retry(key):
    env = environment()
    original = dict(env.STATE_KV.data)
    run(request(env))
    env.STATE_KV.fail_delete = key
    assert run(request(env, "/cleanup"))[0] == 409
    assert saved_manifest(env)["dirty"] is True
    env.STATE_KV.fail_delete = None
    assert run(request(env, "/cleanup"))[0] == 200
    assert env.STATE_KV.data == original


def test_stale_kv_read_is_not_reported_as_verified():
    env = environment()
    run(request(env))
    owned = adapter(env)
    env.STATE_KV.data[owned.prefix + KEYS[0]] = "{}"
    assert run(request(env, "/verify")) == (409, {"ok": False, "dirty": True, "error": "discord_state_not_ready", "run_id": RUN})
    assert saved_manifest(env)["stage"] == "state_verifying"


def test_failed_reverification_does_not_reuse_previous_success():
    env = environment()
    run(request(env))
    assert run(request(env, "/verify"))[0] == 200
    owned = adapter(env)
    env.STATE_KV.data[owned.prefix + KEYS[0]] = "invalid json"
    assert run(request(env, "/verify"))[0] == 409
    assert run(request(env, "/cleanup"))[0] == 200
    assert saved_manifest(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize("field", ["run_id", "scope_id", "event_ids", "target_fingerprints"])
def test_do_rejects_owner_replacement(field):
    env = environment()
    run(request(env))
    store = StateStore(env)
    manifest = run(store.get_e2e_manifest(SERVICE))
    assert isinstance(manifest, dict)
    before = deepcopy(manifest)
    manifest[field] = {"run_id": OTHER, "scope_id": "f" * 32, "event_ids": ["other"],
                       "target_fingerprints": {"state_scope_sha256": "f" * 64}}[field]
    with pytest.raises(RuntimeError, match="manifest_write_failed"):
        run(store.put_e2e_manifest(SERVICE, manifest))
    assert run(store.get_e2e_manifest(SERVICE)) == before


def test_global_lock_blocks_state_probe():
    env = environment()
    worker = Default()
    worker.env = env
    lock = run(worker._acquire_sync_lock(source="normal-sync"))
    assert run(request(env))[0] == 409
    assert env.STATE_KV.put_calls == []
    run(worker._release_sync_lock(lock["owner"]))
    assert run(request(env))[0] == 200


def test_foreign_saved_queue_is_rejected_before_use():
    env = environment()
    run(request(env))
    owned = adapter(env)
    env.STATE_KV.data[owned.prefix + KEYS[1]] = json.dumps([{"id": "foreign", "op": "delete"}])
    with pytest.raises(ValueError, match="value_forbidden"):
        run(owned.state().get_json(KEYS[1]))
    assert run(request(env, "/cleanup"))[0] == 200


def test_target_change_rejects_cleanup_and_preserves_keys():
    env = environment()
    run(request(env))
    before = dict(env.STATE_KV.data)
    env.E2E_STATE_SCOPE = "changed-target"
    assert run(request(env, "/cleanup"))[0] == 409
    assert env.STATE_KV.data == before


def test_state_status_does_not_expose_scope_or_event_ids():
    env = environment()
    run(request(env))
    manifest = saved_manifest(env)
    worker = Default()
    worker.env = env
    response = run(worker.fetch(Request("https://bot.test/admin/e2e/status", headers={
        "Authorization": "Bearer test-token",
    })))
    text = run(response.text())
    assert response.status == 200
    assert manifest["scope_id"] not in text
    assert all(event_id not in text for event_id in manifest["event_ids"])
    assert json.loads(text)["scenarios"][SERVICE]["dirty"] is True


@pytest.mark.parametrize("phase", ["prepare", "cleanup"])
def test_manifest_save_failure_preserves_recovery_boundary(monkeypatch, phase):
    env = environment()
    original = dict(env.STATE_KV.data)
    if phase == "cleanup":
        run(request(env))
    storage = env.SYNC_COORDINATOR.stub.durable_object.ctx.storage
    original_put = storage.put

    async def fail_manifest(key, value):
        if key == "e2e:manifest:discord_state":
            raise RuntimeError("injected_manifest_failure")
        await original_put(key, value)

    monkeypatch.setattr(storage, "put", fail_manifest)
    assert run(request(env, "/cleanup" if phase == "cleanup" else ""))[0] == 409
    assert env.STATE_KV.data == original
    if phase == "cleanup":
        assert saved_manifest(env)["dirty"] is True
        monkeypatch.setattr(storage, "put", original_put)
        assert run(request(env, "/cleanup"))[0] == 200
    else:
        assert run(StateStore(env).get_e2e_manifest(SERVICE)) is None
        assert env.STATE_KV.put_calls == []


def test_state_scope_unset_disables_route():
    env = environment()
    env.E2E_STATE_SCOPE = ""
    assert run(request(env))[0] == 404
    assert env.STATE_KV.put_calls == []


@pytest.mark.parametrize('mutation', ['foreign_event', 'notification'])
def test_embedded_retry_cannot_escape_owned_state(mutation):
    from discord_retry_state import PENDING_FIELD

    env = environment()
    assert run(request(env))[0] == 200
    kv = adapter(env)
    event_id = kv.owner['event_ids'][0]
    op: dict = {'id': event_id, 'op': 'upsert'}
    if mutation == 'foreign_event':
        op['id'] = 'foreign'
    else:
        op['notification'] = {'channel_id': 'unowned'}
    value = json.dumps({event_id: json.dumps({'id': event_id, PENDING_FIELD: op})})
    before = dict(env.STATE_KV.data)
    with pytest.raises(ValueError, match='discord_state_value_forbidden'):
        run(kv.put(KEYS[0], value))
    assert env.STATE_KV.data == before
