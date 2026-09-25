"""17件・上限5件の固定試験で許可する所有記録と段階遷移。"""

import re

from e2e_discord_batch_state import slot_run_id
from e2e_google_sync_state import KEYS, KIND, OWNER_FIELDS, digest, source_id

COUNT = 17
LIMIT = 5
QUEUE_STEP = 18
LAST_APPLY = 22
LAST_STEP = 26
STATUSES = ("prepared",) * 18 + ("pending",) * 4 + ("drained",) * 5


def pending_count(step):
    # 準備時はqueueを持たず、取得後に17件を別HTTPへ引き渡す。
    return max(0, COUNT - max(0, step - QUEUE_STEP) * LIMIT) if step >= QUEUE_STEP else 0


def valid_transition(previous, value):
    """所有先・固定17件・既存対応IDを変える更新や段階飛越を拒否する。"""
    if not value["dirty"]:
        return (previous == value if not previous.get("dirty") else
                previous.get("stage") == "cleanup"
                and all(s.get("cleaned") is True for s in previous["fixtures"])
                and value.get("last_run_id") == previous["run_id"]
                and value.get("resource_fingerprints") == previous["target_fingerprints"]
                and value.get("scope_sha256") == digest(previous["scope_id"])
                and value.get("outcome") == ("passed" if previous.get("passed") else "failed_clean"))
    if not (value.get("boundary") is True and value.get("full_apply") is True
            and value.get("kind") == KIND
            and not any(value.get(k) for k in ("matrix", "all_sync", "http_sync", "webhook_sync", "retry_enabled"))
            and re.fullmatch(r"E2E-\d{8}T\d{6}Z-[a-f0-9]{8}", str(value.get("run_id", "")))
            and re.fullmatch(r"[a-f0-9]{32}", str(value.get("scope_id", "")))
            and type(value.get("step")) is int and 0 <= value["step"] <= LAST_STEP
            and value.get("stage") in ("working", "ready", "verified", "cleanup")
            and isinstance(value.get("fixtures"), list) and len(value["fixtures"]) == COUNT):
        return False
    for key in ("hashes", "target_fingerprints"):
        data = value.get(key)
        if not isinstance(data, dict) or any(not re.fullmatch(r"[a-f0-9]{64}", str(v)) for v in data.values()):
            return False
    baseline = value.get("baseline_deleted")
    if not isinstance(baseline, list) or len(baseline) > 100 or any(
        not re.fullmatch(r"[a-f0-9]{64}", str(v)) for v in baseline
    ):
        return False
    if (set(value["hashes"]) - set(KEYS) or set(value["target_fingerprints"]) != {
        "calendar_id_sha256", "guild_id_sha256", "notion_database_id_sha256", "state_scope_sha256",
    }):
        return False
    for index, slot in enumerate(value["fixtures"]):
        if not isinstance(slot, dict) or not isinstance(slot.get("source"), dict):
            return False
        source = slot["source"]
        if (slot.get("run_id") != slot_run_id(value["run_id"], index)
                or slot.get("google_event_id") != source_id(value["run_id"], index)
                or source.get("id") != slot["google_event_id"]
                or source.get("summary") != f"[E2E] Google to Discord sync {slot['run_id']}"
                or (source.get("extendedProperties") or {}).get("private", {}).get("ie_event_bot_e2e_run") != slot["run_id"]
                or any(type(slot.get(k)) is not bool for k in ("source_attempted", "apply_attempted", "delete_attempted", "cleaned"))
                or (slot["cleaned"] and value["stage"] != "cleanup")):
            return False
    ids = {s["google_event_id"] for s in value["fixtures"]}
    pending = value.get("pending_ids")
    if (not isinstance(pending, list) or any(not isinstance(i, str) for i in pending)
            or len(set(pending)) != len(pending) or not set(pending) <= ids):
        return False
    writes = value.get("shared_writes")
    if not isinstance(writes, dict) or set(writes) - set(KEYS) or any(
        not isinstance(items, list) or not items or len(items) > 32
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(v)) for v in items) for items in writes.values()
    ):
        return False
    if value["step"] < QUEUE_STEP and (value["hashes"] or writes or pending):
        return False
    if value["stage"] in ("ready", "verified") and len(pending) != pending_count(value["step"]):
        return False
    if not previous.get("dirty"):
        return (value["step"] == 0 and value["stage"] == "working" and not value["hashes"]
                and not writes and previous.get("last_run_id") != value["run_id"])
    if not previous.get("boundary") or any(previous.get(k) != value.get(k) for k in (*OWNER_FIELDS, "baseline_deleted")):
        return False
    for old, new in zip(previous["fixtures"], value["fixtures"]):
        if old["source"] != new["source"] or any(old.get(k) and old.get(k) != new.get(k) for k in (
            "google_event_id", "run_id", "notion_page_id", "discord_event_id",
            "source_attempted", "apply_attempted", "delete_attempted", "cleaned",
        )):
            return False
    if any(writes.get(k, [])[:len(v)] != v for k, v in previous["shared_writes"].items()):
        return False
    if previous["stage"] != "working" and any(previous.get(k) != value.get(k) for k in (
        "hashes", "pending_ids", "shared_writes", "expected_cursor",
    )):
        return False
    if value["stage"] == "cleanup":
        return value["step"] == previous["step"] and value.get("passed", False) == (
            previous.get("passed", False) if previous["stage"] == "cleanup" else
            previous["stage"] == "verified" and previous["step"] == LAST_STEP)
    if previous["stage"] == "cleanup":
        return False
    if value["step"] == previous["step"] + 1:
        return previous["stage"] == "verified" and value["stage"] == "working"
    return value["step"] == previous["step"] and value["stage"] in {
        "working": ("working", "ready"), "ready": ("verified",), "verified": ("verified",),
    }.get(previous["stage"], ())
