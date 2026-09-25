"""解放失敗の可視化と、同期結果・機密情報の保護を確認する。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from entry import Default
from state import StateStore
from tests.test_discord_sync_lock import invoke, make_worker


@pytest.mark.parametrize("stage,error_type", [
    ("get_stub", "RuntimeError"),
    ("rpc_call", "TimeoutError"),
    ("rpc_call", "JsException"),
    ("rpc_call", "private_exception_name"),
    ("decode_rpc", "JSONDecodeError"),
    ("decode_rpc", "TypeError"),
    ("release_response", "none"),
])
def test_release_failure_emits_one_safe_event(capsys, stage, error_type):
    calls = []

    async def rpc(payload):
        calls.append(json.loads(payload))
        if stage == "rpc_call":
            raise type(error_type, (Exception,), {})("private_exception_body")
        if stage == "decode_rpc":
            return "private_response" if error_type == "JSONDecodeError" else "[]"
        return json.dumps({"ok": False, "error": "private_response"})

    def get_stub(name):
        if stage == "get_stub":
            raise RuntimeError("private_stub_details")
        return SimpleNamespace(sync_state=rpc)

    worker = Default()
    worker.env = SimpleNamespace(SYNC_COORDINATOR=SimpleNamespace(getByName=get_stub))
    assert asyncio.run(worker._release_sync_lock("private_owner")) is None
    output = capsys.readouterr()
    events = [json.loads(line) for line in output.out.splitlines()]
    assert events[0] == {
        "event": "sync_lock_release_failed", "level": "error", "stage": stage,
        "error_type": "other" if error_type == "private_exception_name" else error_type,
    }
    assert len(events) == 2
    assert events[1]["event"] == "sync_lock_release_recovery_failed"
    assert "private" not in output.out + output.err
    assert len(calls) == (0 if stage == "get_stub" else 4)
    assert sum(call["action"] == "release" for call in calls) == (0 if stage == "get_stub" else 1)


@pytest.mark.parametrize("mode", ["success", "no_binding", "no_owner"])
def test_release_without_failure_is_silent(capsys, mode):
    worker = make_worker()
    owner = asyncio.run(worker._acquire_sync_lock("test"))["owner"]
    if mode == "no_binding":
        del worker.env.SYNC_COORDINATOR
    if mode == "no_owner":
        owner = ""
    asyncio.run(worker._release_sync_lock(owner))
    assert capsys.readouterr().out == ""
    if mode == "success":
        assert asyncio.run(worker._acquire_sync_lock("next"))["ok"] is True


@pytest.mark.parametrize("source", ["manual", "cron", "all"])
@pytest.mark.parametrize("body_fails", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
def test_release_failure_preserves_sync_outcome(monkeypatch, capsys, source, body_fails, persistent):
    worker = make_worker()
    original_rpc = worker._do_stub_rpc
    releases = []

    async def rpc(stub, payload):
        if payload["action"] == "release":
            releases.append(payload)
            if persistent or len(releases) == 1:
                raise RuntimeError("private_release_details")
        return await original_rpc(stub, payload)

    async def body(*args, **kwargs):
        if body_fails:
            raise ValueError("original_sync_failure")
        return {"ok": True}

    monkeypatch.setattr(worker, "_do_stub_rpc", rpc)
    monkeypatch.setattr("entry.run_discord_notion_poll_sync", body)

    async def execute():
        if source == "all":
            response = await worker._run_sync_dispatch(
                None, StateStore(worker.env), "manual", google_fetcher=body, google_applier=body,
            )
            return json.loads(await response.text()), response.status
        return await invoke(worker, source)

    if body_fails:
        with pytest.raises(ValueError, match="^original_sync_failure$"):
            asyncio.run(execute())
    else:
        result, status = asyncio.run(execute())
        assert result["ok"] is True
        assert status == (None if source == "cron" else 200)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0] == {"event": "sync_lock_release_failed", "level": "error",
                         "stage": "rpc_call", "error_type": "RuntimeError"}
    assert len(events) == 2
    assert events[1]["event"] == ("sync_lock_release_recovery_failed" if persistent else "sync_lock_release_recovered")
    assert events[1]["release_attempts"] == (2 if persistent else 1)
    next_lock = asyncio.run(worker._acquire_sync_lock("next"))
    assert next_lock["locked" if persistent else "ok"] is True
