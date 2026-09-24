"""空の専用Notion DBで通常cleanup HTTP・共有KVを段階検証する。"""

import json
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import e2e_notion_cleanup_probe as probe


SERVICE = probe.NOTION_CLEANUP_MANIFEST_SERVICE
KEYS = ("cleanup:last_epoch", "result:job_cleanup")
PHASES = ("prepare", "execute", "duplicate")


def _require(condition, code):
    if not condition:
        raise RuntimeError(code)


def _targets(env):
    return probe._target_fingerprints(env.NOTION_EVENT_INTERNAL_ID)


async def _save(store, owner):
    await store.put_e2e_manifest(SERVICE, owner)


async def _pages(env, owner):
    status, data = await probe._notion_request_stage(
        env, owner["stages"], {}, "cleanup_normal_query", "POST",
        f"/databases/{env.NOTION_EVENT_INTERNAL_ID}/query", {"page_size": 100},
    )
    _require(status == 200 and isinstance(data, dict) and data.get("has_more") is False
             and isinstance(data.get("results"), list), "cleanup_normal_query_failed")
    return data["results"]


def _matches(env, owner, page, kind, archived):
    return probe._page_matches(
        page, page_id=owner[f"{kind}_page_id"], database_id=env.NOTION_EVENT_INTERNAL_ID,
        expected=owner["expected"][kind], archived=archived,
    )


async def _inputs(env, owner):
    pages = await _pages(env, owner)
    kinds = ("due", "future") if owner["completed"] == "prepare" else ("future",)
    expected = {owner[f"{kind}_page_id"]: kind for kind in kinds}
    _require(len(pages) == len(expected) and {p.get("id") for p in pages} == set(expected)
             and all(_matches(env, owner, p, expected[p["id"]], False) for p in pages),
             "cleanup_normal_foreign_input")


class _OwnedKV:
    """通常キーを実KVへ通し、書込み前に所有digestをDOへ記録する。"""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    async def get(self, key):
        _require(key in KEYS, "cleanup_normal_key_forbidden")
        current = await self.store.get_e2e_manifest(SERVICE)
        _require(current and current.get("normal") and current.get("dirty") is True
                 and current.get("run_id") == self.owner["run_id"], "cleanup_normal_owner_mismatch")
        value = await self.store.get_text(key)
        _require((probe._fingerprint(value) if value is not None else None)
                 == self.owner["hashes"].get(key), "cleanup_normal_kv_not_ready")
        return value

    async def put(self, key, value):
        await self.get(key)
        if key == "cleanup:last_epoch":
            epoch = float(value)
            _require(math.isfinite(epoch) and self.owner["job_started"] <= epoch
                     <= datetime.now(timezone.utc).timestamp(), "cleanup_normal_epoch_invalid")
            self.owner["epoch"] = value
        digest = probe._fingerprint(value)
        self.owner["writes"].setdefault(key, []).append(digest)
        await _save(self.store, self.owner)
        await self.store.env.STATE_KV.put(key, value)
        self.owner["hashes"][key] = digest
        await _save(self.store, self.owner)


class _JobEnv:
    def __init__(self, env, kv):
        self._env = env
        self.STATE_KV = kv
        self.KV_RESULT_MIN_WRITE_SECONDS = "0"

    def __getattr__(self, key):
        return getattr(self._env, key)


def _detail(phase):
    if phase == "execute":
        return {"mode": "native", "ok": True, "scanned": 2, "archived": 1}
    return {"mode": "native", "ok": True, "skipped": True, "reason": "interval_guard"}


async def _verify(env, store, owner):
    for key in KEYS:
        await _OwnedKV(store, owner).get(key)
    await _inputs(env, owner)
    completed = owner["completed"]
    for kind in ("due", "future"):
        status, page = await probe._notion_request_stage(
            env, owner["stages"], {}, f"cleanup_normal_read_{kind}", "GET",
            f"/pages/{owner[f'{kind}_page_id']}",
        )
        archived = kind == "due" and completed != "prepare"
        _require(status == 200 and isinstance(page, dict) and _matches(env, owner, page, kind, archived),
                 "cleanup_normal_page_verification_failed")
    if completed != "prepare":
        _require(await store.get_text("cleanup:last_epoch") == owner.get("epoch")
                 and len(owner["writes"].get("cleanup:last_epoch", [])) == 1,
                 "cleanup_normal_epoch_rewritten")
        result = await store.get_last_result("job_cleanup")
        _require((result or {}).get("payload") == _detail(completed), "cleanup_normal_result_failed")
        _require(len(owner["writes"].get("result:job_cleanup", [])) == (1 if completed == "execute" else 2),
                 "cleanup_normal_result_write_count_failed")
    owner["stages"][f"cleanup_normal_verify_{completed}"] = 200
    await _save(store, owner)


async def _prepare(env, store, run_id):
    previous = await store.get_e2e_manifest(SERVICE)
    _require(not previous or not previous.get("dirty"), "environment_dirty")
    _require(not probe._configuration_error(env), "cleanup_normal_configuration_invalid")
    owner = {
        "version": 1, "kind": "notion_cleanup_job", "normal": True,
        "run_id": run_id, "dirty": True, "target_fingerprints": _targets(env),
        "create_attempted": {"due_page": False, "future_page": False}, "expected": {},
        "hashes": {}, "writes": {}, "stages": {}, "stage": "preparing", "completed": "",
    }
    error = await probe._verify_database(
        env, env.NOTION_EVENT_INTERNAL_ID, probe._EVENT_SCHEMA,
        owner["stages"], {}, "target_notion_event_database", "notion_event",
    )
    _require(not error, "cleanup_normal_target_failed")
    _require(not await _pages(env, owner), "cleanup_normal_database_not_empty")
    for key in KEYS:
        _require(await store.get_text(key) is None, "cleanup_normal_shared_state_not_empty")
    await _save(store, owner)
    now = datetime.now(timezone.utc)
    owner["deadline"] = (now + timedelta(minutes=4)).isoformat()
    for kind in ("due", "future"):
        payload, expected = probe._page_payload(env.NOTION_EVENT_INTERNAL_ID, run_id, kind, now)
        owner["expected"][kind] = expected
        stages = owner["stages"]
        _, _, error = await probe._create_owned_page(
            env, store, owner, database_id=env.NOTION_EVENT_INTERNAL_ID, run_id=run_id,
            page_kind=kind, payload=payload, expected=expected, stages=stages, retries={},
        )
        owner["stages"].update(stages)
        _require(not error, error)
    owner["completed"] = "prepare"
    owner["stage"] = "prepared"
    owner["stages"]["cleanup_normal_prepare"] = 200
    await _save(store, owner)
    return owner


async def _job(env, store, owner, phase, request):
    from entry import Application

    await _inputs(env, owner)
    for key in KEYS:
        await _OwnedKV(store, owner).get(key)
    now = datetime.now(timezone.utc)
    _require(now < datetime.fromisoformat(owner["deadline"]), "cleanup_normal_deadline_exceeded")
    owner["job_started"] = now.timestamp()
    owner["stage"] = "working"
    await _save(store, owner)
    job_request = SimpleNamespace(url="https://e2e.invalid/jobs/cleanup", method="POST", headers=request.headers)
    response = await Application(_JobEnv(env, _OwnedKV(store, owner))).fetch(job_request)
    detail = json.loads(await response.text())
    _require(response.status == 200 and detail == _detail(phase), "cleanup_normal_job_failed")
    owner["stages"][f"cleanup_normal_{phase}"] = 200


async def cleanup(env, store, run_id):
    owner = await store.get_e2e_manifest(SERVICE)
    _require(owner and owner.get("normal") and owner.get("run_id", owner.get("last_run_id")) == run_id,
             "cleanup_run_id_mismatch")
    if not owner.get("dirty"):
        return {"ok": True, "dirty": False, "run_id": run_id, "stages": owner["stages"]}
    _require(owner.get("target_fingerprints") == _targets(env), "dirty_manifest_target_mismatch")
    owner["stage"] = "cleanup"
    await _save(store, owner)
    result = await probe._cleanup_resources(env, owner)
    owner["stages"].update(result["stages"])
    for kind in ("due", "future"):
        if result.get(f"{kind}_page_id"):
            owner[f"{kind}_page_id"] = result[f"{kind}_page_id"]
    await _save(store, owner)
    _require(result["ok"], str(result.get("error") or "cleanup_normal_pages_remaining"))
    for key in KEYS:
        value = await store.get_text(key)
        if value is not None:
            _require(probe._fingerprint(value) in owner["writes"].get(key, []), "cleanup_normal_foreign_kv")
            await env.STATE_KV.delete(key)
        _require(await store.get_text(key) is None, "cleanup_normal_cleanup_not_ready")
    owner["stages"]["cleanup_normal_cleanup"] = 200
    fingerprints = _targets(env)
    for kind in ("due", "future"):
        if owner.get(f"{kind}_page_id"):
            fingerprints[f"{kind}_page_id_sha256"] = probe._fingerprint(owner[f"{kind}_page_id"])
    clean = {
        "version": 1, "kind": "notion_cleanup_job", "normal": True, "dirty": False,
        "last_run_id": run_id, "outcome": "passed" if owner["completed"] == "duplicate"
        and owner["stages"].get("cleanup_normal_verify_duplicate") == 200 else "failed_clean",
        "stages": owner["stages"], "resource_fingerprints": fingerprints,
    }
    await _save(store, clean)
    return {"ok": True, "dirty": False, "run_id": run_id, "stages": clean["stages"]}


async def run(env, store, run_id, phase, request):
    if phase == "prepare":
        owner = await _prepare(env, store, run_id)
    else:
        owner = await store.get_e2e_manifest(SERVICE)
        _require(owner and owner.get("normal") and owner.get("dirty") is True
                 and owner.get("run_id") == run_id and owner.get("target_fingerprints") == _targets(env),
                 "cleanup_normal_owner_mismatch")
        if phase == "verify":
            await _verify(env, store, owner)
        else:
            _require(phase in PHASES and owner.get("completed") == PHASES[PHASES.index(phase) - 1]
                     and owner.get("stage") != "working", "cleanup_normal_phase_mismatch")
            _require(owner["stages"].get(f"cleanup_normal_verify_{owner['completed']}") == 200,
                     "cleanup_normal_previous_unverified")
            await _job(env, store, owner, phase, request)
            owner["completed"] = phase
            owner["stage"] = "ready"
            await _save(store, owner)
    return {"ok": True, "dirty": True, "run_id": run_id, "stages": owner["stages"]}
