"""通常StateStoreへ渡すKVを、DOで所有したrunと固定キーへ制限する。"""

import json
import re
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from state import StateStore


SERVICE = "discord_state"
KIND = "discord_kv_state"
KEYS = ("discord:snapshot", "sync:discord_notion_queue")
_RUN = re.compile(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}")
_HEX = re.compile(r"[0-9a-f]{32}")
_OWNER_FIELDS = ("run_id", "scope_id", "event_ids", "target_fingerprints")
_MAX_STATE_BYTES = 32768
_MAX_EVENTS = 5


def valid_owner(value) -> bool:
    if not isinstance(value, dict):
        return False
    ids = value.get("event_ids")
    targets = value.get("target_fingerprints")
    return bool(
        _RUN.fullmatch(str(value.get("run_id") or ""))
        and _HEX.fullmatch(str(value.get("scope_id") or ""))
        and isinstance(ids, list) and 1 <= len(ids) <= _MAX_EVENTS
        and all(isinstance(item, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", item) for item in ids)
        and len(set(ids)) == len(ids)
        and isinstance(targets, dict) and set(targets) == {"state_scope_sha256"}
        and re.fullmatch(r"[0-9a-f]{64}", str(targets["state_scope_sha256"]))
    )


def valid_transition(previous: dict, manifest: dict) -> bool:
    """所有run・scopeの差し替えとclean後の古い書込みをDOで拒否する。"""
    if manifest["dirty"] and not valid_owner(manifest):
        return False
    if previous.get("dirty") is True:
        if manifest["dirty"]:
            return all(previous.get(key) == manifest.get(key) for key in _OWNER_FIELDS)
        return (
            previous.get("run_id") == manifest.get("last_run_id")
            and previous.get("target_fingerprints") == manifest.get("resource_fingerprints")
            and manifest.get("scope_sha256") == sha256(str(previous.get("scope_id")).encode()).hexdigest()
        )
    if not manifest["dirty"]:
        return previous == manifest
    return previous.get("last_run_id") != manifest["run_id"]


class OwnedDiscordKV:
    """読書き前に所有権と値を確認する。KV応答はキャッシュしない。"""

    def __init__(self, store: StateStore, owner: dict):
        if not store.enabled() or not store.e2e_manifest_enabled() or not valid_owner(owner):
            raise ValueError("discord_state_owner_required")
        self.store = store
        self.owner = deepcopy(owner)
        self.prefix = f"e2e:discord_state:{owner['run_id']}:{owner['scope_id']}:"

    async def _check_owner(self, key: str) -> None:
        if key not in KEYS:
            raise ValueError("discord_state_key_forbidden")
        manifest = await self.store.get_e2e_manifest(SERVICE)
        if not isinstance(manifest, dict) or manifest.get("dirty") is not True or not all(
            manifest.get(name) == self.owner[name] for name in _OWNER_FIELDS
        ):
            raise ValueError("discord_state_owner_mismatch")

    def _check_value(self, key: str, text: str) -> None:
        if len(text.encode("utf-8")) > _MAX_STATE_BYTES:
            raise ValueError("discord_state_value_too_large")
        value = json.loads(text)
        ids = self.owner["event_ids"]
        if key == KEYS[0]:
            valid = isinstance(value, dict) and not (set(value) - set(ids))
            if valid:
                for event_id, fingerprint in value.items():
                    event = json.loads(fingerprint) if isinstance(fingerprint, str) else None
                    if not isinstance(event, dict) or str(event.get("id") or "") != event_id:
                        valid = False
        else:
            valid = isinstance(value, list) and len(value) <= len(ids) and all(
                isinstance(op, dict) and set(op) == {"id", "op"}
                and op["id"] in ids and op["op"] in ("upsert", "delete") for op in value
            )
        if not valid:
            raise ValueError("discord_state_value_forbidden")

    async def get(self, key: str):
        await self._check_owner(key)
        value = await self.store.env.STATE_KV.get(self.prefix + key)
        if value is not None and str(value) not in ("jsnull", "jsundefined"):
            self._check_value(key, str(value))
        return value

    async def put(self, key: str, value: str) -> None:
        await self._check_owner(key)
        self._check_value(key, value)
        await self.store.env.STATE_KV.put(self.prefix + key, value)

    async def cleanup(self) -> None:
        # 固定2キーだけを削除する。list結果や呼出側の任意キーは使わない。
        for key in KEYS:
            await self._check_owner(key)
            await self.store.env.STATE_KV.delete(self.prefix + key)

    def state(self) -> StateStore:
        return StateStore(SimpleNamespace(STATE_KV=self, SYNC_COORDINATOR=None))


def _fixture(owner: dict) -> tuple[dict, list]:
    event_id = owner["event_ids"][0]
    return {event_id: json.dumps({"id": event_id, "status": 1})}, [{"id": event_id, "op": "upsert"}]


async def run_discord_state_probe(env, store: StateStore, run_id: str, phase: str) -> dict:
    """外部イベントを作らず、別HTTPでKV保存・読戻し・回収を検証する。"""
    if not _RUN.fullmatch(run_id) or phase not in ("prepare", "verify", "cleanup"):
        return {"ok": False, "error": "invalid_state_request"}
    scope = str(getattr(env, "E2E_STATE_SCOPE", "") or "").strip()
    if not scope or not store.enabled() or not store.e2e_manifest_enabled():
        return {"ok": False, "error": "discord_state_bindings_required"}
    targets = {"state_scope_sha256": sha256(scope.encode()).hexdigest()}
    manifest = await store.get_e2e_manifest(SERVICE)
    if phase == "prepare":
        if manifest and (manifest.get("dirty") or manifest.get("last_run_id") == run_id):
            return {"ok": False, "dirty": bool(manifest.get("dirty")), "error": "environment_dirty"}
        manifest = {
            "kind": KIND, "version": 1, "dirty": True, "run_id": run_id,
            "scope_id": uuid4().hex, "event_ids": ["state-" + sha256(run_id.encode()).hexdigest()[:16]],
            "target_fingerprints": targets, "stage": "state_prepared",
        }
        await store.put_e2e_manifest(SERVICE, manifest)
        adapter = OwnedDiscordKV(store, manifest)
        state = adapter.state()
        snapshot, queue = _fixture(manifest)
        await state.put_json_if_changed(KEYS[1], queue)
        await state.set_discord_snapshot(snapshot)
        return {"ok": True, "dirty": True, "stage": "state_prepared"}
    if not manifest or not manifest.get("dirty"):
        if phase == "cleanup" and manifest and manifest.get("last_run_id") == run_id and manifest.get("resource_fingerprints") == targets:
            return {"ok": True, "dirty": False}
        return {"ok": False, "error": "discord_state_not_prepared"}
    if manifest.get("run_id") != run_id or manifest.get("target_fingerprints") != targets:
        return {"ok": False, "dirty": True, "error": "discord_state_owner_mismatch"}
    adapter = OwnedDiscordKV(store, manifest)
    if phase == "verify":
        state = adapter.state()
        snapshot, queue = _fixture(manifest)
        if await state.get_discord_snapshot() != snapshot or await state.get_json(KEYS[1]) != queue:
            return {"ok": False, "dirty": True, "error": "discord_state_not_ready"}
        manifest["stage"] = "state_verified"
        await store.put_e2e_manifest(SERVICE, manifest)
        return {"ok": True, "dirty": True, "stage": "state_verified"}
    await adapter.cleanup()
    await store.put_e2e_manifest(SERVICE, {
        "kind": KIND, "version": 1, "dirty": False, "last_run_id": run_id,
        "resource_fingerprints": targets,
        "scope_sha256": sha256(manifest["scope_id"].encode()).hexdigest(),
        "outcome": "passed" if manifest.get("stage") == "state_verified" else "failed_clean",
    })
    return {"ok": True, "dirty": False}
