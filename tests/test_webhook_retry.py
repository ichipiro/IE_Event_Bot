"""通常Webhookの同一通知再試行と処理中の排他を検証する。"""
import asyncio
from types import SimpleNamespace

import pytest

import entry
from entry import Default
from state import StateStore
from sync_lock_do import SyncCoordinator
from tests.fakes import DurableObjectNamespace, MemoryKV, Request, make_durable_object


def setup_worker(monkeypatch):
    env = SimpleNamespace(STATE_KV=MemoryKV(), SYNC_COORDINATOR=DurableObjectNamespace(make_durable_object(SyncCoordinator())),
                          GCAL_WEBHOOK_TOKEN="test-token", KV_GCAL_DEDUPE_ENABLED="true",
                          KV_SYNC_COOLDOWN_ENABLED="false", SYNC_DO_LOCK_ENABLED="true")
    worker = Default()
    worker.env = env
    calls = []

    async def fetch(env, state, *, commit_cursor):
        calls.append("fetch")
        return {"ok": True, "items": [], "next_updated_min": "2026-09-25T00:00:00Z"}

    async def apply(env, state, items):
        return {"ok": True}

    monkeypatch.setattr(entry, "run_google_delta_fetch", fetch)
    monkeypatch.setattr(entry, "apply_google_events", apply)
    request = Request("https://bot.test/gcal/webhook", method="POST", headers={
        "X-Goog-Channel-Token": "test-token", "X-Goog-Channel-ID": "retry-channel",
        "X-Goog-Message-Number": "42"})
    return worker, request, calls, fetch


@pytest.mark.parametrize("failure", ["google", "exception", "busy", "cooldown"])
def test_same_notification_retries_after_failure(monkeypatch, failure):
    worker, request, calls, successful_fetch = setup_worker(monkeypatch)

    async def scenario():
        lock = None
        if failure == "busy":
            lock = await worker._acquire_sync_lock(source="manual")
        elif failure == "cooldown":
            worker.env.KV_SYNC_COOLDOWN_ENABLED = "true"
            await StateStore(worker.env).set_sync_last_epoch_now()
        else:
            async def failed(*args, **kwargs):
                if failure == "exception":
                    raise RuntimeError("fixed failure")
                return {"ok": False}
            monkeypatch.setattr(entry, "run_google_delta_fetch", failed)
        try:
            response = await worker._handle_gcal_webhook(request, StateStore(worker.env))
            assert response.status in (500, 503)
        finally:
            if lock:
                await worker._release_sync_lock(lock["owner"])
        worker.env.KV_SYNC_COOLDOWN_ENABLED = "false"
        monkeypatch.setattr(entry, "run_google_delta_fetch", successful_fetch)
        assert (await worker._handle_gcal_webhook(request, StateStore(worker.env))).status == 204
        assert (await worker._handle_gcal_webhook(request, StateStore(worker.env))).status == 204
        assert calls == ["fetch"]
    asyncio.run(scenario())


def test_inflight_duplicate_waits_for_success_without_losing_retry(monkeypatch):
    worker, request, calls, successful_fetch = setup_worker(monkeypatch)

    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()

        async def paused(*args, **kwargs):
            started.set()
            await resume.wait()
            return await successful_fetch(*args, **kwargs)

        monkeypatch.setattr(entry, "run_google_delta_fetch", paused)
        task = asyncio.create_task(worker._handle_gcal_webhook(request, StateStore(worker.env)))
        await asyncio.wait_for(started.wait(), 2)
        try:
            assert (await worker._handle_gcal_webhook(request, StateStore(worker.env))).status == 503
        finally:
            resume.set()
            assert (await task).status == 204
        assert (await worker._handle_gcal_webhook(request, StateStore(worker.env))).status == 204
        assert calls == ["fetch"]
    asyncio.run(scenario())


def test_public_webhook_persists_before_ack_without_dispatch(monkeypatch):
    worker, request, calls, _ = setup_worker(monkeypatch)
    async def scenario():
        assert (await worker.fetch(request)).status == 204
        assert calls == []
    asyncio.run(scenario())


def test_expired_claim_cannot_complete_or_release_new_owner(monkeypatch):
    import sync_lock_do
    worker, _, _, _ = setup_worker(monkeypatch)
    store = StateStore(worker.env)
    now = [100.0]
    monkeypatch.setattr(sync_lock_do.time, "time", lambda: now[0])

    async def scenario():
        first = await store.claim_google_message("channel", "1", 10)
        assert (await store.claim_google_message("channel", "1", 10))["status"] == "busy"
        now[0] = 111
        second = await store.claim_google_message("channel", "1", 10)
        for succeeded in (False, True):
            with pytest.raises(RuntimeError, match="finish_failed"):
                await store.finish_google_message(first, succeeded=succeeded)
        await store.finish_google_message(second, succeeded=True)
        assert (await store.claim_google_message("channel", "1", 10))["status"] == "duplicate"
    asyncio.run(scenario())


def test_canceled_sync_releases_message_for_retry(monkeypatch):
    worker, request, calls, successful_fetch = setup_worker(monkeypatch)

    async def scenario():
        async def canceled(*args, **kwargs):
            raise asyncio.CancelledError()
        monkeypatch.setattr(entry, "run_google_delta_fetch", canceled)
        with pytest.raises(asyncio.CancelledError):
            await worker._handle_gcal_webhook(request, StateStore(worker.env))
        monkeypatch.setattr(entry, "run_google_delta_fetch", successful_fetch)
        assert (await worker._handle_gcal_webhook(request, StateStore(worker.env))).status == 204
        assert calls == ["fetch"]
    asyncio.run(scenario())
