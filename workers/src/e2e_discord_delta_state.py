"""Discord差分E2Eの保存状態を1資源・1runに制限する。"""

import json


_DELTA_RESUME_REVISIONS = {"delta_prepared": 1, "delta_updated": 3}


def delta_ready_to_resume(manifest: dict) -> bool:
    event_id = str(manifest.get("discord_event_id") or "")
    checkpoint = manifest.get("delta_checkpoint")
    return (
        bool(event_id) and bool(manifest.get("notion_page_id"))
        and isinstance(checkpoint, dict) and valid_delta_checkpoint(checkpoint, event_id)
        and checkpoint["revision"] == _DELTA_RESUME_REVISIONS.get(str(manifest.get("stage") or ""))
        and checkpoint["queue"] == [] and set(checkpoint["snapshot"]) == {event_id}
    )


def delta_owner_matches(manifest: dict, owner: dict) -> bool:
    return (
        manifest.get("kind") == "discord_delta_sync"
        and manifest.get("version") == 1
        and manifest.get("dirty") is True
        and all(owner.get(key) and owner[key] == manifest.get(key) for key in (
            "run_id", "discord_event_id", "target_fingerprints",
        ))
    )


def valid_delta_checkpoint(value, event_id: str) -> bool:
    if not isinstance(value, dict) or set(value) != {"revision", "snapshot", "queue"}:
        return False
    if type(value["revision"]) is not int or value["revision"] < 1:
        return False
    snapshot, queue = value["snapshot"], value["queue"]
    if not isinstance(snapshot, dict) or set(snapshot) - {event_id}:
        return False
    for fingerprint in snapshot.values():
        if not isinstance(fingerprint, str):
            return False
        try:
            event = json.loads(fingerprint)
        except (ValueError, TypeError):
            return False
        if not isinstance(event, dict) or str(event.get("id") or "") != event_id:
            return False
    return isinstance(queue, list) and len(queue) <= 1 and all(
        isinstance(op, dict) and set(op) == {"id", "op"}
        and op["id"] == event_id and op["op"] in ("upsert", "delete")
        for op in queue
    )
