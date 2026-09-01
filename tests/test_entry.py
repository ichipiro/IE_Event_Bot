"""Worker の HTTP 入口と同期ディスパッチを検証する。"""

import asyncio
import json
from types import SimpleNamespace

from workers import Response

import state as state_module
from entry import Default
from state import StateStore
from sync_lock_do import SyncCoordinator
from tests.fakes import DurableObjectNamespace, MemoryKV, Request, make_durable_object


def run(coroutine):
    return asyncio.run(coroutine)


def response_json(response: Response) -> dict:
    return json.loads(run(response.text()))


def make_worker(env) -> Default:
    worker = Default()
    worker.env = env
    return worker


def test_health_reports_kv_binding() -> None:
    worker = make_worker(SimpleNamespace(STATE_KV=MemoryKV()))

    response = run(worker.fetch(Request("https://bot.test/health")))

    assert response.status == 200
    assert response_json(response) == {"ok": True, "kv_state_enabled": True}


def test_protected_route_requires_exact_bearer_token() -> None:
    worker = make_worker(SimpleNamespace(INTERNAL_API_TOKEN="test-token"))

    unauthorized = run(worker.fetch(Request("https://bot.test/jobs/qa-check")))

    assert unauthorized.status == 401
    assert run(unauthorized.text()) == "unauthorized"
    assert not worker._authorized(
        Request(
            "https://bot.test/jobs/qa-check",
            headers={"Authorization": "Bearer wrong-token"},
        )
    )
    assert worker._authorized(
        Request(
            "https://bot.test/jobs/qa-check",
            headers={"authorization": "Bearer test-token"},
        )
    )


def test_protected_route_rejects_when_internal_token_is_missing() -> None:
    worker = make_worker(SimpleNamespace())

    response = run(worker.fetch(Request("https://bot.test/jobs/qa-check")))

    assert response.status == 401
    assert run(response.text()) == "unauthorized"


def test_webhook_rejects_when_channel_token_secret_is_missing() -> None:
    worker = make_worker(SimpleNamespace())
    dispatch_sources: list[str] = []

    async def fake_dispatch(request, state, source):
        dispatch_sources.append(source)
        return Response("", status=200)

    worker._run_sync_dispatch = fake_dispatch

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/gcal/webhook",
                method="POST",
                headers={"X-Goog-Channel-Token": "incoming-token"},
            )
        )
    )

    assert response.status == 503
    assert dispatch_sources == []


def test_webhook_rejects_non_post_method() -> None:
    worker = make_worker(SimpleNamespace(GCAL_WEBHOOK_TOKEN="channel-token"))

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/gcal/webhook",
                method="GET",
                headers={"X-Goog-Channel-Token": "channel-token"},
            )
        )
    )

    assert response.status == 405
    assert response_json(response) == {"ok": False, "error": "method_not_allowed"}


def test_webhook_rejects_missing_or_wrong_channel_token() -> None:
    worker = make_worker(SimpleNamespace(GCAL_WEBHOOK_TOKEN="channel-token"))
    dispatch_sources: list[str] = []

    async def fake_dispatch(request, state, source):
        dispatch_sources.append(source)
        return Response("", status=200)

    worker._run_sync_dispatch = fake_dispatch

    missing = run(
        worker.fetch(Request("https://bot.test/gcal/webhook", method="POST"))
    )
    wrong = run(
        worker.fetch(
            Request(
                "https://bot.test/gcal/webhook",
                method="POST",
                headers={"X-Goog-Channel-Token": "wrong-token"},
            )
        )
    )

    assert missing.status == 401
    assert wrong.status == 401
    assert dispatch_sources == []


def test_sync_dispatch_stops_during_cooldown(monkeypatch) -> None:
    kv = MemoryKV({"sync:last_epoch": "950"})
    env = SimpleNamespace(
        STATE_KV=kv,
        SYNC_INTERVAL_SECONDS="300",
        SYNC_DO_LOCK_ENABLED="false",
    )
    worker = make_worker(env)
    monkeypatch.setattr(state_module.time, "time", lambda: 1000.0)

    response = run(
        worker._run_sync_dispatch(
            None,
            StateStore(env),
            source="manual",
        )
    )

    assert response.status == 200
    assert response_json(response) == {
        "ok": True,
        "status": "cooldown_skip",
        "interval_seconds": 300.0,
        "source": "manual",
    }


def test_sync_dispatch_stops_when_durable_lock_is_held() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    namespace = DurableObjectNamespace(coordinator)
    env = SimpleNamespace(
        SYNC_COORDINATOR=namespace,
        SYNC_DO_LOCK_ENABLED="true",
    )
    worker = make_worker(env)
    acquired = run(
        namespace.stub.fetch(
            "https://sync-lock/acquire",
            method="POST",
            body=json.dumps({"action": "acquire", "owner": "existing", "ttl_seconds": 120}),
        )
    )
    assert acquired.status == 200

    response = run(
        worker._run_sync_dispatch(
            None,
            StateStore(env),
            source="manual",
        )
    )
    payload = response_json(response)

    assert response.status == 200
    assert payload["status"] == "in_progress_skip"
    assert payload["lock"]["locked"] is True
    assert payload["lock"]["owner"] == "existing"


def test_webhook_duplicate_dispatches_only_once() -> None:
    kv = MemoryKV()
    env = SimpleNamespace(
        STATE_KV=kv,
        KV_GCAL_DEDUPE_ENABLED="true",
        GCAL_WEBHOOK_TOKEN="channel-token",
    )
    worker = make_worker(env)
    dispatch_sources: list[str] = []

    async def fake_dispatch(request, state, source):
        dispatch_sources.append(source)
        return Response("", status=200)

    worker._run_sync_dispatch = fake_dispatch
    headers = {
        "X-Goog-Channel-ID": "channel-1",
        "X-Goog-Message-Number": "42",
        "X-Goog-Channel-Token": "channel-token",
    }

    first = run(
        worker.fetch(Request("https://bot.test/gcal/webhook", method="POST", headers=headers))
    )
    second = run(
        worker.fetch(Request("https://bot.test/gcal/webhook", method="POST", headers=headers))
    )

    assert first.status == 204
    assert second.status == 204
    assert dispatch_sources == ["webhook"]
    assert kv.data["gcal_msg:channel-1:42"] == "1"
