"""固定2イベントの所有権と共有KV範囲をDOで管理する。"""

import json
import re
from copy import deepcopy
from hashlib import sha256

from e2e_discord_kv_state import KEYS, OwnedDiscordKV, _MAX_STATE_BYTES


SERVICE = "discord_batch"
KIND = "discord_batch_sync"
GOOGLE_SERVICE = "discord_batch_google"
GOOGLE_KIND = "discord_batch_google_sync"
NOTIFICATION_SERVICE = "discord_batch_notification"
NOTIFICATION_KIND = "discord_batch_notification_sync"
COUNT = 2
_FIELDS = ("kind", "run_id", "scope_id", "state_scope_sha256", "target_fingerprints")
_ID_FIELDS = ("discord_event_id", "notion_page_id", "source_sha256")


def manifest_service(value: dict) -> str:
    if value.get("kind") == NOTIFICATION_KIND:
        return NOTIFICATION_SERVICE
    return GOOGLE_SERVICE if value.get("kind") == GOOGLE_KIND else SERVICE


def google_event_id(run_id: str) -> str:
    return "e2e" + sha256(f"discord-batch-google:{run_id}".encode()).hexdigest()


def slot_run_id(run_id: str, index: int) -> str:
    return (
        run_id[:-8] + sha256(f"{run_id}:discord-batch:{index}".encode()).hexdigest()[:8]
    )


def fixture_fingerprint(slots: list) -> str:
    owners = [{key: slot.get(key) for key in ("run_id", *_ID_FIELDS)} for slot in slots]
    for owner, slot in zip(owners, slots):
        if "google_event_id" in slot:
            owner["google_event_id"] = slot["google_event_id"]
        if "message_id" in slot:
            owner["message_id"] = slot["message_id"]
    return sha256(
        json.dumps(owners, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def valid_batch_owner(value: dict) -> bool:
    slots = value.get("fixtures")
    targets = value.get("target_fingerprints")
    google = value.get("kind") == GOOGLE_KIND
    notification = value.get("kind") == NOTIFICATION_KIND
    target_keys = {"guild_id_sha256", "notion_database_id_sha256"}
    attempt_keys = {"discord_event", "notion_page"}
    if google:
        target_keys.add("calendar_id_sha256")
        attempt_keys.add("google_event")
    if notification:
        target_keys.update(("channel_id_sha256", "role_id_sha256"))
        attempt_keys.add("message")
    if not (
        re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", str(value.get("run_id") or ""))
        and re.fullmatch(r"[0-9a-f]{32}", str(value.get("scope_id") or ""))
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("state_scope_sha256") or ""))
        and isinstance(targets, dict)
        and set(targets) == target_keys
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
            or set(attempted) != attempt_keys
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
        if google and (
            slot.get("google_event_id") != google_event_id(slot["run_id"])
            or type(slot.get("google_cleanup_done", False)) is not bool
        ):
            return False
        if not google and any(
            k in slot for k in ("google_event_id", "google_cleanup_done")
        ):
            return False
        if type(slot.get("cleanup_done", False)) is not bool:
            return False
        if notification:
            if any(type(slot.get(k, False)) is not bool for k in (
                "message_cleanup_done", "reaction_deferred", "reaction_done",
            )):
                return False
            if "message_id" in slot and (
                not isinstance(slot["message_id"], str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", slot["message_id"])
            ):
                return False
            if attempted["message"] and not re.fullmatch(
                r"[0-9a-f]{64}", str(slot.get("message_content_sha256") or "")
            ):
                return False
            if slot.get("message_id") and not attempted["message"]:
                return False
            if slot.get("reaction_done") and not slot.get("message_id"):
                return False
            if slot.get("cleanup_done") and attempted["message"] and not slot.get("message_cleanup_done"):
                return False
        elif any(k in slot for k in (
            "message_id", "message_content_sha256", "message_cleanup_done", "reaction_deferred", "reaction_done",
        )):
            return False
    for key in ("run_id", *_ID_FIELDS[:2], "message_id"):
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
        if previous.get("kind") == NOTIFICATION_KIND and not all(
            s.get("cleanup_done") for s in previous["fixtures"]
        ):
            return False
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
        if any(old.get(k) and old[k] != new.get(k) for k in (*_ID_FIELDS, "message_id", "message_content_sha256")):
            return False
        if any(
            v and not new["create_attempted"][k]
            for k, v in old["create_attempted"].items()
        ):
            return False
        if any(
            old.get(k) and not new.get(k)
            for k in ("cleanup_done", "google_cleanup_done", "message_cleanup_done",
                      "reaction_deferred", "reaction_done")
        ):
            return False
    return (
        previous.get("stage") != "batch_cleanup"
        or value.get("stage") == "batch_cleanup"
    )


async def check_batch_owner(store, owner: dict) -> dict:
    current = await store.get_e2e_manifest(manifest_service(owner))
    if (
        not isinstance(current, dict)
        or current.get("dirty") is not True
        or not valid_batch_owner(current)
        or any(current.get(k) != owner.get(k) for k in _FIELDS)
    ):
        raise ValueError("discord_batch_owner_mismatch")
    return current


def key_prefix(owner: dict) -> str:
    return f"e2e:{manifest_service(owner)}:{owner['run_id']}:{owner['scope_id']}:"


async def cleanup_batch_kv(store, owner: dict) -> None:
    for key in KEYS:
        await check_batch_owner(store, owner)
        await store.env.STATE_KV.delete(key_prefix(owner) + key)


class BatchQueueChanged(Exception):
    pass


class BatchDiscordKV(OwnedDiscordKV):
    def __init__(self, store, owner: dict, *, expected_queue=None):
        self.expected_queue = deepcopy(expected_queue)
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

    async def get(self, key: str):
        value = await super().get(key)
        if key == KEYS[1] and self.expected_queue is not None:
            queue = [] if value is None or str(value) in ("jsnull", "jsundefined") else json.loads(str(value))
            if queue != self.expected_queue:
                raise BatchQueueChanged("discord_batch_queue_changed")
        return value

    def _check_value(self, key: str, text: str) -> None:
        if key != KEYS[1] or self.batch_owner.get("kind") != NOTIFICATION_KIND:
            return super()._check_value(key, text)
        if len(text.encode("utf-8")) > _MAX_STATE_BYTES:
            raise ValueError("discord_state_value_too_large")
        value = json.loads(text)
        slots = {s["discord_event_id"]: s for s in self.batch_owner["fixtures"]}
        if not isinstance(value, list) or len(value) > COUNT:
            raise ValueError("discord_state_value_forbidden")
        seen = set()
        for op in value:
            if (not isinstance(op, dict) or set(op) != {"id", "op", "notification"}
                    or op.get("id") not in slots or op["id"] in seen
                    or op.get("op") not in ("upsert", "notify")):
                raise ValueError("discord_state_value_forbidden")
            seen.add(op["id"])
            delivery = op["notification"]
            if (not isinstance(delivery, dict)
                    or set(delivery) not in ({"channel_id"}, {"channel_id", "message_id"})
                    or not isinstance(delivery.get("channel_id"), str)
                    or sha256(delivery["channel_id"].encode()).hexdigest()
                    != self.batch_owner["target_fingerprints"]["channel_id_sha256"]):
                raise ValueError("discord_state_value_forbidden")
            if "message_id" in delivery and (
                not delivery["message_id"]
                or delivery["message_id"] != slots[op["id"]].get("message_id")
            ):
                raise ValueError("discord_state_value_forbidden")

    async def _check_owner(self, key: str) -> None:
        if key not in KEYS:
            raise ValueError("discord_batch_key_forbidden")
        current = await check_batch_owner(self.store, self.batch_owner)
        if [s.get("discord_event_id") for s in current["fixtures"]] != self.owner[
            "event_ids"
        ]:
            raise ValueError("discord_batch_owner_mismatch")
        # 投稿後のIDをDOから取得し、古いKVの他メッセージ参照を拒否する。
        self.batch_owner = deepcopy(current)
