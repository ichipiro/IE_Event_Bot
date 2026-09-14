"""固定のKV障害モデルと実時間TTLを検査する。外部サービスは呼ばない。"""

import asyncio
import json
import re
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from discord_notion_sync import _apply_discord_event_diff, _fingerprint
from e2e_sync_lock_probe import (
    OWNER_FIELDS,
    RUN_PATTERN,
    ProbeError,
    _digest,
    _lock_state,
)
from state import StateStore

SERVICE = "sync_faults"
KIND = "sync_state_faults"
SNAPSHOT = "discord:snapshot"
QUEUE = "sync:discord_notion_queue"
RESULT = "result:sync_discord_notion"
KEYS = (SNAPSHOT, QUEUE, RESULT, "result:sync_all", "sync:last_epoch", "evidence")
CASES = (
    "stale_snapshot",
    "stale_queue_replay",
    "stale_queue_loss",
    "queue_fail_before",
    "queue_fail_after",
    "snapshot_fail_before",
    "snapshot_fail_after",
    "ttl_manual",
)
CONTROL_NAME = "e2e:sync-fault-control"


def valid_fault_transition(previous, value):
    """所有者・確定済みhash・終了状態の改変を拒否する。"""
    if value["dirty"]:
        hashes = value.get("hashes")
        stage = value.get("stage")
        if not (
            RUN_PATTERN.fullmatch(str(value.get("run_id") or ""))
            and re.fullmatch(r"[0-9a-f]{32}", str(value.get("scope_id") or ""))
            and isinstance(value.get("target_fingerprints"), dict)
            and set(value["target_fingerprints"]) == {"state_scope_sha256"}
            and re.fullmatch(
                r"[0-9a-f]{64}", str(value["target_fingerprints"]["state_scope_sha256"])
            )
            and isinstance(hashes, dict)
            and set(hashes) <= set(CASES)
            and all(
                isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
                for h in hashes.values()
            )
            and stage
            in ("fault_testing", "fault_prepared", "fault_verifying", "fault_verified")
        ):
            return False
        if stage in ("fault_prepared", "fault_verified") and set(hashes) != set(CASES):
            return False
    if previous.get("dirty"):
        if value["dirty"]:
            transitions = {
                "fault_testing": {"fault_testing", "fault_prepared", "fault_verifying"},
                "fault_prepared": {"fault_verifying"},
                "fault_verifying": {"fault_verifying", "fault_verified"},
                "fault_verified": {"fault_verifying"},
            }
            old = previous.get("hashes", {})
            return (
                all(previous.get(k) == value.get(k) for k in OWNER_FIELDS)
                and all(hashes.get(k) == h for k, h in old.items())
                and (previous.get("stage") == "fault_testing" or hashes == old)
                and stage in transitions.get(previous.get("stage"), set())
            )
        return (
            value.get("last_run_id") == previous.get("run_id")
            and value.get("resource_fingerprints")
            == previous.get("target_fingerprints")
            and value.get("scope_sha256") == _digest(previous["scope_id"])
            and value.get("outcome")
            == (
                "passed"
                if previous.get("stage") == "fault_verified"
                else "failed_clean"
            )
        )
    if not value["dirty"]:
        return previous == value
    return (
        previous.get("last_run_id") != value["run_id"]
        and value.get("stage") == "fault_testing"
        and hashes == {}
    )


async def _wait_write():
    # 同じKVキーへの書込みを1秒以上離す。注入失敗後の再試行も対象。
    await asyncio.sleep(1.05)


class FaultKV:
    """固定キーへ実書込みし、読取りだけを決定的な障害モデルに置換する。"""

    def __init__(self, store, owner, case):
        if case not in CASES:
            raise ProbeError("sync_faults_case_invalid")
        self.store, self.owner, self.case = store, deepcopy(owner), case
        self.prefix = f"e2e:sync_faults:{owner['run_id']}:{owner['scope_id']}:{case}:"
        self.latest: dict[str, str] = {}
        self.stale: dict[str, str] = {}
        self.failure: tuple[str, str] | None = None
        self.injected = 0

    async def check(self, key):
        current = await self.store.get_e2e_manifest(SERVICE)
        if (
            key not in KEYS
            or not current
            or not current.get("dirty")
            or any(current.get(k) != self.owner.get(k) for k in OWNER_FIELDS)
        ):
            raise ProbeError("sync_faults_owner_mismatch")

    async def get(self, key):
        await self.check(key)
        # 本物のKV伝播遅延の観測とは区別する。verifyだけがbindingから読む。
        return self.stale.get(key, self.latest.get(key))

    async def put(self, key, value):
        await self.check(key)
        if not isinstance(value, str) or len(value.encode()) > 8192:
            raise ProbeError("sync_faults_value_invalid")
        fault = self.failure
        if fault and key == fault[0]:
            self.failure = None
            self.injected += 1
            if fault[1] == "before":
                raise InjectedSaveFailure()
        if key in self.latest:
            await _wait_write()
        await self.store.env.STATE_KV.put(self.prefix + key, value)
        self.latest[key] = value
        if fault and key == fault[0] and fault[1] == "after":
            raise InjectedSaveFailure()

    def state(self):
        return StateStore(
            SimpleNamespace(
                STATE_KV=self, SYNC_COORDINATOR=None, KV_RESULT_MIN_WRITE_SECONDS="0"
            )
        )


class InjectedSaveFailure(Exception):
    pass


def _event(event_id):
    return {
        "id": event_id,
        "name": "fault probe",
        "status": 1,
        "scheduled_start_time": "2030-01-01T00:00:00Z",
    }


async def _kv_case(kv):
    """通常差分処理を通し、適用回数と古いqueueからの残件回復を検証する。"""
    first, second = _event("probe-1"), _event("probe-2")
    env = SimpleNamespace(
        DISCORD_TO_GOOGLE_SYNC_ENABLED="false", DISCORD_NOTION_MAX_CHANGES_PER_RUN="1"
    )
    calls = []
    fail_apply = False

    async def applied(env, event, token):
        calls.append(event["id"])
        return not fail_apply

    async def forbidden(*args):
        raise ProbeError("sync_faults_unexpected_external_operation")

    async def poll(events):
        # 毎回StateStoreを作り直す。外部適用は呼出し回数だけを記録する代替。
        return await _apply_discord_event_diff(
            env,
            kv.state(),
            events,
            upsert_runner=applied,
            delete_runner=forbidden,
            notify_runner=forbidden,
        )

    case = kv.case
    if case in ("stale_snapshot", "stale_queue_replay"):
        await kv.state().set_discord_snapshot({first["id"]: _fingerprint(first)})
        await kv.state().put_json(QUEUE, [])
        kv.stale = (
            {SNAPSHOT: "{}"}
            if case == "stale_snapshot"
            else {QUEUE: json.dumps([{"op": "upsert", "id": first["id"]}])}
        )
        await poll([first])
        kv.stale.clear()
        await poll([first])
        expected = [first["id"]]  # 既に成功済みの適用をもう一度行う。
        lost = False
    elif case == "stale_queue_loss":
        # 初回の通常処理で残件を作り、最新snapshotと古い空queueを組み合わせる。
        fail_apply = True
        await poll([first])
        calls.clear()
        kv.stale = {QUEUE: "[]"}
        await poll([first, second])
        kv.stale.clear()
        fail_apply = False
        for _ in range(3):
            result = await poll([first, second])
            if result["pending_changes"] == 0:
                break
        expected = [first["id"], first["id"], second["id"]]
        lost = first["id"] not in calls
        if lost:
            raise ProbeError("sync_faults_pending_lost")
    else:
        key = QUEUE if case.startswith("queue_") else SNAPSHOT
        kv.failure = (key, "after" if case.endswith("after") else "before")
        try:
            await poll([first, second])
        except InjectedSaveFailure:
            pass
        else:
            raise ProbeError("sync_faults_injection_missing")
        if kv.injected != 1 or calls != [first["id"]]:
            raise ProbeError("sync_faults_injection_order_invalid")
        for _ in range(3):
            result = await poll([first, second])
            if result["pending_changes"] == 0:
                break
        expected = (
            [first["id"], first["id"], second["id"]]
            if case == "queue_fail_before"
            else [first["id"], second["id"]]
            if case == "snapshot_fail_after"
            else [first["id"], second["id"], first["id"]]
        )
        lost = False
    if calls != expected or await kv.state().get_json(QUEUE) != []:
        raise ProbeError("sync_faults_observation_changed")
    return {
        "case": case,
        "apply_calls": len(calls),
        "injections": kv.injected,
        "pending_lost": lost,
        "external_api_called": False,
        "read_model": "injected",
        "queue_drained": True,
    }


async def _wait_expired(store):
    for _ in range(65):
        lock = await _lock_state(store)
        if lock.get("owner") and float(lock["now"]) >= float(lock["expires_at"]):
            return
        await asyncio.sleep(0.2)
    raise ProbeError("sync_faults_expiry_timeout")


async def _ttl_case(kv, invoke):
    """通常共通処理を10秒TTLで保持し、期限切れ後の新旧実行を検証する。"""
    entered, resume, new_entered, new_resume = (asyncio.Event() for _ in range(4))
    source = kv.case.removeprefix("ttl_")

    async def old_body():
        entered.set()
        await resume.wait()
        return {"ok": True, "writer": "old"}

    async def new_body():
        new_entered.set()
        await new_resume.wait()
        return {"ok": True, "writer": "new"}

    old = asyncio.create_task(invoke(source, kv.state(), old_body, lock_ttl_seconds=10))
    new = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        old_owner = (await _lock_state(kv.store))["owner"]
        await _wait_expired(kv.store)
        new = asyncio.create_task(
            invoke(source, kv.state(), new_body, lock_ttl_seconds=10)
        )
        await asyncio.wait_for(new_entered.wait(), 5)
        new_owner = (await _lock_state(kv.store))["owner"]
        if new_owner == old_owner:
            raise ProbeError("sync_faults_owner_unchanged")
        before = dict(kv.latest)
        resume.set()
        result, status = await old
        if (
            status != 409
            or result.get("error") != "sync_lock_lost"
            or kv.latest != before
            or (await _lock_state(kv.store))["owner"] != new_owner
        ):
            raise ProbeError("sync_faults_expired_owner_wrote")
        new_resume.set()
        result, status = await new
        expected = (
            {"result:sync_all", "sync:last_epoch"} if source == "all" else {RESULT}
        )
        if status != 200 or not result.get("ok") or set(kv.latest) != expected:
            raise ProbeError("sync_faults_replacement_failed")
    finally:
        tasks = [task for task in (old, new) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if (await _lock_state(kv.store)).get("owner"):
        raise ProbeError("sync_faults_lock_not_released")
    return {
        "case": kv.case,
        "expired_status": 409,
        "replacement_status": 200,
        "owner_protected": True,
        "external_api_called": False,
    }


async def _phase(env, store, run_id, phase, invoke):
    target = {"state_scope_sha256": _digest(str(env.E2E_STATE_SCOPE))}
    owner = await store.get_e2e_manifest(SERVICE)
    if phase == "prepare":
        if owner and (owner.get("dirty") or owner.get("last_run_id") == run_id):
            raise ProbeError("environment_dirty")
        if (await _lock_state(store)).get("owner"):
            raise ProbeError("sync_faults_lock_busy")
        owner = {
            "kind": KIND,
            "version": 1,
            "dirty": True,
            "run_id": run_id,
            "scope_id": uuid4().hex,
            "target_fingerprints": target,
            "stage": "fault_testing",
            "hashes": {},
            "stages": {},
        }
        await store.put_e2e_manifest(SERVICE, owner)
        for case in CASES:
            kv = FaultKV(store, owner, case)
            evidence = await (
                _ttl_case(kv, invoke) if case.startswith("ttl_") else _kv_case(kv)
            )
            # 通常状態も含めたhashを保存し、別HTTPで全固定キーを実KVから照合する。
            evidence["state_hashes"] = {k: _digest(v) for k, v in kv.latest.items()}
            text = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            await kv.put("evidence", text)
            owner["hashes"][case] = _digest(text)
            owner["stages"][case] = 200
            await store.put_e2e_manifest(SERVICE, owner)
        owner["stage"] = "fault_prepared"
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
        raise ProbeError("sync_faults_not_prepared")
    if owner.get("run_id") != run_id or owner.get("target_fingerprints") != target:
        raise ProbeError("sync_faults_owner_mismatch")
    if (await _lock_state(store)).get("owner"):
        raise ProbeError("sync_faults_lock_busy")
    if phase == "verify":
        owner["stage"] = "fault_verifying"
        await store.put_e2e_manifest(SERVICE, owner)
        if set(owner["hashes"]) != set(CASES):
            raise ProbeError("sync_faults_incomplete")
        for case in CASES:
            kv = FaultKV(store, owner, case)
            await kv.check("evidence")
            evidence = str(await env.STATE_KV.get(kv.prefix + "evidence"))
            if _digest(evidence) != owner["hashes"][case]:
                raise ProbeError("sync_faults_not_ready")
            hashes = json.loads(evidence)["state_hashes"]
            for key in KEYS[:-1]:
                await kv.check(key)
                value = await env.STATE_KV.get(kv.prefix + key)
                actual = (
                    None
                    if value is None or str(value) in ("jsnull", "jsundefined")
                    else _digest(str(value))
                )
                if actual != hashes.get(key):
                    raise ProbeError("sync_faults_not_ready")
        owner["stage"] = "fault_verified"
        owner["stages"]["fault_readback"] = 200
        await store.put_e2e_manifest(SERVICE, owner)
        return {"ok": True, "dirty": True, "stage": "fault_verified"}
    for case in CASES:
        kv = FaultKV(store, owner, case)
        for key in KEYS:
            await kv.check(key)
            await env.STATE_KV.delete(kv.prefix + key)
    owner["stages"]["fault_cleanup"] = 200
    clean = {
        "kind": KIND,
        "version": 1,
        "dirty": False,
        "last_run_id": run_id,
        "resource_fingerprints": target,
        "scope_sha256": _digest(owner["scope_id"]),
        "stages": owner["stages"],
        "outcome": "passed" if owner["stage"] == "fault_verified" else "failed_clean",
    }
    return {"ok": True, "dirty": False, "_clean_manifest": clean}


async def run_sync_fault_probe(env, store, run_id, phase, invoke):
    if not RUN_PATTERN.fullmatch(run_id) or phase not in (
        "prepare",
        "verify",
        "cleanup",
    ):
        return {"ok": False, "error": "sync_faults_request_invalid"}
    if not (
        store.enabled()
        and store.e2e_manifest_enabled()
        and str(getattr(env, "E2E_STATE_SCOPE", "") or "").strip()
        and str(getattr(env, "SYNC_DO_LOCK_ENABLED", "true")).lower() == "true"
        and not StateStore.is_kv_sync_cooldown_enabled(env)
    ):
        return {"ok": False, "error": "sync_faults_configuration_invalid"}
    stub = env.SYNC_COORDINATOR.getByName(CONTROL_NAME)
    control = f"{run_id}-{uuid4().hex}"
    lock = await store._sync_do_rpc(
        stub, "acquire", {"owner": control, "ttl_seconds": 300}
    )
    if not lock or lock.get("ok") is not True:
        return {"ok": False, "error": "sync_faults_probe_busy"}
    result = {}
    try:
        result = await asyncio.wait_for(_phase(env, store, run_id, phase, invoke), 50)
    except Exception as exc:
        result = {
            "ok": False,
            "dirty": True,
            "error": str(exc)
            if isinstance(exc, ProbeError)
            else "sync_faults_probe_failed",
        }
    finally:
        try:
            released = await store._sync_do_rpc(stub, "release", {"owner": control})
            status = await store._sync_do_rpc(stub, "status")
            if (
                not released
                or released.get("ok") is not True
                or not status
                or status.get("ok") is not True
                or not isinstance(status.get("lock"), dict)
                or status["lock"].get("owner") == control
            ):
                raise ProbeError("sync_faults_control_release_failed")
        except Exception:
            result = {
                "ok": False,
                "dirty": True,
                "error": "sync_faults_control_release_failed",
            }
    clean = result.pop("_clean_manifest", None)
    if clean:
        try:
            await store.put_e2e_manifest(SERVICE, clean)
        except Exception:
            return {
                "ok": False,
                "dirty": True,
                "error": "sync_faults_cleanup_record_failed",
            }
    return result
