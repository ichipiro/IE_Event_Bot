"""通常同期の共通ロックを検査し、結果KVだけをrun単位で所有する。"""

import asyncio
import re
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from state import StateStore

SERVICE = "sync_lock"
KIND = "sync_lock_contention"
SOURCES = ("manual", "cron", "all")
ROUNDS = tuple((source, failure) for failure in (False, True) for source in SOURCES)
KEYS = ("result:sync_discord_notion", "result:sync_all", "sync:last_epoch")
OWNER_FIELDS = ("run_id", "scope_id", "target_fingerprints")
RUN_PATTERN = re.compile(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}")
CONTROL_NAME = "e2e:sync-lock-control"
ROUND_TIMEOUT = 10


class ProbeError(Exception):
    pass


class InjectedFailure(Exception):
    pass


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def valid_lock_transition(previous: dict, manifest: dict) -> bool:
    """DOでrun・scope・接続対象の差し替えとclean後の再実行を拒否する。"""
    if manifest["dirty"]:
        targets = manifest.get("target_fingerprints")
        if not (
            RUN_PATTERN.fullmatch(str(manifest.get("run_id") or ""))
            and re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("scope_id") or ""))
            and isinstance(targets, dict)
            and set(targets) == {"state_scope_sha256"}
            and re.fullmatch(r"[0-9a-f]{64}", str(targets["state_scope_sha256"]))
        ):
            return False
    if manifest["dirty"]:
        hashes = manifest.get("result_hashes")
        stage = manifest.get("stage")
        if not isinstance(hashes, list) or len(hashes) > len(ROUNDS):
            return False
        for index, values in enumerate(hashes):
            keys = {KEYS[1], KEYS[2]} if ROUNDS[index][0] == "all" else {KEYS[0]}
            if (
                not isinstance(values, dict)
                or set(values) != keys
                or any(
                    not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                    for v in values.values()
                )
            ):
                return False
        if stage not in (
            "lock_testing",
            "lock_prepared",
            "lock_verifying",
            "lock_verified",
        ):
            return False
        if stage in ("lock_prepared", "lock_verified") and len(hashes) != len(ROUNDS):
            return False
    if previous.get("dirty"):
        if manifest["dirty"]:
            old_hashes = previous.get("result_hashes", [])
            if hashes[: len(old_hashes)] != old_hashes or len(hashes) - len(
                old_hashes
            ) not in (0, 1):
                return False
            transitions = {
                "lock_testing": {"lock_testing", "lock_prepared", "lock_verifying"},
                "lock_prepared": {"lock_verifying"},
                "lock_verifying": {"lock_verifying", "lock_verified"},
                "lock_verified": {"lock_verifying"},
            }
            if stage not in transitions.get(str(previous.get("stage") or ""), set()):
                return False
            if (
                len(hashes) != len(old_hashes)
                and previous.get("stage") != "lock_testing"
            ):
                return False
            return all(previous.get(key) == manifest.get(key) for key in OWNER_FIELDS)
        return (
            manifest.get("last_run_id") == previous.get("run_id")
            and manifest.get("resource_fingerprints")
            == previous.get("target_fingerprints")
            and manifest.get("scope_sha256") == _digest(previous["scope_id"])
            and manifest.get("outcome")
            == (
                "passed" if previous.get("stage") == "lock_verified" else "failed_clean"
            )
        )
    if not manifest["dirty"]:
        return previous == manifest
    return (
        previous.get("last_run_id") != manifest["run_id"]
        and manifest.get("stage") == "lock_testing"
        and manifest.get("result_hashes") == []
    )


class OwnedResultKV:
    """各roundの固定3キー以外に触れず、書込み内容はhashだけを記録する。"""

    def __init__(self, store, owner, index):
        if type(index) is not int or not 0 <= index < len(ROUNDS):
            raise ProbeError("sync_lock_round_invalid")
        self.store = store
        self.owner = deepcopy(owner)
        self.prefix = f"e2e:sync_lock:{owner['run_id']}:{owner['scope_id']}:{index}:"
        self.writes: dict[str, str] = {}
        self.reads = 0

    async def check(self, key):
        current = await self.store.get_e2e_manifest(SERVICE)
        if (
            key not in KEYS
            or not current
            or not current.get("dirty")
            or any(current.get(k) != self.owner.get(k) for k in OWNER_FIELDS)
        ):
            raise ProbeError("sync_lock_owner_mismatch")

    async def get(self, key):
        await self.check(key)
        self.reads += 1
        return await self.store.env.STATE_KV.get(self.prefix + key)

    async def put(self, key, value):
        await self.check(key)
        if not isinstance(value, str) or len(value.encode()) > 4096:
            raise ProbeError("sync_lock_value_invalid")
        await self.store.env.STATE_KV.put(self.prefix + key, value)
        self.writes[key] = _digest(value)

    def state(self):
        return StateStore(
            SimpleNamespace(
                STATE_KV=self, SYNC_COORDINATOR=None, KV_RESULT_MIN_WRITE_SECONDS="0"
            )
        )


async def _lock_state(store):
    result = await store._sync_do_rpc(
        store._sync_do_stub(store.env.SYNC_COORDINATOR), "status"
    )
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or not isinstance(result.get("lock"), dict)
    ):
        raise ProbeError("sync_lock_status_failed")
    return result["lock"]


async def _round(store, owner, index, invoke):
    source, failure = ROUNDS[index]
    kv = OwnedResultKV(store, owner, index)
    entered, proceed = asyncio.Event(), asyncio.Event()
    calls = 0

    async def body():
        nonlocal calls
        calls += 1
        entered.set()
        await proceed.wait()
        if failure:
            raise InjectedFailure()
        return {"ok": True, "probe": source}

    task = asyncio.create_task(invoke(source, kv.state(), body))
    try:
        await asyncio.wait_for(entered.wait(), ROUND_TIMEOUT)
        lock = await _lock_state(store)
        if not lock.get("owner"):
            raise ProbeError("sync_lock_owner_missing")
        reads = kv.reads

        # 保持側の本体を止めたまま3経路へ入る。拒否側は本体・結果KVへ到達しない。
        async def forbidden():
            raise ProbeError("sync_lock_rejected_body_called")

        for contender in SOURCES:
            result, status = await invoke(contender, kv.state(), forbidden)
            rejected = (
                (
                    status == 200
                    and result.get("status") == "in_progress_skip"
                    and result.get("lock", {}).get("locked") is True
                )
                if contender == "all"
                else (status == 409 and result.get("error") == "sync_in_progress")
            )
            if not rejected or kv.writes or kv.reads != reads:
                raise ProbeError("sync_lock_rejection_failed")
            if (await _lock_state(store)).get("owner") != lock["owner"]:
                raise ProbeError("sync_lock_owner_changed")
        proceed.set()
        try:
            result, status = await task
        except InjectedFailure:
            if not failure:
                raise
        else:
            if failure or status != 200 or result.get("ok") is not True:
                raise ProbeError("sync_lock_completion_failed")
    finally:
        # 失敗時も保持側のfinallyを待ってから回収へ進む。
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if calls != 1 or (await _lock_state(store)).get("owner"):
        raise ProbeError("sync_lock_release_failed")
    if failure:
        if kv.writes:
            raise ProbeError("sync_lock_failure_result_written")

        async def recovered():
            return {"ok": True, "probe": source}

        result, status = await invoke(source, kv.state(), recovered)
        if (
            status != 200
            or result.get("ok") is not True
            or (await _lock_state(store)).get("owner")
        ):
            raise ProbeError("sync_lock_recovery_failed")
    expected = {KEYS[1], KEYS[2]} if source == "all" else {KEYS[0]}
    if set(kv.writes) != expected:
        raise ProbeError("sync_lock_result_missing")
    return kv.writes


async def _run_phase(env, store, run_id, phase, invoke):
    target = {"state_scope_sha256": _digest(str(env.E2E_STATE_SCOPE))}
    owner = await store.get_e2e_manifest(SERVICE)
    if phase == "prepare":
        if owner and (owner.get("dirty") or owner.get("last_run_id") == run_id):
            return {
                "ok": False,
                "dirty": bool(owner.get("dirty")),
                "error": "environment_dirty",
            }
        owner = {
            "kind": KIND,
            "version": 1,
            "dirty": True,
            "run_id": run_id,
            "scope_id": uuid4().hex,
            "target_fingerprints": target,
            "stage": "lock_testing",
            "stages": {},
            "result_hashes": [],
        }
        await store.put_e2e_manifest(SERVICE, owner)
        for index in range(len(ROUNDS)):
            hashes = await asyncio.wait_for(
                _round(store, owner, index, invoke), ROUND_TIMEOUT * 2
            )
            owner["result_hashes"].append(hashes)
            owner["stages"][f"lock_round_{index}"] = 200
            await store.put_e2e_manifest(SERVICE, owner)
        owner["stage"] = "lock_prepared"
        await store.put_e2e_manifest(SERVICE, owner)
        return {"ok": True, "dirty": True, "status": "prepared"}
    if not owner or not owner.get("dirty"):
        if (
            phase == "cleanup"
            and owner
            and owner.get("last_run_id") == run_id
            and owner.get("resource_fingerprints") == target
        ):
            return {"ok": True, "dirty": False}
        raise ProbeError("sync_lock_not_prepared")
    if owner.get("run_id") != run_id or owner.get("target_fingerprints") != target:
        raise ProbeError("sync_lock_owner_mismatch")
    if (await _lock_state(store)).get("owner"):
        raise ProbeError("sync_lock_still_held")
    if phase == "verify":
        owner["stage"] = "lock_verifying"
        await store.put_e2e_manifest(SERVICE, owner)
        hashes = owner.get("result_hashes", [])
        if len(hashes) != len(ROUNDS):
            raise ProbeError("sync_lock_incomplete")
        for index, expected in enumerate(hashes):
            kv = OwnedResultKV(store, owner, index)
            for key in KEYS:
                value = await kv.get(key)
                actual = (
                    None
                    if value is None or str(value) in ("jsnull", "jsundefined")
                    else _digest(str(value))
                )
                if actual != expected.get(key):
                    raise ProbeError("sync_lock_not_ready")
        owner["stage"] = "lock_verified"
        owner["stages"]["lock_result_readback"] = 200
        await store.put_e2e_manifest(SERVICE, owner)
        return {"ok": True, "dirty": True, "stage": "lock_verified"}
    for index in range(len(ROUNDS)):
        kv = OwnedResultKV(store, owner, index)
        for key in KEYS:
            await kv.check(key)
            await env.STATE_KV.delete(kv.prefix + key)
    owner["stages"]["lock_kv_cleanup"] = 200
    clean = {
        "kind": KIND,
        "version": 1,
        "dirty": False,
        "last_run_id": run_id,
        "resource_fingerprints": target,
        "scope_sha256": _digest(owner["scope_id"]),
        "stages": owner["stages"],
        "outcome": "passed" if owner["stage"] == "lock_verified" else "failed_clean",
    }
    return {"ok": True, "dirty": False, "_clean_manifest": clean}


async def run_sync_lock_probe(env, store, run_id, phase, invoke) -> dict:
    """固定の制御ロックでE2Eを直列化し、globalは通常同期自身に取得させる。"""
    if not RUN_PATTERN.fullmatch(run_id) or phase not in (
        "prepare",
        "verify",
        "cleanup",
    ):
        return {"ok": False, "error": "sync_lock_request_invalid"}
    if not (
        store.enabled()
        and store.e2e_manifest_enabled()
        and str(getattr(env, "E2E_STATE_SCOPE", "") or "").strip()
    ):
        return {"ok": False, "error": "sync_lock_bindings_required"}
    if str(
        getattr(env, "SYNC_DO_LOCK_ENABLED", "true")
    ).lower() != "true" or StateStore.is_kv_sync_cooldown_enabled(env):
        return {"ok": False, "error": "sync_lock_configuration_invalid"}
    stub = env.SYNC_COORDINATOR.getByName(CONTROL_NAME)
    control_owner = f"{run_id}-{uuid4().hex}"
    lock = await store._sync_do_rpc(
        stub, "acquire", {"owner": control_owner, "ttl_seconds": 300}
    )
    if not lock or lock.get("ok") is not True:
        return {"ok": False, "error": "sync_lock_probe_busy"}
    result = {}
    try:
        result = await asyncio.wait_for(
            _run_phase(env, store, run_id, phase, invoke), 45
        )
    except Exception as exc:
        code = str(exc) if isinstance(exc, ProbeError) else "sync_lock_probe_failed"
        result = {"ok": False, "dirty": True, "error": code}
    finally:
        try:
            released = await store._sync_do_rpc(
                stub, "release", {"owner": control_owner}
            )
            status = await store._sync_do_rpc(stub, "status")
            if (
                not released
                or released.get("ok") is not True
                or not status
                or status.get("ok") is not True
                or not isinstance(status.get("lock"), dict)
                or status["lock"].get("owner") == control_owner
            ):
                raise ProbeError("sync_lock_control_release_failed")
        except Exception:
            result = {
                "ok": False,
                "dirty": True,
                "error": "sync_lock_control_release_failed",
            }
    # 制御ロックの解放を確認するまで所有記録をcleanにしない。
    clean = result.pop("_clean_manifest", None)
    if clean:
        try:
            await store.put_e2e_manifest(SERVICE, clean)
        except Exception:
            return {
                "ok": False,
                "dirty": True,
                "error": "sync_lock_cleanup_record_failed",
            }
    return result
