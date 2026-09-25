"""外部fixtureと通常KVの所有権を同じDO manifestに保持する。"""

import re
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from e2e_discord_kv_state import KEYS, OwnedDiscordKV


SERVICE = "discord_kv"
KIND = "discord_kv_sync"
_FIELDS = ("run_id", "scope_id", "state_scope_sha256", "target_fingerprints")
_IDS = ("discord_event_id", "notion_page_id")


def valid_fixture_owner(value: dict) -> bool:
    targets = value.get("target_fingerprints")
    return bool(
        re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", str(value.get("run_id") or ""))
        and re.fullmatch(r"[0-9a-f]{32}", str(value.get("scope_id") or ""))
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("state_scope_sha256") or ""))
        and isinstance(targets, dict)
        and set(targets) == {"guild_id_sha256", "notion_database_id_sha256"}
        and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in targets.values())
        and all(key not in value or (isinstance(value[key], str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value[key])) for key in _IDS)
    )


def valid_fixture_transition(previous: dict, value: dict) -> bool:
    if any(key in value for key in ("snapshot", "queue", "delta_checkpoint")):
        return False
    if value["dirty"] and not valid_fixture_owner(value):
        return False
    if previous.get("dirty") is True:
        if value["dirty"]:
            return all(previous.get(k) == value.get(k) for k in _FIELDS) and all(
                not previous.get(k) or previous[k] == value.get(k) for k in _IDS
            )
        expected = {**previous["target_fingerprints"], "state_scope_sha256": previous["state_scope_sha256"],
                    "scope_sha256": sha256(previous["scope_id"].encode()).hexdigest()}
        expected.update({key + "_sha256": sha256(previous[key].encode()).hexdigest() for key in _IDS if previous.get(key)})
        return previous["run_id"] == value.get("last_run_id") and all(
            value.get("resource_fingerprints", {}).get(k) == v for k, v in expected.items()
        )
    return previous.get("last_run_id") != value["run_id"] if value["dirty"] else previous == value


class FixtureDiscordKV(OwnedDiscordKV):
    """イベントID確定後だけ通常StateStoreへ固定2キーを公開する。"""

    def __init__(self, store, manifest: dict):
        if not valid_fixture_owner(manifest) or not manifest.get("discord_event_id"):
            raise ValueError("discord_kv_owner_required")
        self.fixture_owner = {key: deepcopy(manifest[key]) for key in (*_FIELDS, "discord_event_id")}
        super().__init__(store, {
            "run_id": manifest["run_id"], "scope_id": manifest["scope_id"],
            "event_ids": [manifest["discord_event_id"]],
            "target_fingerprints": {"state_scope_sha256": manifest["state_scope_sha256"]},
        })
        self.prefix = f"e2e:discord_kv:{manifest['run_id']}:{manifest['scope_id']}:"

    async def _check_owner(self, key: str) -> None:
        if key not in KEYS:
            raise ValueError("discord_state_key_forbidden")
        current = await self.store.get_e2e_manifest(SERVICE)
        if not isinstance(current, dict) or current.get("dirty") is not True or any(
            current.get(k) != v for k, v in self.fixture_owner.items()
        ):
            raise ValueError("discord_kv_owner_mismatch")


class FixtureStore:
    """既存fixture処理のmanifest保存にKV所有権と回収を加える。"""

    def __init__(self, store, run_id: str, scope: str, targets: dict):
        self.store = store
        self.run_id = run_id
        self.scope_sha = sha256(scope.encode()).hexdigest()
        self.targets = targets

    def enabled(self):
        return self.store.enabled()

    def e2e_manifest_enabled(self):
        return self.store.e2e_manifest_enabled()

    async def get_e2e_manifest(self, service):
        if service != SERVICE:
            raise ValueError("discord_kv_service_forbidden")
        return await self.store.get_e2e_manifest(service)

    def check_owner(self, manifest: dict) -> None:
        if not valid_fixture_owner(manifest) or manifest.get("dirty") is not True or (
            manifest["run_id"] != self.run_id or manifest["state_scope_sha256"] != self.scope_sha
            or manifest["target_fingerprints"] != self.targets
        ):
            raise ValueError("discord_kv_owner_mismatch")

    async def put_e2e_manifest(self, service, value: dict) -> None:
        previous = await self.get_e2e_manifest(service)
        value = deepcopy(value)
        if isinstance(previous, dict) and previous.get("dirty") is True:
            self.check_owner(previous)
        if value.get("dirty") is True:
            if value.get("run_id") != self.run_id or value.get("target_fingerprints") != self.targets:
                raise ValueError("discord_kv_owner_mismatch")
            value["scope_id"] = previous["scope_id"] if previous and previous.get("dirty") else uuid4().hex
            value["state_scope_sha256"] = self.scope_sha
            if value.get("stage") == "delta_prepared":
                value["stage"] = "kv_prepared"
        else:
            if not isinstance(previous, dict):
                raise ValueError("discord_kv_owner_mismatch")
            self.check_owner(previous)
            if value.get("last_run_id") != self.run_id:
                raise ValueError("discord_kv_owner_mismatch")
            # 外部資源の回収成功後も、KVの削除完了まではdirtyを維持する。
            if previous.get("discord_event_id"):
                await FixtureDiscordKV(self.store, previous).cleanup()
            value["resource_fingerprints"].update({
                "state_scope_sha256": self.scope_sha,
                "scope_sha256": sha256(previous["scope_id"].encode()).hexdigest(),
            })
            value["outcome"] = "passed" if previous.get("stage") == "kv_verified" else "failed_clean"
            value["stages"]["kv_cleanup"] = 200
        await self.store.put_e2e_manifest(service, value)
