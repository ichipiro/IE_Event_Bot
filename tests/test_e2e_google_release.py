"""解放診断の境界と、機密の例外本文を出さないことを検証する。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from e2e_google_sync_probe import _release_control
from state import StateStore


@pytest.mark.parametrize(("released", "status", "step", "owner_matches"), [
    ({"ok": False}, {"ok": True, "lock": {}}, "release_response", False),
    ({"ok": True}, {"ok": False}, "status_response", None),
    ({"ok": True}, {"ok": True, "lock": []}, "lock_response", None),
    ({"ok": True}, {"ok": True, "lock": {"owner": "private-owner"}}, "owner_check", True),
    ({"ok": True}, {"ok": True, "lock": {}}, None, False),
    ({"ok": True}, {"ok": True, "lock": {"owner": "another-owner"}}, None, False),
])
def test_release_response_boundaries(released, status, step, owner_matches):
    actions = []

    async def sync_state(payload):
        action = json.loads(payload)["action"]
        actions.append(action)
        return json.dumps(released if action == "release" else status)

    result = asyncio.run(_release_control(StateStore, SimpleNamespace(sync_state=sync_state), "private-owner"))
    assert actions == ["release", "status"]
    if step is None:
        assert result is None
    else:
        assert result["step"] == step
        assert result["owner_matches"] is owner_matches
        assert result["exception"] == "none"
        assert "private-owner" not in json.dumps(result)


@pytest.mark.parametrize("action", ["release", "status"])
@pytest.mark.parametrize(("exception", "classification"), [
    (TimeoutError, "timeout"), (TypeError, "type_error"),
    (RuntimeError, "runtime_error"), (ValueError, "other"),
    (type("JsException", (Exception,), {}), "js_exception"),
])
def test_release_exception_redaction(action, exception, classification):
    actions = []

    async def sync_state(payload):
        current = json.loads(payload)["action"]
        actions.append(current)
        if current == action:
            raise exception("private-token private-owner private-url")
        return '{"ok": true}'

    result = asyncio.run(_release_control(StateStore, SimpleNamespace(sync_state=sync_state), "private-owner"))
    assert result == {
        "step": f"{action}_rpc", "exception": classification,
        "release_ok": True if action == "status" else None,
        "status_ok": None, "owner_matches": None,
        "cause": "unknown", "fresh_status_ok": False, "fresh_owner_matches": None,
    }
    assert actions == (["release", "status"] if action == "status" else ["release"])
    assert "private" not in json.dumps(result)
