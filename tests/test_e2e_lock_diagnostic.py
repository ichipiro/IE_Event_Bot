"""解放失敗を固定分類し、限定再試行後も残留するロックを区別する。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import e2e_google_sync_probe as probe


@pytest.mark.parametrize("message, cause", [
    ("Network connection lost.", "disconnected"),
    ("Response closed due to connection limit", "connection_limit"),
    ("The Durable Object was reset because its code was updated.", "object_reset"),
    ("Too many subrequests", "subrequest_limit"),
    ("This borrowed proxy was automatically destroyed", "python_proxy"),
    ("Cannot perform I/O on behalf of a different request", "io_context"),
    ("unknown private error", "unknown"),
])
def test_release_error_keeps_fixed_cause_after_recovery_attempts(message, cause):
    old, fresh = object(), object()
    calls = []

    async def rpc(stub, action, payload=None):
        calls.append((stub, action))
        if stub is old:
            raise type("JsException", (Exception,), {})(message + " private-secret")
        return {"ok": True, "lock": {"owner": "private-owner"}}

    def get_stub(name):
        assert name == "e2e:google-sync-control"
        return fresh

    store = SimpleNamespace(_sync_do_rpc=rpc, env=SimpleNamespace(
        SYNC_COORDINATOR=SimpleNamespace(getByName=get_stub)))
    result = asyncio.run(probe._release_control(store, old, "private-owner"))
    assert result == {
        "step": "release_rpc", "exception": "js_exception", "cause": cause,
        "release_ok": None, "status_ok": None, "owner_matches": None,
        "fresh_status_ok": True, "fresh_owner_matches": True,
    }
    assert calls == [(old, "release"), (fresh, "status"), (fresh, "release"),
                     (fresh, "status"), (fresh, "release"), (fresh, "status")]
    assert "private" not in json.dumps(result)


def test_fresh_diagnostic_failure_keeps_original_cause():
    async def rpc(*args):
        raise RuntimeError("Network connection lost. private-secret")

    store = SimpleNamespace(_sync_do_rpc=rpc, env=SimpleNamespace(
        SYNC_COORDINATOR=SimpleNamespace(getByName=lambda name: object())))
    result = asyncio.run(probe._release_control(store, object(), "private-owner"))
    assert result["cause"] == "disconnected"
    assert result["fresh_status_ok"] is False
    assert result["fresh_owner_matches"] is None
    assert "private" not in json.dumps(result)
