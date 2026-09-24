"""予定形式・件数・共有queue再試行の固定シナリオ所有記録。"""

import re

from e2e_discord_batch_state import slot_run_id
from e2e_google_sync_state import KEYS, MAX_BASELINE_DELETED, OWNER_FIELDS, digest, source_id

STATUSES = ("prepared",) * 5 + (
    "pending", "pending", "drained", "updated", "updated", "deleted", "deleted",
    "retry_pending", "retried",
    "retry_pending", "pending", "pending", "retried",
    "prepared", "prepared", "pending", "pending", "pending", "drained",
    "retry_pending", "retried", "retry_pending", "retried",
)

PENDING_COUNTS = {5: 3, 6: 1, 12: 1, 14: 3, 15: 2, 16: 1,
                  20: 5, 21: 3, 22: 1, 24: 1, 26: 1}


def final_step(owner):
    # 拡張前に失敗した5件runも、元の完了条件で回収できるようにする。
    return len(STATUSES) - 1 if owner.get("matrix_extended") is True else 17


def valid_transition(previous, value):
    if not value["dirty"]:
        return (
            previous == value if not previous.get("dirty") else
            previous.get("stage") == "cleanup"
            and value.get("last_run_id") == previous.get("run_id")
            and value.get("resource_fingerprints") == previous.get("target_fingerprints")
            and value.get("scope_sha256") == digest(previous["scope_id"])
            and value.get("outcome") == ("passed" if previous.get("passed") else "failed_clean")
        )
    if not (
        value.get("matrix") is True and value.get("full_apply") is True
        and value.get("retry_enabled") is True
        and re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", str(value.get("run_id", "")))
        and re.fullmatch(r"[0-9a-f]{32}", str(value.get("scope_id", "")))
        and type(value.get("matrix_extended", False)) is bool
        and type(value.get("step")) is int and 0 <= value["step"] <= final_step(value)
        and value.get("stage") in ("working", "ready", "verified", "cleanup")
        and isinstance(value.get("fixtures"), list) and len(value["fixtures"]) == (7 if value.get("matrix_extended") else 5)
        and isinstance(value.get("series"), dict)
    ):
        return False
    for key in ("hashes", "target_fingerprints", "baseline_deleted"):
        data = value.get(key)
        if not isinstance(data, dict) or any(not re.fullmatch(r"[a-f0-9]{64}", str(v)) for v in data.values()):
            return False
    if set(value["hashes"]) - set(KEYS) or set(value["target_fingerprints"]) != {
        "calendar_id_sha256", "guild_id_sha256", "notion_database_id_sha256", "state_scope_sha256",
    } or len(value["baseline_deleted"]) > MAX_BASELINE_DELETED:
        return False
    if any(not re.fullmatch(r"[a-f0-9]{64}", str(k)) for k in value["baseline_deleted"]):
        return False
    series = value["series"]
    root = series.get("source", {})
    if (root.get("id") != source_id(value["run_id"], 3)
            or root.get("recurrence") != ["RRULE:FREQ=DAILY;COUNT=2"]
            or root.get("summary") != f"[E2E] Google to Discord sync {slot_run_id(value['run_id'], 3)}"
            or (root.get("extendedProperties") or {}).get("private", {}).get("ie_event_bot_e2e_run") != slot_run_id(value["run_id"], 3)):
        return False
    for flag in ("source_attempted", "delete_attempted"):
        if type(series.get(flag)) is not bool:
            return False
    ids = []
    for index, slot in enumerate(value["fixtures"]):
        if not isinstance(slot, dict) or not isinstance(slot.get("source"), dict):
            return False
        source = slot["source"]
        if (
            slot.get("run_id") != slot_run_id(value["run_id"], index)
            or source.get("summary") != f"[E2E] Google to Discord sync {slot['run_id']}"
            or (source.get("extendedProperties") or {}).get("private", {}).get("ie_event_bot_e2e_run") != slot["run_id"]
            or any(type(slot.get(k)) is not bool for k in ("source_attempted", "apply_attempted", "delete_attempted"))
        ):
            return False
        event_id = slot.get("google_event_id")
        if index not in (3, 4):
            if event_id != source_id(value["run_id"], index) or source.get("id") != event_id:
                return False
        elif event_id is not None:
            if not isinstance(event_id, str) or not event_id or len(event_id) > 1024 or source.get("id") != event_id:
                return False
            if source.get("recurringEventId") != series["source"]["id"] or not source.get("originalStartTime"):
                return False
        if event_id:
            ids.append(event_id)
    if len(ids) != len(set(ids)) or (value["step"] >= 5 and len(ids) != len(value["fixtures"])):
        return False
    pending = value.get("pending_ids", [])
    if not isinstance(pending, list) or len(set(pending)) != len(pending) or not set(pending) <= set(ids):
        return False
    writes = value.get("shared_writes", {})
    if not isinstance(writes, dict) or set(writes) - set(KEYS) or any(
        not isinstance(items, list) or not items or len(items) > 32
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(v)) for v in items)
        for items in writes.values()
    ):
        return False
    if value["step"] < 5 and (value["hashes"] or writes or pending):
        return False
    if value["stage"] in ("ready", "verified") and len(pending) != PENDING_COUNTS.get(value["step"], 0):
        return False
    if not previous.get("dirty"):
        return (value["step"] == 0 and value["stage"] == "working"
                and not value["hashes"] and not writes
                and previous.get("last_run_id") != value["run_id"])
    if previous.get("matrix") is not True:
        return False
    if previous.get("matrix_extended") != value.get("matrix_extended"):
        return False
    if any(previous.get(k) != value.get(k) for k in (*OWNER_FIELDS, "baseline_deleted", "retry_enabled")):
        return False
    if previous["series"]["source"] != series["source"]:
        return False
    if any(previous["series"].get(k) and not series.get(k) for k in ("source_attempted", "delete_attempted")):
        return False
    for old, new in zip(previous["fixtures"], value["fixtures"]):
        if any(old.get(k) and old.get(k) != new.get(k) for k in (
            "google_event_id", "run_id", "notion_page_id", "discord_event_id",
            "source_attempted", "apply_attempted", "delete_attempted",
        )):
            return False
        if old["source"].get("originalStartTime") != new["source"].get("originalStartTime"):
            return False
    if any(writes.get(k, [])[:len(v)] != v for k, v in previous.get("shared_writes", {}).items()):
        return False
    if previous["stage"] != "working" and any(previous.get(k) != value.get(k) for k in (
        "hashes", "pending_ids", "shared_writes", "expected_cursor",
    )):
        return False
    if value["stage"] == "cleanup":
        return value["step"] == previous["step"] and value.get("passed", False) == (
            previous.get("passed", False) if previous["stage"] == "cleanup" else
            previous["stage"] == "verified" and previous["step"] == final_step(previous)
        )
    if previous["stage"] == "cleanup":
        return False
    if value["step"] == previous["step"] + 1:
        return previous["stage"] == "verified" and value["stage"] == "working"
    return value["step"] == previous["step"] and value["stage"] in {
        "working": ("working", "ready"), "ready": ("verified",), "verified": ("ready", "verified"),
    }.get(previous["stage"], ())
