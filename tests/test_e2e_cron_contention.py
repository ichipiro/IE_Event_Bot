import asyncio
import json
from types import SimpleNamespace

import pytest

import e2e_cron_contention as probe
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace
from tests.test_e2e_real_cron import RUN, START, worker


def setup(monkeypatch):
    base, controller = worker(monkeypatch)
    env = base.env
    env.STATE_KV = MemoryKV()
    env.SYNC_COORDINATOR = make_sync_coordinator_namespace()
    env.SYNC_DO_LOCK_ENABLED = "true"
    env.CRON_ENABLE_DISCORD_NOTION_SYNC = "true"
    env.INTERNAL_API_TOKEN = "test-only"
    handler = probe.Default()
    handler.env = env
    return handler, controller


def request(mode="probe", owner="", token="test-only", path="/sync/discord-notion", method="POST"):
    return Request("https://worker.test" + path, method=method, headers={
        "Authorization": "Bearer " + token, "X-E2E-Run-ID": RUN,
        "X-E2E-Probe-Mode": mode, "X-E2E-Owner": owner,
    })


@pytest.mark.parametrize("holder", ["cron", "manual"])
def test_real_dispatch_both_directions_and_release(monkeypatch, holder):
    handler, controller = setup(monkeypatch)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait(seconds):
            if seconds:
                entered.set()
                await release.wait()

        monkeypatch.setattr(probe, "asyncio", SimpleNamespace(sleep=wait))
        held = asyncio.create_task(handler.scheduled(controller, handler.env, None)
            if holder == "cron" else handler.fetch(request("hold")))
        await entered.wait()
        app = probe.ProbeApplication(handler.env, "probe")
        status = await probe.lock_status(app)
        assert status["holder"] == holder
        assert handler.env.STATE_KV.put_calls == []
        if holder == "cron":
            response = await handler.fetch(request(owner=status["owner_sha256"]))
            assert response.status == 409
            receipt = json.loads(await response.text())
        else:
            await handler.scheduled(controller, handler.env, None)
            receipt = json.loads(handler.env.STATE_KV.data[f"e2e:real-cron:{RUN}:{START + 60_000}"])
        assert receipt["status"] == 409
        assert receipt["body_calls"] == receipt["result_writes"] == 0
        assert receipt["before"]["owner_sha256"] == receipt["after"]["owner_sha256"]
        assert f"e2e:real-cron:{RUN}:result" not in handler.env.STATE_KV.data
        release.set()
        await held
        assert not (await probe.lock_status(app))["locked"]
        response = await handler.fetch(request("after"))
        assert response.status == 200
        result = json.loads(handler.env.STATE_KV.data[f"e2e:real-cron:{RUN}:result"])
        assert result["payload"]["probe_mode"] == "after"
        assert all(name == f"e2e:real-cron:{RUN}" for name in handler.env.SYNC_COORDINATOR.requested_names)
        assert handler.env.SYNC_COORDINATOR.stub.durable_object.ctx.storage.data == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("case,status", [("auth", 401), ("method", 405), ("owner", 412), ("mode", 400), ("expired", 409), ("flag", 409), ("version", 409), ("scheduled_http", 404)])
def test_request_guards(monkeypatch, case, status):
    handler, _ = setup(monkeypatch)
    req = request()
    if case == "auth":
        req = request(token="wrong")
    elif case == "method":
        req = request(method="GET")
    elif case == "mode":
        req = request("bad")
    elif case == "expired":
        handler.env.E2E_CRON_DEADLINE_MS = str(START + 60_100)
    elif case == "flag":
        handler.env.CRON_ENABLE_QA = "true"
    elif case == "version":
        handler.env.CF_VERSION_METADATA.tag = "other"
    elif case == "scheduled_http":
        req = request(path="/__scheduled")
    assert asyncio.run(handler.fetch(req)).status == status
    assert handler.env.STATE_KV.put_calls == []


def test_result_failure_releases_lock(monkeypatch):
    handler, _ = setup(monkeypatch)

    async def fail(*args):
        raise RuntimeError("kv_unavailable")

    handler.env.STATE_KV.put = fail
    with pytest.raises(RuntimeError, match="kv_unavailable"):
        asyncio.run(handler.fetch(request("after")))
    app = probe.ProbeApplication(handler.env, "probe")
    assert not asyncio.run(probe.lock_status(app))["locked"]
