"""中断した全体同期の回収は期限切れロックだけを引き継ぐ。"""

import asyncio
import json
from copy import deepcopy

import pytest

import sync_lock_do
from e2e.e2e_google_sync_state import GoogleStateError
from e2e.e2e_watch_shared_probe import reclaim_expired_lock
from tests.test_e2e_all_sync import AllScenario
from tests.test_e2e_sync_lock_probe import OTHER


def interrupted_sync(monkeypatch):
    test = AllScenario(monkeypatch)
    before = deepcopy(test.env.STATE_KV.data)
    test.reach(2)
    owner = test.owner()
    owner["step"], owner["stage"] = 3, "working"
    asyncio.run(test.store.put_e2e_manifest("google_sync", owner))
    return test, before


def expired_global(test, monkeypatch):
    stub = test.store._sync_do_stub(test.env.SYNC_COORDINATOR)
    assert stub is not None
    with monkeypatch.context() as clock:
        clock.setattr(sync_lock_do.time, "time", lambda: 100.0)
        result = asyncio.run(test.store._sync_do_rpc(
            stub, "acquire", {"owner": "interrupted-sync", "ttl_seconds": 10},
        ))
        assert result is not None and result["ok"]
    return stub


def actions(stub, offset):
    return [json.loads(call["payload_json"]) for call in stub.calls[offset:] if call.get("rpc") == "sync_state"]


def test_all_sync_cleanup_claims_expired_global_lock_and_releases_only_its_owner(monkeypatch):
    test, before = interrupted_sync(monkeypatch)
    stub = expired_global(test, monkeypatch)
    offset = len(stub.calls)

    status, payload = test.call("cleanup")

    assert status == 200, payload
    assert test.owner()["outcome"] == "failed_clean"
    assert test.env.STATE_KV.data == before
    assert not test.discord and all(page.get("archived") for page in test.pages.values())
    acquired = [call for call in actions(stub, offset) if call["action"] == "acquire"]
    released = [call for call in actions(stub, offset) if call["action"] == "release"]
    assert len(acquired) == len(released) == 1
    assert acquired[0]["owner"].startswith("e2e-google-sync-recovery:")
    assert released[0]["owner"] == acquired[0]["owner"]


def test_all_sync_cleanup_does_not_release_live_foreign_global_lock(monkeypatch):
    test, _ = interrupted_sync(monkeypatch)
    stub = test.store._sync_do_stub(test.env.SYNC_COORDINATOR)
    assert stub is not None
    acquired = asyncio.run(test.store._sync_do_rpc(
        stub, "acquire", {"owner": "live-foreign-sync", "ttl_seconds": 120},
    ))
    assert acquired is not None and acquired["ok"]
    before = deepcopy((test.google, test.pages, test.discord, test.env.STATE_KV.data))
    offset = len(stub.calls)

    assert test.call("cleanup")[0] == 409

    assert (test.google, test.pages, test.discord, test.env.STATE_KV.data) == before
    assert not [call for call in actions(stub, offset) if call["action"] == "release"]
    status = asyncio.run(test.store._sync_do_rpc(stub, "status"))
    assert status is not None and status["lock"]["owner"] == "live-foreign-sync"


@pytest.mark.parametrize("invalid", ["run", "target", "clean", "not_all_sync"])
def test_reclaim_requires_dirty_all_sync_owner_with_matching_run_and_targets(monkeypatch, invalid):
    test, _ = interrupted_sync(monkeypatch)
    stub = expired_global(test, monkeypatch)
    owner = test.owner()
    run_id = owner["run_id"]
    if invalid == "run":
        run_id = OTHER
    elif invalid == "target":
        test.env.GOOGLE_CALENDAR_ID = "foreign-calendar"
    elif invalid == "clean":
        owner["dirty"] = False
    else:
        owner["all_sync"] = False
    offset = len(stub.calls)

    with pytest.raises(GoogleStateError, match="cleanup_owner"):
        asyncio.run(reclaim_expired_lock(test.env, test.store, owner, run_id))

    assert not [call for call in actions(stub, offset) if call["action"] in ("acquire", "release")]
