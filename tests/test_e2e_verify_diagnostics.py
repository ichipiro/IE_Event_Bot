"""E2E検証待ちの理由だけを記録し、所有状態や秘密値をログへ出さない。"""

import asyncio
import json
from copy import deepcopy

import pytest

from e2e import e2e_watch_shared_probe as watch
from e2e.e2e_google_sync_state import GoogleKV, GoogleStateError
from tests.test_e2e_all_sync import AllScenario
from tests.test_e2e_initial_cursor import window_scenario


def test_kv_digest_pending_logs_only_fixed_classification(monkeypatch, capsys):
    test = AllScenario(monkeypatch)
    assert test.call("all")[0] == 200
    owner = test.owner()
    before = deepcopy(test.env.STATE_KV.data)
    original = test.env.STATE_KV.get

    async def stale(key):
        if key.endswith("sync:updated_min"):
            return "private-stale-cursor-value"
        return await original(key)

    monkeypatch.setattr(test.env.STATE_KV, "get", stale)
    capsys.readouterr()

    with pytest.raises(GoogleStateError, match="^google_sync_not_ready$"):
        asyncio.run(GoogleKV(test.store, owner).get("sync:updated_min"))

    assert json.loads(capsys.readouterr().out) == {
        "event": "e2e_google_sync_verify", "reason": "kv_digest_pending",
    }
    assert test.env.STATE_KV.data == before and test.owner() == owner


def test_missing_watch_sync_logs_only_fixed_classification(monkeypatch, capsys):
    test, _, _ = window_scenario(monkeypatch, "watch")
    assert test.call("watch")[0] == 200
    owner = test.owner()
    before = deepcopy(test.env.STATE_KV.data)

    async def missing_observation(*args, **kwargs):
        return {"ok": True, "observations": {}}

    monkeypatch.setattr(watch, "rpc", missing_observation)
    capsys.readouterr()

    with pytest.raises(GoogleStateError, match="^google_sync_not_ready$"):
        asyncio.run(watch.verify_watch(test.env, test.store, owner))

    assert json.loads(capsys.readouterr().out) == {
        "event": "e2e_google_sync_verify", "reason": "watch_sync_pending",
    }
    assert test.env.STATE_KV.data == before and test.owner() == owner


def test_successful_kv_read_does_not_log_pending_classification(monkeypatch, capsys):
    test = AllScenario(monkeypatch)
    assert test.call("all")[0] == 200
    owner = test.owner()
    capsys.readouterr()

    assert asyncio.run(GoogleKV(test.store, owner).get("sync:updated_min")) == owner["initial_cursor"]

    assert capsys.readouterr().out == ""
