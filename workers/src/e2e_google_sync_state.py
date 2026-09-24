"""通常Google同期の固定KVキーとrun所有記録。"""

import json
import re
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

from state import StateStore
from e2e_discord_batch_state import slot_run_id

SERVICE = "google_sync"
KIND = "google_normal_sync"
KEYS = (
    "sync:updated_min",
    "map:gcal_notion",
    "map:gcal_discord",
    "sync:google_apply_queue",
    "sync:last_epoch",
    "result:sync_all",
)
STEPS = ("pending", "drained", "updated", "deleted", "retry_pending", "retried")
OWNER_FIELDS = ("run_id", "scope_id", "target_fingerprints", "full_apply")
# 32 KiBのmanifestにfixture・KV書込み記録の余地を残す。
MAX_BASELINE_DELETED = 100


def digest(value):
    return sha256(value.encode()).hexdigest()


def source_id(run_id, index):
    return "e2e" + digest(f"google-sync:{run_id}:{index}")


def final_step(owner):
    # 旧4段階のdirty manifestも、更新後に所有確認して回収できる。
    return len(STEPS) - 1 if owner.get("retry_enabled") is True else 3


def valid_google_transition(previous, value):
    if value["dirty"]:
        slots = value.get("fixtures")
        hashes = value.get("hashes")
        if not (
            re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", str(value.get("run_id", "")))
            and re.fullmatch(r"[0-9a-f]{32}", str(value.get("scope_id", "")))
            and isinstance(value.get("target_fingerprints"), dict)
            and set(value["target_fingerprints"])
            == {
                "calendar_id_sha256",
                "guild_id_sha256",
                "notion_database_id_sha256",
                "state_scope_sha256",
            }
            and all(
                re.fullmatch(r"[0-9a-f]{64}", str(v))
                for v in value["target_fingerprints"].values()
            )
            and isinstance(slots, list)
            and len(slots) == (3 if value.get("full_apply") else 2)
            and type(value.get("full_apply", False)) is bool
            and (not value.get("full_apply") or not value.get("retry_enabled"))
            and isinstance(hashes, dict)
            and set(hashes) <= set(KEYS)
            and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in hashes.values())
            and type(value.get("step")) is int
            and type(value.get("retry_enabled", False)) is bool
            and type(value.get("api_rejection_enabled", False)) is bool
            and (not value.get("api_rejection_enabled") or value.get("retry_enabled"))
            and 0 <= value["step"] <= final_step(value)
            and value.get("stage") in ("working", "ready", "verified", "cleanup")
        ):
            return False
        writes = value.get("shared_writes", {})
        baseline = value.get("baseline_deleted", {})
        if (
            not isinstance(baseline, dict)
            or len(baseline) > MAX_BASELINE_DELETED
            or (baseline and not value.get("full_apply"))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(item))
                   for pair in baseline.items() for item in pair)
        ):
            return False
        if not isinstance(writes, dict) or set(writes) - set(KEYS):
            return False
        if any(
            not isinstance(items, list) or not items or len(items) > 32
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(item)) for item in items)
            for items in writes.values()
        ) or (writes and not value.get("full_apply")):
            return False
        if value.get("full_apply"):
            pending = value.get("pending_ids", [])
            if (
                not isinstance(pending, list)
                or any(not isinstance(event_id, str) for event_id in pending)
                or len(set(pending)) != len(pending)
                or not set(pending) <= {slot.get("google_event_id") for slot in slots if isinstance(slot, dict)}
                or (value["stage"] in ("ready", "verified")
                    and len(pending) != (len(slots) - 1 if value["step"] == 0 else 0))
            ):
                return False
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict) or slot.get("google_event_id") != source_id(
                value["run_id"], index
            ):
                return False
            source = slot.get("source")
            if (
                slot.get("run_id") != slot_run_id(value["run_id"], index)
                or not isinstance(source, dict)
                or source.get("id") != slot["google_event_id"]
                or source.get("summary")
                != f"[E2E] Google to Discord sync {slot['run_id']}"
                or not isinstance(source.get("extendedProperties"), dict)
                or source["extendedProperties"]
                .get("private", {})
                .get("ie_event_bot_e2e_run")
                != slot["run_id"]
            ):
                return False
            if any(
                type(slot.get(k)) is not bool
                for k in ("source_attempted", "apply_attempted")
            ):
                return False
    if previous.get("dirty"):
        if not value["dirty"]:
            return (
                value.get("last_run_id") == previous["run_id"]
                and value.get("resource_fingerprints")
                == previous["target_fingerprints"]
                and value.get("scope_sha256") == digest(previous["scope_id"])
                and previous.get("stage") == "cleanup"
                and value.get("outcome")
                == ("passed" if previous.get("passed") else "failed_clean")
            )
        if any(previous.get(k) != value.get(k) for k in OWNER_FIELDS):
            return False
        if previous.get("baseline_deleted", {}) != baseline:
            return False
        if previous.get("retry_enabled", False) != value.get("retry_enabled", False):
            return False
        if previous.get("api_rejection_enabled", False) != value.get("api_rejection_enabled", False):
            return False
        old_writes = previous.get("shared_writes", {})
        if any(writes.get(key, [])[:len(items)] != items for key, items in old_writes.items()):
            return False
        if previous["stage"] != "working" and value.get("pending_ids") != previous.get("pending_ids"):
            return False
        if previous["stage"] != "working" and writes != old_writes:
            return False
        if previous["stage"] != "working" and hashes != previous["hashes"]:
            return False
        if previous["stage"] != "working" and value.get(
            "expected_cursor"
        ) != previous.get("expected_cursor"):
            return False
        for old, new in zip(previous["fixtures"], slots):
            if any(
                old.get(k) and old.get(k) != new.get(k)
                for k in (
                    "google_event_id",
                    "run_id",
                    "notion_page_id",
                    "discord_event_id",
                    "source_attempted",
                    "apply_attempted",
                    "delete_attempted",
                )
            ):
                return False
        if value["stage"] == "cleanup":
            return value["step"] == previous["step"] and value.get("passed", False) == (
                previous.get("passed", False)
                if previous["stage"] == "cleanup"
                else previous["stage"] == "verified"
                and previous["step"] == final_step(previous)
            )
        if previous["stage"] == "cleanup":
            return False
        if value["step"] == previous["step"] + 1:
            return previous["stage"] == "verified" and value["stage"] == "working"
        return value["step"] == previous["step"] and value["stage"] in {
            "working": ("working", "ready"),
            "ready": ("verified",),
            "verified": ("ready", "verified"),
        }.get(previous["stage"], ())
    return (
        previous == value
        if not value["dirty"]
        else previous.get("last_run_id") != value["run_id"]
        and value["step"] == 0
        and value["stage"] == "working"
        and not value["hashes"]
        and not value.get("shared_writes")
    )


class GoogleStateError(Exception):
    pass


class GoogleKV:
    def __init__(self, store, owner):
        self.store, self.owner = store, deepcopy(owner)
        self.hashes = dict(owner["hashes"])
        # 通常の読取りcacheではなく、APIから得た所有IDをDOへ記録するために使う。
        self.references = {}
        self.prefix = "" if owner.get("full_apply") else f"e2e:google_sync:{owner['run_id']}:{owner['scope_id']}:"
        self.shared_writes = deepcopy(owner.get("shared_writes", {}))

    async def check(self, key, *, writing=False):
        current = await self.store.get_e2e_manifest(SERVICE)
        if (
            key not in KEYS
            or not current
            or current.get("dirty") is not True
            or any(current.get(k) != self.owner.get(k) for k in OWNER_FIELDS)
            or current.get("step") != self.owner["step"]
            or current.get("stage") == "cleanup"
            or (writing and current.get("stage") != "working")
        ):
            raise GoogleStateError("google_sync_owner_mismatch")

    async def get(self, key):
        await self.check(key)
        value = await self.store.env.STATE_KV.get(self.prefix + key)
        # Python WorkersのJS null/undefinedも、通常StateStoreと同じ欠損値にする。
        value = None if value is None else str(value)
        if value in ("jsnull", "jsundefined"):
            value = None
        if (None if value is None else digest(value)) != self.hashes.get(key):
            raise GoogleStateError("google_sync_not_ready")
        return value

    async def put(self, key, value):
        await self.check(key, writing=True)
        if not isinstance(value, str) or len(value.encode()) > 32768:
            raise GoogleStateError("google_sync_state_invalid")
        ids = {slot["google_event_id"] for slot in self.owner["fixtures"]}
        if key in (KEYS[1], KEYS[2], KEYS[3]):
            data = json.loads(value)
            if key == KEYS[1]:
                valid = (
                    isinstance(data, dict)
                    and set(data) == {"internal", "external"}
                    and data["external"] == {}
                    and isinstance(data["internal"], dict)
                    and set(data["internal"]) <= ids
                )
            elif key == KEYS[2]:
                valid = isinstance(data, dict) and set(data) <= ids
            else:
                valid = (
                    isinstance(data, list)
                    and len(data) <= len(ids)
                    and all(isinstance(e, dict) and e.get("id") in ids for e in data)
                )
            if not valid:
                raise GoogleStateError("google_sync_state_invalid")
        if self.owner.get("full_apply"):
            # KV応答喪失でも回収できるよう、書込み予定のdigestを先にDOへ保存する。
            current = await self.store.get_e2e_manifest(SERVICE)
            writes = deepcopy(current.get("shared_writes", {}))
            values = writes.setdefault(key, [])
            if digest(value) not in values:
                values.append(digest(value))
            current["shared_writes"] = writes
            await self.store.put_e2e_manifest(SERVICE, current)
            self.shared_writes = writes
        await self.store.env.STATE_KV.put(self.prefix + key, value)
        self.hashes[key] = digest(value)
        if key in (KEYS[1], KEYS[2]):
            self.references[key] = json.loads(value)

    def state(self):
        return StateStore(
            SimpleNamespace(
                STATE_KV=self, SYNC_COORDINATOR=None, KV_RESULT_MIN_WRITE_SECONDS="0"
            )
        )
