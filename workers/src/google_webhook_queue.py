"""Webhook通知を永続化し、接続寿命に依存しないDO Alarmから同期する。"""
import json
import time
from hashlib import sha256
from types import SimpleNamespace

from workers import Response

from state import StateStore

QUEUE_KEY = "gcal_webhook_queue"
MAX_PENDING = 128
RETRY_SECONDS = 60


def queue_name(run_id=""):
    return f"e2e:gcal-webhook:{run_id}" if run_id else "gcal-webhook"


async def enqueue(env, request, *, run_id="", step=0):
    from entry import _header
    fields = {key: _header(request, header) or "" for key, header in (
        ("channel_id", "X-Goog-Channel-ID"), ("message_number", "X-Goog-Message-Number"),
        ("resource_id", "X-Goog-Resource-ID"), ("resource_state", "X-Goog-Resource-State"),
    )}
    if not fields["channel_id"] or not fields["message_number"] or any(len(v) > 512 for v in fields.values()):
        return Response("invalid notification", status=400)
    try:
        store = StateStore(env)
        stub = env.SYNC_COORDINATOR.getByName(queue_name(run_id))
        result = await store._sync_do_rpc(stub, "enqueue_google_webhook", {
            "notification": {**fields, "run_id": run_id, "step": step},
        })
        if result and result.get("ok"):
            return Response("", status=204)
    except Exception:
        pass  # 永続化・Alarm予約が確認できなければ成功を返さない。
    return Response("webhook unavailable", status=503)


async def queue_action(storage, payload):
    notification = payload.get("notification")
    if (not isinstance(notification, dict)
            or set(notification) != {"channel_id", "message_number", "resource_id", "resource_state", "run_id", "step"}
            or any(not isinstance(notification[k], str) or len(notification[k]) > 512
                   for k in ("channel_id", "message_number", "resource_id", "resource_state", "run_id"))
            or not notification["channel_id"] or not notification["message_number"]
            or type(notification["step"]) is not int):
        return {"ok": False}, 400
    pending = await read_queue(storage)
    key = sha256(json.dumps([notification["channel_id"], notification["message_number"]]).encode()).hexdigest()
    if key not in pending:
        if len(pending) >= MAX_PENDING:
            return {"ok": False}, 503
        # Alarmを先に予約する。直後に停止しても、未保存通知には成功応答を返さない。
        await storage.setAlarm(int(time.time() * 1000) + 1000)
        pending[key] = notification
        await storage.put(QUEUE_KEY, json.dumps(pending))
    else:
        alarm_at = await storage.getAlarm()
        if alarm_at is None or str(alarm_at) in ("jsnull", "jsundefined"):
            await storage.setAlarm(int(time.time() * 1000) + 1000)
    return {"ok": True}, 200


async def read_queue(storage):
    raw = await storage.get(QUEUE_KEY)
    return json.loads("{}" if raw is None or str(raw) in ("jsnull", "jsundefined") else str(raw))


async def clear_owned_queue(env, run_id):
    store = StateStore(env)
    result = await store._sync_do_rpc(env.SYNC_COORDINATOR.getByName(queue_name(run_id)),
                                     "clear_google_webhook_queue", {"run_id": run_id})
    if not result or result.get("ok") is not True:
        raise RuntimeError("google_webhook_queue_cleanup_failed")


async def clear_queue(storage, payload):
    run_id = payload.get("run_id")
    from sync_lock_do import _E2E_RUN_ID_PATTERN
    if not isinstance(run_id, str) or not _E2E_RUN_ID_PATTERN.fullmatch(run_id):
        return {"ok": False}, 400
    pending = await read_queue(storage)
    if any(item.get("run_id") != run_id for item in pending.values()):
        return {"ok": False}, 409
    await storage.deleteAlarm()
    await storage.delete(QUEUE_KEY)
    return {"ok": not bool(await read_queue(storage))}, 200


async def dispatch_notification(env, item):
    from entry import Application
    request = SimpleNamespace(url=env.GCAL_WEBHOOK_URL, method="POST", headers={
        "X-Goog-Channel-Token": env.GCAL_WEBHOOK_TOKEN,
        "X-Goog-Channel-ID": item["channel_id"], "X-Goog-Message-Number": item["message_number"],
        "X-Goog-Resource-ID": item["resource_id"], "X-Goog-Resource-State": item["resource_state"],
    })
    if item["run_id"]:
        if str(getattr(env, "E2E_WATCH_SHARED_ENABLED", "false")).lower() != "true":
            return Response("", status=503)
        from e2e_watch_shared_probe import callback
        response = await callback(env, StateStore(env), request, deferred=True,
                                  expected_run=item["run_id"], expected_step=item["step"])
        # 回収済み・旧stepの通知を通常経路へ流さない。
        return response if response is not None else Response("", status=204)
    return await Application(env)._handle_gcal_webhook(request, StateStore(env))


async def run_alarm(env, storage):
    pending = await read_queue(storage)
    if not pending:
        return
    # 実行中の異常終了でも再開できるよう、先に次のAlarmを永続化する。
    await storage.setAlarm(int((time.time() + RETRY_SECONDS) * 1000))
    key, item = next(iter(pending.items()))
    try:
        response = await dispatch_notification(env, item)
    except Exception:
        # 通知と予約済みAlarmを維持し、プラットフォームの有限回再試行に依存しない。
        print("google_webhook_retry_pending")
        return
    # API待ちの間に追加された通知を上書きしない。
    pending = await read_queue(storage)
    if response.status == 204 and pending.get(key) == item:
        del pending[key]
        if pending:
            await storage.put(QUEUE_KEY, json.dumps(pending))
            await storage.setAlarm(int(time.time() * 1000) + 1000)
        else:
            await storage.delete(QUEUE_KEY)
            await storage.deleteAlarm()
