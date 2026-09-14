"""snapshotとqueueの双方から未処理操作を復元する。"""

import json
from copy import deepcopy

PENDING_FIELD = "_pending_sync"


def split_snapshot(snapshot: dict) -> tuple[dict, list]:
    """既存の指紋形式を読み、追加した残件情報だけを比較対象から除く。"""
    observed, pending = dict(snapshot), []
    for event_id, text in snapshot.items():
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            continue  # 旧形式の扱いは通常の差分判定へ委ねる。
        if not isinstance(value, dict) or PENDING_FIELD not in value:
            continue
        op = value.pop(PENDING_FIELD)
        if (
            not isinstance(op, dict)
            or op.get("id") != event_id
            or op.get("op") not in ("upsert", "delete", "notify")
            or set(op) - {"op", "id", "notification"}
            or value.get("id") != event_id
        ):
            raise RuntimeError("discord_snapshot_pending_invalid")
        pending.append(op)
        if op["op"] == "delete":
            observed.pop(event_id, None)
        else:
            observed[event_id] = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return observed, pending


def merge_retry_ops(snapshot_ops: list, queue_ops: list) -> list:
    """どちらか一方に残る操作も維持し、投稿済みIDは古い値で消さない。"""
    merged = {}
    for original in [*queue_ops, *snapshot_ops]:
        if not isinstance(original, dict) or not original.get("id"):
            continue
        op = deepcopy(original)
        event_id = str(op["id"])
        if event_id not in merged:
            merged[event_id] = op
            continue
        previous = merged[event_id]
        # 未適用の可能性が残る場合は通知だけの再試行に縮めない。
        if op.get("op") == "upsert":
            previous["op"] = "upsert"
        new_delivery = op.get("notification")
        if new_delivery is None:
            continue
        old_delivery = previous.get("notification")
        if old_delivery is None:
            previous["notification"] = new_delivery
            continue
        if (
            not isinstance(old_delivery, dict)
            or not isinstance(new_delivery, dict)
            or old_delivery.get("channel_id") != new_delivery.get("channel_id")
            or (
                old_delivery.get("message_id")
                and new_delivery.get("message_id")
                and old_delivery["message_id"] != new_delivery["message_id"]
            )
        ):
            raise RuntimeError("discord_notification_state_conflict")
        if new_delivery.get("message_id"):
            previous["notification"] = new_delivery
    return list(merged.values())


def snapshot_with_pending(current: dict, previous: dict, pending: list) -> dict:
    """未処理の削除・通知も含めて、指紋と残件を1回のKV putにまとめる。"""
    snapshot = dict(current)
    for op in pending:
        event_id = op["id"]
        text = current.get(event_id, previous.get(event_id))
        try:
            value = json.loads(text) if text else None
        except (TypeError, ValueError):
            value = None
        if not isinstance(value, dict):
            value = {"id": event_id, "status": "1"}
        value["id"] = event_id
        value[PENDING_FIELD] = deepcopy(op)
        snapshot[event_id] = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return snapshot
