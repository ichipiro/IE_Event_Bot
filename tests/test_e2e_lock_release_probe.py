"""実DO障害注入用probeの所有境界と、失敗途中の回収を確認する。"""

import asyncio
from types import SimpleNamespace

import pytest

import e2e_lock_release_probe as probe
from e2e_google_sync_state import GoogleStateError
from state import StateStore
from tests.fakes import make_sync_coordinator_namespace

RUN = "E2E-20260925T100000Z-123456ab"


def test_probe_cleanup_preserves_foreign_lock():
    env = SimpleNamespace(SYNC_COORDINATOR=make_sync_coordinator_namespace())

    async def scenario():
        stub = env.SYNC_COORDINATOR.getByName(probe.probe_name(RUN))
        await StateStore._sync_do_rpc(stub, "acquire", {"owner": "foreign-owner"})
        with pytest.raises(GoogleStateError, match="release_probe_verification_failed"):
            await probe.cleanup(env, RUN)
        status = await StateStore._sync_do_rpc(stub, "status")
        assert status and status["lock"]["owner"] == "foreign-owner"

    asyncio.run(scenario())


def test_failed_probe_still_releases_owned_lock(monkeypatch):
    env = SimpleNamespace(SYNC_COORDINATOR=make_sync_coordinator_namespace())

    async def broken_release(self, owner):
        return

    monkeypatch.setattr(probe.Application, "_release_sync_lock", broken_release, raising=False)

    async def scenario():
        owner = {"run_id": RUN, "dirty": True, "webhook_sync": True, "stages": {}}
        with pytest.raises(GoogleStateError, match="release_probe_verification_failed"):
            await probe.run(env, StateStore(env), owner)
        stub = env.SYNC_COORDINATOR.getByName(probe.probe_name(RUN))
        status = await StateStore._sync_do_rpc(stub, "status")
        assert status and not status["lock"].get("owner")
        assert owner["stages"] == {}

    asyncio.run(scenario())
