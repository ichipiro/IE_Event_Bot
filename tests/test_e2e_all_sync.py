"""通常dispatchと両方向の実装を代替HTTP・実DOロジックへ接続する。"""

import asyncio
import json
from copy import deepcopy
from urllib.parse import urlparse

import pytest
from workers import Response

import discord_notion_sync as discord_sync
import e2e_google_sync_probe as google
from e2e_google_sync_state import ALL_KEYS, ALL_STEPS, GoogleKV
from tests.test_e2e_google_sync_probe import Scenario
from tests.test_e2e_sync_lock_probe import RUN, OTHER


class AllScenario(Scenario):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.env.SYNC_INTERVAL_SECONDS = "0"  # 専用Wranglerの設定と同じ。
        api = google.discord_request.__globals__["fetch"]

        async def fetch(url, options=None):
            options = options or {}
            if "googleapis" not in url:
                return await api(url, options)
            method = options.get("method", "GET")
            payload = json.loads(options.get("body") or "{}")
            event_id = urlparse(url).path.rsplit("/", 1)[-1]
            assert method == "PATCH"  # 往復でGoogle予定を新規作成しない。
            assert event_id in self.google
            payload["extendedProperties"]["private"] = {
                **self.google[event_id].get("extendedProperties", {}).get("private", {}),
                **payload["extendedProperties"]["private"],
            }
            status, result = await google._google_request(method, url, "test-google-token", payload)
            return Response(json.dumps(result), status=status)

        async def token(*args):
            return "test-google-token"

        monkeypatch.setattr(discord_sync, "fetch", fetch)
        monkeypatch.setattr(discord_sync, "get_google_access_token", token)

    def reach(self, step):
        status, payload = self.call("all")
        assert status == 200, payload
        for current in range(step + 1):
            if current:
                status, payload = self.call("advance")
                assert status == 200, (current, payload)
            status, payload = self.call("verify")
            assert status == 200, (current, payload)
            assert payload["status"] == ALL_STEPS[current]


def test_full_roundtrip_failure_recovery_controls_and_cleanup(monkeypatch):
    test = AllScenario(monkeypatch)
    before = dict(test.env.STATE_KV.data)
    test.reach(8)
    owner = test.owner()
    assert len(test.google) == len(test.pages) == len(test.discord) == 2
    assert owner["stages"]["all_sync_failure_4"] == 200
    assert owner["stages"]["all_sync_failure_6"] == 200
    assert owner["stages"]["all_sync_cooldown"] == 200
    assert owner["stages"]["all_sync_contention"] == 200
    assert len(owner["hashes"]) == len(ALL_KEYS)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"
    assert test.env.STATE_KV.data == before
    assert not test.discord and all(p.get("archived") for p in test.pages.values())
    assert test.call("cleanup")[0] == 200


@pytest.mark.parametrize("step", range(9))
def test_cleanup_at_each_checkpoint(monkeypatch, step):
    test = AllScenario(monkeypatch)
    before = dict(test.env.STATE_KV.data)
    test.reach(step)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == ("passed" if step == 8 else "failed_clean")
    assert test.env.STATE_KV.data == before
    assert not test.discord


@pytest.mark.parametrize("field,value", [("all_sync", False), ("run_id", OTHER), ("scope_id", "b" * 32)])
def test_manifest_cannot_replace_mode_or_owner(monkeypatch, field, value):
    test = AllScenario(monkeypatch)
    test.reach(0)
    owner = test.owner()
    owner[field] = value
    with pytest.raises(RuntimeError, match="write_failed"):
        asyncio.run(test.store.put_e2e_manifest("google_sync", owner))
    assert test.call("cleanup")[0] == 200


def test_stale_kv_verify_revokes_pass_and_preserves_recovery(monkeypatch):
    test = AllScenario(monkeypatch)
    test.reach(8)
    owner = test.owner()
    key = GoogleKV(test.store, owner).prefix + "discord:snapshot"
    test.env.STATE_KV.data[key] = "{}"
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_foreign_discord_stops_before_reverse_write(monkeypatch):
    test = AllScenario(monkeypatch)
    test.reach(0)
    test.discord["foreign"] = {"id": "foreign", "name": "do not modify"}
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "all_sync_discord_input_mismatch"
    assert test.call("cleanup")[0] == 200
    assert test.discord == {"foreign": {"id": "foreign", "name": "do not modify"}}


def test_wrong_run_cleanup_does_not_touch_resources(monkeypatch):
    test = AllScenario(monkeypatch)
    test.reach(1)
    before = deepcopy((test.google, test.pages, test.discord, test.env.STATE_KV.data))
    assert test.call("cleanup", run_id=OTHER)[0] == 409
    assert (test.google, test.pages, test.discord, test.env.STATE_KV.data) == before
    assert test.call("cleanup", run_id=RUN)[0] == 200


def test_other_scenario_cannot_start_during_all_sync(monkeypatch):
    from tests.test_e2e_sync_lock_probe import request

    test = AllScenario(monkeypatch)
    test.reach(0)
    before = deepcopy(test.google)
    status, payload = asyncio.run(request(test.env))
    assert status == 409 and payload["error"] == "google_sync_shared_busy"
    assert test.google == before
    assert test.call("cleanup")[0] == 200


def test_cleanup_failure_keeps_ownership_and_can_retry(monkeypatch):
    test = AllScenario(monkeypatch)
    test.reach(7)
    test.env.STATE_KV.fail_delete = True
    assert test.call("cleanup")[0] == 409
    assert test.owner()["dirty"] is True
    test.env.STATE_KV.fail_delete = False
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_mode_start_refuses_other_active_manifest_before_creating(monkeypatch):
    from tests.test_e2e_sync_lock_probe import request

    test = AllScenario(monkeypatch)
    assert asyncio.run(request(test.env))[0] == 200
    assert test.call("all")[0] == 409
    assert not test.google and not test.pages and not test.discord
    assert asyncio.run(request(test.env, "/cleanup"))[0] == 200


def test_private_error_is_not_returned_and_partial_resources_are_recovered(monkeypatch):
    test = AllScenario(monkeypatch)
    test.reach(0)

    async def failed(*args, **kwargs):
        raise RuntimeError("private request URL and token")

    monkeypatch.setattr(discord_sync, "run_discord_notion_poll_sync", failed)
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "google_sync_failed"
    assert "private" not in json.dumps(payload)
    assert test.call("cleanup")[0] == 200
    assert not test.discord and all(p.get("archived") for p in test.pages.values())
