"""通知の永続化、Alarm再開、同時追加と所有回収を検証する。"""
import asyncio
import json
from types import SimpleNamespace

from workers import Response

import google_webhook_queue as queue
from entry import Default
from state import StateStore
from tests.fakes import Request, MemoryStorage
from tests.test_e2e_sync_lock_probe import environment, RUN, OTHER


def notification(number="1", run_id=""):
    return {"channel_id": "test-channel", "resource_id": "resource", "message_number": number,
            "resource_state": "exists", "run_id": run_id, "step": 1 if run_id else 0}


def test_alarm_restarts_from_storage_and_retries_failed_dispatch(monkeypatch):
    storage = MemoryStorage()
    calls = []

    async def dispatch(env, item):
        calls.append(item)
        return Response("", status=503 if len(calls) == 1 else 204)
    monkeypatch.setattr(queue, "dispatch_notification", dispatch)

    async def scenario():
        assert (await queue.queue_action(storage, {"notification": notification()}))[0]["ok"]
        assert storage.alarm_at is not None
        assert (await queue.queue_action(storage, {"notification": notification()}))[0]["ok"]
        assert len(await queue.read_queue(storage)) == 1
        await queue.run_alarm(SimpleNamespace(), storage)
        assert len(await queue.read_queue(storage)) == 1
        assert storage.alarm_at is not None
        await queue.run_alarm(SimpleNamespace(), storage)
        assert not await queue.read_queue(storage)
        assert storage.alarm_at is None
        assert len(calls) == 2
    asyncio.run(scenario())


def test_alarm_does_not_lose_notification_enqueued_during_network_wait(monkeypatch):
    storage = MemoryStorage()
    async def dispatch(env, item):
        await queue.queue_action(storage, {"notification": notification("2")})
        return Response("", status=204)
    monkeypatch.setattr(queue, "dispatch_notification", dispatch)

    async def scenario():
        await queue.queue_action(storage, {"notification": notification()})
        await queue.run_alarm(SimpleNamespace(), storage)
        assert [item["message_number"] for item in (await queue.read_queue(storage)).values()] == ["2"]
        assert storage.alarm_at is not None
    asyncio.run(scenario())


def test_failed_alarm_registration_never_acknowledges_notification(monkeypatch):
    env = environment()
    env.GCAL_WEBHOOK_TOKEN = "test-token"
    worker = Default()
    worker.env = env
    storage = env.SYNC_COORDINATOR.getByName(queue.queue_name()).durable_object.ctx.storage
    async def failed(*args):
        raise RuntimeError("fixed failure")
    monkeypatch.setattr(storage, "setAlarm", failed)
    request = Request("https://test/gcal/webhook", method="POST", headers={
        "X-Goog-Channel-Token": "test-token", "X-Goog-Channel-ID": "channel",
        "X-Goog-Message-Number": "1"})
    assert asyncio.run(worker.fetch(request)).status == 503
    assert not storage.data


def test_cleanup_only_clears_owned_notifications_and_alarm():
    storage = MemoryStorage()
    async def scenario():
        await queue.queue_action(storage, {"notification": notification(run_id=RUN)})
        assert (await queue.clear_queue(storage, {"run_id": OTHER}))[1] == 409
        assert storage.alarm_at is not None
        assert (await queue.clear_queue(storage, {"run_id": RUN}))[0]["ok"]
        assert not await queue.read_queue(storage)
        assert storage.alarm_at is None
    asyncio.run(scenario())


def test_alarm_executes_normal_sync_and_duplicate_runs_once(monkeypatch):
    from tests.test_webhook_retry import setup_worker
    worker, request, calls, _ = setup_worker(monkeypatch)
    worker.env.GCAL_WEBHOOK_URL = request.url
    stub = worker.env.SYNC_COORDINATOR.getByName(queue.queue_name())
    stub.durable_object.env = worker.env
    async def scenario():
        assert (await worker.fetch(request)).status == 204
        assert not calls
        await stub.durable_object.alarm()
        assert calls == ["fetch"]
        assert (await worker.fetch(request)).status == 204
        await stub.durable_object.alarm()
        assert calls == ["fetch"]
        assert not await queue.read_queue(stub.durable_object.ctx.storage)
        result = await StateStore(worker.env).get_json("result:sync_all")
        assert result is not None and result["payload"]["ok"]
    asyncio.run(scenario())


def test_alarm_exception_keeps_notification_and_scheduled_retry(monkeypatch):
    storage = MemoryStorage()
    async def failed(*args):
        raise RuntimeError("fixed failure")
    monkeypatch.setattr(queue, "dispatch_notification", failed)
    async def scenario():
        await queue.queue_action(storage, {"notification": notification()})
        await queue.run_alarm(SimpleNamespace(), storage)
        assert len(json.loads(storage.data[queue.QUEUE_KEY])) == 1
        assert storage.alarm_at is not None
    asyncio.run(scenario())
