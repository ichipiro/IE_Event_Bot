"""固定2イベントの所有権と共有KV範囲をDOで管理する。"""

import json
import re
from copy import deepcopy
from hashlib import sha256

from e2e_discord_kv_state import KEYS, OwnedDiscordKV


SERVICE = "discord_batch"
KIND = "discord_batch_sync"
COUNT = 2
_FIELDS = ("run_id", "scope_id", "state_scope_sha256", "target_fingerprints")
_ID_FIELDS = ("discord_event_id", "notion_page_id", "source_sha256")


def slot_run_id(run_id: str, index: int) -> str:
    return (
        run_id[:-8] + sha256(f"{run_id}:discord-batch:{index}".encode()).hexdigest()[:8]
    )


def fixture_fingerprint(slots: list) -> str:
    owners = [{key: slot.get(key) for key in ("run_id", *_ID_FIELDS)} for slot in slots]
    return sha256(
        json.dumps(owners, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def valid_batch_owner(value: dict) -> bool:
    slots = value.get("fixtures")
    targets = value.get("target_fingerprints")
    if not (
        re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", str(value.get("run_id") or ""))
        and re.fullmatch(r"[0-9a-f]{32}", str(value.get("scope_id") or ""))
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("state_scope_sha256") or ""))
        and isinstance(targets, dict)
        and set(targets) == {"guild_id_sha256", "notion_database_id_sha256"}
        and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in targets.values())
        and isinstance(slots, list)
        and len(slots) == COUNT
    ):
        return False
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict) or slot.get("run_id") != slot_run_id(
            value["run_id"], index
        ):
            return False
        attempted = slot.get("create_attempted")
        if (
            not isinstance(attempted, dict)
            or set(attempted) != {"discord_event", "notion_page"}
            or any(type(v) is not bool for v in attempted.values())
        ):
            return False
        if any(
            k in slot
            and (
                not isinstance(slot[k], str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", slot[k])
            )
            for k in _ID_FIELDS[:2]
        ):
            return False
        if "source_sha256" in slot and not re.fullmatch(
            r"[0-9a-f]{64}", str(slot["source_sha256"])
        ):
            return False
        if type(slot.get("cleanup_done", False)) is not bool:
            return False
    for key in ("run_id", *_ID_FIELDS[:2]):
        ids = [slot[key] for slot in slots if slot.get(key)]
        if len(set(ids)) != len(ids):
            return False
    return True


def valid_batch_transition(previous: dict, value: dict) -> bool:
    if any(k in value for k in ("snapshot", "queue", "delta_checkpoint")):
        return False
    if value["dirty"] and not valid_batch_owner(value):
        return False
    if previous.get("dirty") is not True:
        return (
            previous.get("last_run_id") != value["run_id"]
            if value["dirty"]
            else previous == value
        )
    if not valid_batch_owner(previous):
        return False
    if not value["dirty"]:
        expected = {
            **previous["target_fingerprints"],
            "state_scope_sha256": previous["state_scope_sha256"],
            "scope_sha256": sha256(previous["scope_id"].encode()).hexdigest(),
            "fixtures_sha256": fixture_fingerprint(previous["fixtures"]),
        }
        return (
            previous["run_id"] == value.get("last_run_id")
            and value.get("resource_fingerprints") == expected
        )
    if any(previous[k] != value[k] for k in _FIELDS):
        return False
    for old, new in zip(previous["fixtures"], value["fixtures"]):
        if any(old.get(k) and old[k] != new.get(k) for k in _ID_FIELDS):
            return False
        if any(
            v and not new["create_attempted"][k]
            for k, v in old["create_attempted"].items()
        ):
            return False
        if old.get("cleanup_done") and not new.get("cleanup_done"):
            return False
    return (
        previous.get("stage") != "batch_cleanup"
        or value.get("stage") == "batch_cleanup"
    )


async def check_batch_owner(store, owner: dict) -> dict:
    current = await store.get_e2e_manifest(SERVICE)
    if (
        not isinstance(current, dict)
        or current.get("dirty") is not True
        or not valid_batch_owner(current)
        or any(current.get(k) != owner.get(k) for k in _FIELDS)
    ):
        raise ValueError("discord_batch_owner_mismatch")
    return current


def key_prefix(owner: dict) -> str:
    return f"e2e:discord_batch:{owner['run_id']}:{owner['scope_id']}:"


async def cleanup_batch_kv(store, owner: dict) -> None:
    for key in KEYS:
        await check_batch_owner(store, owner)
        await store.env.STATE_KV.delete(key_prefix(owner) + key)


class BatchDiscordKV(OwnedDiscordKV):
    def __init__(self, store, owner: dict):
        if not valid_batch_owner(owner) or any(
            not item.get("discord_event_id") for item in owner["fixtures"]
        ):
            raise ValueError("discord_batch_owner_required")
        self.batch_owner = deepcopy(owner)
        super().__init__(
            store,
            {
                "run_id": owner["run_id"],
                "scope_id": owner["scope_id"],
                "event_ids": [item["discord_event_id"] for item in owner["fixtures"]],
                "target_fingerprints": {
                    "state_scope_sha256": owner["state_scope_sha256"]
                },
            },
        )
        self.prefix = key_prefix(owner)

    async def _check_owner(self, key: str) -> None:
        if key not in KEYS:
            raise ValueError("discord_batch_key_forbidden")
        current = await check_batch_owner(self.store, self.batch_owner)
        if [s.get("discord_event_id") for s in current["fixtures"]] != self.owner[
            "event_ids"
        ]:
            raise ValueError("discord_batch_owner_mismatch")
