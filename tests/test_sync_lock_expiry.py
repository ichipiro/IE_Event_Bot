"""期限切れの旧同期が新しい結果や所有者を壊さないことを検証する。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import sync_lock_do
from e2e_entry import Default
from state import StateStore
from tests.test_e2e_sync_lock_probe import environment


@pytest.mark.parametrize("source", ["manual", "cron", "all"])
@pytest.mark.parametrize("replacement", [False, True])
def test_expired_owner_cannot_save_result(monkeypatch, source, replacement):
    clock = [1000.0]
    monkeypatch.setattr(sync_lock_do, "time", SimpleNamespace(time=lambda: clock[0]))
    worker = Default()
    worker.env = environment()
    state = StateStore(worker.env)

    async def scenario():
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def old_body():
            entered.set()
            await resume.wait()
            return {"ok": True, "writer": "old"}

        old = asyncio.create_task(worker._invoke_lock_probe(source, state, old_body))
        await entered.wait()
        clock[0] += 121
        replacement_owner = None
        if replacement:

            async def new_body():
                return {"ok": True, "writer": "new"}

            result, status = await worker._invoke_lock_probe(source, state, new_body)
            assert status == 200 and result["ok"]
            replacement_owner = await worker._acquire_sync_lock("replacement")
            assert replacement_owner["ok"]
        previous = dict(worker.env.STATE_KV.data)
        resume.set()
        result, status = await old
        assert status == 409 and result["error"] == "sync_lock_lost"
        assert worker.env.STATE_KV.data == previous
        if replacement_owner:
            assert (await worker._sync_lock_status())["lock"][
                "owner"
            ] == replacement_owner["owner"]
            await worker._release_sync_lock(replacement_owner["owner"])

    asyncio.run(scenario())


def test_expiry_between_google_apply_and_cursor_does_not_commit(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sync_lock_do, "time", SimpleNamespace(time=lambda: clock[0]))
    worker = Default()
    worker.env = environment()
    calls = []

    async def fetch(env, state, *, commit_cursor):
        return {"ok": True, "items": [], "next_updated_min": "cursor-after-apply"}

    async def apply(*args):
        calls.append("apply")
        clock[0] += 121
        return {"ok": True}

    async def discord(*args):
        calls.append("discord")
        return {"ok": True}

    response = asyncio.run(
        worker._run_sync_dispatch(
            None,
            StateStore(worker.env),
            "manual",
            google_fetcher=fetch,
            google_applier=apply,
            discord_runner=discord,
        )
    )
    assert response.status == 409
    assert json.loads(asyncio.run(response.text()))["error"] == "sync_lock_lost"
    assert calls == ["apply"]
    assert "sync:updated_min" not in worker.env.STATE_KV.data


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "detail": "private diagnostics"},
        {"ok": True, "lock": {}},
        {"ok": True, "lock": {"owner": "mine", "expires_at": "bad", "now": 1}},
        {"ok": True, "lock": {"owner": "mine", "expires_at": float("inf"), "now": 1}},
        {"ok": True, "lock": {"owner": "mine", "expires_at": 2, "now": float("nan")}},
        {"ok": True, "lock": {"owner": "other", "expires_at": 2, "now": 1}},
        {"ok": True, "lock": {"owner": "mine", "expires_at": 2, "now": 2}},
    ],
)
def test_lease_read_errors_and_invalid_time_fail_closed(monkeypatch, payload):
    import entry

    worker = Default()
    worker.env = environment()

    async def status():
        return payload

    monkeypatch.setattr(worker, "_sync_lock_status", status)
    with pytest.raises(entry.SyncLockLost) as error:
        asyncio.run(worker._require_sync_owner("mine"))
    assert str(error.value) == ""
