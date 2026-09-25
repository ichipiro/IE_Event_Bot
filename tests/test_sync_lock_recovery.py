"""解放の応答喪失・接続故障と、所有者交代時の復旧を検証する。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import sync_lock_release as recovery
from e2e_google_sync_probe import _release_control
from entry import Default
from state import StateStore
from tests.fakes import make_sync_coordinator_namespace


@pytest.fixture(autouse=True)
def fast_recovery(monkeypatch):
    monkeypatch.setattr(recovery, "_RECOVERY_DELAYS", (0, 0, 0))


@pytest.mark.parametrize("scope", ["global", "e2e:google-sync-control"])
@pytest.mark.parametrize("failure", ["before", "after", "false", "malformed"])
def test_release_recovers_with_new_connection(scope, failure, capsys):
    namespace = make_sync_coordinator_namespace()
    backing = namespace.getByName(scope)
    calls = []
    stubs = []
    releases = []

    def fresh(name):
        assert name == scope
        connection = len(stubs)

        async def rpc(raw):
            payload = json.loads(raw)
            calls.append((connection, payload["action"]))
            if payload["action"] == "release":
                releases.append(payload)
                if len(releases) == 1:
                    if failure == "after":
                        await backing.sync_state(raw)
                    if failure == "false":
                        return '{"ok": false}'
                    if failure == "malformed":
                        return "private-response"
                    raise RuntimeError("private-exception")
            return await backing.sync_state(raw)

        stub = SimpleNamespace(sync_state=rpc)
        stubs.append(stub)
        return stub

    env = SimpleNamespace(SYNC_COORDINATOR=SimpleNamespace(getByName=fresh))

    async def scenario():
        await StateStore._sync_do_rpc(backing, "acquire", {"owner": "private-owner", "ttl_seconds": 300})
        if scope == "global":
            worker = Default()
            worker.env = env
            await worker._release_sync_lock("private-owner")
        else:
            assert await _release_control(StateStore(env), fresh(scope), "private-owner") is None
        state = await StateStore._sync_do_rpc(backing, "status")
        assert state is not None
        assert not state["lock"].get("owner")

    asyncio.run(scenario())
    assert len(releases) == (1 if failure == "after" else 2)
    assert all(item == {"action": "release", "owner": "private-owner"} for item in releases)
    assert len(stubs) >= 2
    assert any(connection > 0 and action == "status" for connection, action in calls)
    output = capsys.readouterr().out
    assert "sync_lock_release_recovered" in output
    assert "private" not in output


@pytest.mark.parametrize("replacement", ["before_status", "before_release"])
def test_recovery_never_releases_replacement_owner(replacement, capsys):
    backing = make_sync_coordinator_namespace().getByName("global")
    releases = []
    status_reads = []

    async def replace():
        await StateStore._sync_do_rpc(backing, "release", {"owner": "old-owner"})
        await StateStore._sync_do_rpc(backing, "acquire", {"owner": "new-owner"})

    async def rpc(stub, action, payload=None):
        if action == "release":
            await replace()
            releases.append(payload)
        else:
            status_reads.append(action)
        return await StateStore._sync_do_rpc(stub, action, payload)

    async def scenario():
        await StateStore._sync_do_rpc(backing, "acquire", {"owner": "old-owner"})
        if replacement == "before_status":
            await replace()
        result = await recovery._recover_release(lambda: backing, rpc, "old-owner", "global")
        assert result["ok"] is True
        status = await StateStore._sync_do_rpc(backing, "status")
        assert status is not None
        assert status["lock"]["owner"] == "new-owner"

    asyncio.run(scenario())
    assert releases == ([] if replacement == "before_status" else [{"owner": "old-owner"}])
    assert len(status_reads) == (1 if replacement == "before_status" else 2)
    output = capsys.readouterr().out
    assert "owner" not in output.replace("owner_check", "")


@pytest.mark.parametrize("failure", ["release_exception", "release_noop", "status_exception", "status_invalid", "timeout"])
def test_recovery_is_bounded_and_does_not_assume_success(monkeypatch, failure, capsys):
    calls = []
    connections = []
    monkeypatch.setattr(recovery, "_RPC_TIMEOUT_SECONDS", 0.01)

    def fresh():
        stub = object()
        connections.append(stub)
        return stub

    async def rpc(stub, action, payload=None):
        calls.append((stub, action, payload))
        if failure == "timeout":
            await asyncio.Event().wait()
        if action == "status":
            if failure == "status_exception":
                raise RuntimeError("private-exception")
            if failure == "status_invalid":
                return {"ok": True, "lock": {"owner": None}}
            return {"ok": True, "lock": {"owner": "private-owner"}}
        if failure == "release_exception":
            raise RuntimeError("private-exception")
        return {"ok": True}  # 保存状態に反映されなかった成功応答。

    result = asyncio.run(recovery._recover_release(fresh, rpc, "private-owner", "global"))
    assert result["ok"] is False and result["attempts"] == 3
    assert len(connections) == len(set(connections)) == 3
    assert sum(action == "status" for _, action, _ in calls) == 3
    assert sum(action == "release" for _, action, _ in calls) == (2 if failure.startswith("release") else 0)
    output = capsys.readouterr().out
    assert "sync_lock_release_recovery_failed" in output and "private" not in output


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("flag", ["overloaded", "retryable"])
def test_explicit_nonretryable_error_stops_recovery(flag, wrapped):
    calls = []
    exc = RuntimeError("private-error")
    properties = {flag: flag == "overloaded"}
    if wrapped:
        setattr(exc, "js_error", SimpleNamespace(**properties))
    else:
        setattr(exc, flag, properties[flag])

    async def rpc(stub, action, payload=None):
        calls.append(action)
        raise exc

    assert recovery._release_retry_blocked(exc)
    result = asyncio.run(recovery._recover_release(object, rpc, "owner", "global"))
    assert not result["ok"] and calls == ["status"]


def test_last_release_response_loss_is_verified_without_third_write():
    calls = []
    owner = ["owner"]

    async def rpc(stub, action, payload=None):
        calls.append(action)
        if action == "status":
            return {"ok": True, "lock": {"owner": owner[0]}}
        if calls.count("release") == 2:
            owner[0] = ""
        raise RuntimeError("response lost")

    result = asyncio.run(recovery._recover_release(object, rpc, "owner", "global"))
    assert result["ok"] is True
    assert calls == ["status", "release", "status", "release", "status"]


@pytest.mark.parametrize("scope", ["global", "e2e:google-sync-control"])
def test_initial_release_timeout_recovers(monkeypatch, scope):
    namespace = make_sync_coordinator_namespace()
    backing = namespace.getByName(scope)
    monkeypatch.setattr("entry._RPC_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("e2e_google_sync_probe._RPC_TIMEOUT_SECONDS", 0.01)
    first = [True]

    def fresh(name):
        async def rpc(raw):
            if json.loads(raw)["action"] == "release" and first[0]:
                first[0] = False
                await asyncio.Event().wait()
            return await backing.sync_state(raw)
        return SimpleNamespace(sync_state=rpc)

    env = SimpleNamespace(SYNC_COORDINATOR=SimpleNamespace(getByName=fresh))

    async def scenario():
        await StateStore._sync_do_rpc(backing, "acquire", {"owner": "owner"})
        if scope == "global":
            worker = Default()
            worker.env = env
            await worker._release_sync_lock("owner")
        else:
            assert await _release_control(StateStore(env), fresh(scope), "owner") is None
        status = await StateStore._sync_do_rpc(backing, "status")
        assert status is not None and not status["lock"].get("owner")

    asyncio.run(scenario())


def test_e2e_status_response_loss_recovers_without_releasing_twice():
    old, fresh = object(), object()
    calls = []

    async def rpc(stub, action, payload=None):
        calls.append((stub, action))
        if action == "release":
            return {"ok": True}
        if stub is old:
            raise RuntimeError("lost status response")
        return {"ok": True, "lock": {}}

    store = SimpleNamespace(_sync_do_rpc=rpc, env=SimpleNamespace(
        SYNC_COORDINATOR=SimpleNamespace(getByName=lambda name: fresh)))
    assert asyncio.run(_release_control(store, old, "owner")) is None
    assert calls == [(old, "release"), (old, "status"), (fresh, "status")]


def test_unreadable_exception_metadata_disables_retry():
    class BrokenException(Exception):
        @property
        def js_error(self):
            raise RuntimeError("private-proxy-failure")

    assert recovery._release_retry_blocked(BrokenException()) is True


@pytest.mark.parametrize("owner,blocked", [("", False), (None, False), ("owner", True)])
def test_recovery_never_calls_do_without_owner_or_when_blocked(owner, blocked):
    def unexpected():
        pytest.fail("再試行できないときは接続しない")

    async def rpc(*args):
        pytest.fail("ownerなしで解放しない")

    result = asyncio.run(recovery._recover_release(unexpected, rpc, owner, "global", blocked=blocked))
    assert result["ok"] is False and result["attempts"] == result["release_attempts"] == 0
