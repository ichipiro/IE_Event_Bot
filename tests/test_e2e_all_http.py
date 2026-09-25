"""通常HTTP入口・共有KV・通常DO成功時刻による全体同期。"""

import asyncio
import json
from copy import deepcopy

import pytest

from tests.fakes import Request
from tests.test_e2e_all_sync import AllScenario
from tests.test_e2e_sync_lock_probe import RUN, OTHER


def normal(test, *, run=RUN, token="test-token", method="POST"):
    response = asyncio.run(test.worker.fetch(Request(
        "https://e2e.test/sync/all", method=method,
        headers={"Authorization": "Bearer " + token, "X-E2E-Run-ID": run,
                 "X-E2E-Version-Tag": run},
    )))
    text = asyncio.run(response.text())
    return response.status, json.loads(text) if text.startswith("{") else {"error": text}


def prepared(monkeypatch):
    test = AllScenario(monkeypatch)
    test.env.E2E_ALL_HTTP_ENABLED = "true"
    status, payload = test.call("http")
    assert status == 200, payload
    assert test.call("verify")[0] == 200
    return test


def test_normal_http_shared_state_roundtrip_and_cleanup(monkeypatch):
    test = prepared(monkeypatch)
    for step in range(1, 4):
        status, payload = normal(test)
        assert status == 200, payload
        status, payload = test.call("verify")
        assert status == 200, payload
        assert test.owner()["stages"][f"all_http_step_{step}"] == 200
        assert "discord:snapshot" in test.env.STATE_KV.data
        assert not any(k.startswith("e2e:google_sync:") for k in test.env.STATE_KV.data)
        assert "sync:last_epoch" not in test.env.STATE_KV.data
        assert asyncio.run(test.store.get_sync_last_epoch()) > 0
    assert len(test.google) == len(test.pages) == len(test.discord) == 2
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"
    assert test.owner()["stages"]["google_sync_shared_cleanup"] == 200
    assert test.env.STATE_KV.data == {"unrelated": "preserved", "result:sync_discord_notion": "previous"}
    assert not test.discord and all(p.get("archived") for p in test.pages.values())


@pytest.mark.parametrize("key", ["discord:snapshot", "sync:discord_notion_queue", "sync:updated_min"])
def test_prepare_refuses_existing_shared_values(monkeypatch, key):
    test = AllScenario(monkeypatch)
    test.env.E2E_ALL_HTTP_ENABLED = "true"
    test.env.STATE_KV.data[key] = "do not overwrite"
    assert test.call("http")[0] == 409
    assert not test.google and test.env.STATE_KV.data[key] == "do not overwrite"


def test_route_checks_auth_run_phase_and_flag(monkeypatch):
    test = prepared(monkeypatch)
    before = deepcopy((test.google, test.pages, test.env.STATE_KV.data))
    assert normal(test, token="wrong")[0] == 401
    assert normal(test, method="GET")[0] == 405
    assert normal(test, run=OTHER)[0] == 409
    assert test.call("advance")[0] == 409
    test.env.E2E_ALL_HTTP_ENABLED = "false"
    assert normal(test)[0] == 404
    assert (test.google, test.pages, test.env.STATE_KV.data) == before


def test_foreign_shared_write_is_preserved_on_cleanup(monkeypatch):
    test = prepared(monkeypatch)
    assert normal(test)[0] == 200
    test.env.STATE_KV.data["discord:snapshot"] = "foreign"
    assert test.call("cleanup")[0] == 409
    assert test.owner()["dirty"]
    assert test.env.STATE_KV.data["discord:snapshot"] == "foreign"


def test_foreign_external_input_stops_before_normal_dispatch(monkeypatch):
    test = prepared(monkeypatch)
    test.discord["foreign"] = {"id": "foreign", "name": "preserve"}
    assert normal(test)[0] == 409
    assert not test.pages
    assert test.discord == {"foreign": {"id": "foreign", "name": "preserve"}}


def test_uses_normal_fetch_state_and_default_runners(monkeypatch):
    from entry import Default

    test = prepared(monkeypatch)
    original_fetch, original_dispatch = Default.fetch, Default._run_sync_dispatch
    paths = []

    async def fetch(self, request):
        paths.append(request.url)
        return await original_fetch(self, request)

    async def dispatch(self, request, state, source, **kwargs):
        assert not kwargs
        assert request.url.endswith("/sync/all") and source == "manual"
        assert state.env.SYNC_COORDINATOR is test.env.SYNC_COORDINATOR
        assert state.env.STATE_KV.prefix == ""
        return await original_dispatch(self, request, state, source)

    monkeypatch.setattr(Default, "fetch", fetch)
    monkeypatch.setattr(Default, "_run_sync_dispatch", dispatch)
    assert normal(test)[0] == 200
    assert paths == ["https://e2e.test/sync/all"]


@pytest.mark.parametrize("step", range(4))
def test_shared_cleanup_at_each_checkpoint(monkeypatch, step):
    test = prepared(monkeypatch)
    for _ in range(step):
        assert normal(test)[0] == 200
        assert test.call("verify")[0] == 200
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == ("passed" if step == 3 else "failed_clean")
    assert not test.discord
    assert test.env.STATE_KV.data == {"unrelated": "preserved", "result:sync_discord_notion": "previous"}


def test_mode_is_immutable_and_epoch_tamper_revokes_success(monkeypatch):
    test = prepared(monkeypatch)
    changed = test.owner()
    changed["http_sync"] = False
    with pytest.raises(RuntimeError, match="write_failed"):
        asyncio.run(test.store.put_e2e_manifest("google_sync", changed))
    for _ in range(3):
        assert normal(test)[0] == 200
        assert test.call("verify")[0] == 200
    asyncio.run(test.store.set_sync_last_epoch_now())
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_normal_sync_preserves_mapping_from_known_discord_origin_tombstone(monkeypatch):
    test = AllScenario(monkeypatch)
    test.env.E2E_ALL_HTTP_ENABLED = "true"
    deleted = {"id": "known-deleted", "status": "cancelled", "updated": "2026-09-14T01:00:00Z",
               "extendedProperties": {"private": {"ie_origin": "discord", "ie_discord_event_id": "old-discord"}}}
    test.google[deleted["id"]] = deepcopy(deleted)
    assert test.call("http")[0] == 200
    assert test.call("verify")[0] == 200
    status, payload = normal(test)
    assert status == 200, payload
    assert test.call("verify")[0] == 200
    mapping = json.loads(test.env.STATE_KV.data["map:gcal_discord"])
    assert mapping["known-deleted"] == "old-discord" and len(mapping) == 3
    assert test.call("cleanup")[0] == 200
    assert test.google["known-deleted"] == deleted


def test_baseline_mapping_cannot_be_added_or_changed_after_prepare(monkeypatch):
    test = prepared(monkeypatch)
    owner = test.owner()
    owner['http_baseline_maps'] = {'a' * 64: 'b' * 64}
    with pytest.raises(RuntimeError, match='write_failed'):
        asyncio.run(test.store.put_e2e_manifest('google_sync', owner))


def test_unknown_baseline_mapping_is_rejected_without_kv_write(monkeypatch):
    from e2e_google_sync_state import GoogleKV, GoogleStateError

    test = prepared(monkeypatch)
    owner = test.owner()
    owner['step'], owner['stage'] = 1, 'working'
    asyncio.run(test.store.put_e2e_manifest('google_sync', owner))
    kv = GoogleKV(test.store, owner)
    with pytest.raises(GoogleStateError, match='state_invalid_discord_map'):
        asyncio.run(kv.put('map:gcal_discord', json.dumps({'foreign': 'foreign-discord'})))
    assert 'map:gcal_discord' not in test.env.STATE_KV.data
