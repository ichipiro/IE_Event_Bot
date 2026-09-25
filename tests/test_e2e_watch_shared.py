"""通常watch APIと実通知入口の接続を代替外部APIで検証する。"""

import asyncio
import json
import time
from copy import deepcopy

import pytest
from workers import Response

import google_watch
import entry
import e2e_watch_shared_probe as probe
from tests.fakes import Request
from tests.test_e2e_all_sync import AllScenario


class WatchScenario(AllScenario):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.env.E2E_WATCH_SHARED_ENABLED = "true"
        self.env.E2E_ALL_HTTP_ENABLED = "true"
        self.env.E2E_GOOGLE_WEBHOOK_CHANGE_ENABLED = "true"
        self.env.GCAL_WEBHOOK_TOKEN = "test-webhook-token"
        self.env.GCAL_WEBHOOK_URL = "https://e2e.test/gcal/webhook"
        self.env.KV_GCAL_DEDUPE_ENABLED = "true"
        self.channels = {}
        self.stops = []

        async def token(*args):
            return "test-google-token"

        async def sleep(*args):
            return

        async def watch_api(url, options):
            body = json.loads(options["body"])
            if url.endswith("/channels/stop"):
                assert self.channels[body["id"]]["resourceId"] == body["resourceId"]
                self.channels[body["id"]]["stopped"] = True
                self.stops.append(body["id"])
                return Response("", status=204)
            assert url.endswith("/events/watch")
            data = {"id": body["id"], "resourceId": "resource-" + body["id"],
                    "expiration": str(int((time.time() + 604800) * 1000))}
            self.channels[body["id"]] = {**data, "stopped": False}
            # watch APIの応答より先にsync callbackを届ける。
            response = await self.worker.fetch(self.request(body["id"], "1", "sync", body["token"]))
            assert response.status == (401 if body["token"] != self.env.GCAL_WEBHOOK_TOKEN else 204)
            return Response(json.dumps(data))

        original_delta = entry.run_google_delta_fetch

        async def delta(env, *args, **kwargs):
            if getattr(env, "GOOGLE_API_BEARER_TOKEN", "") == "e2e-invalid-bearer":
                return {"ok": False, "error": "google_http_401", "items": []}
            return await original_delta(env, *args, **kwargs)

        monkeypatch.setattr(entry, "run_google_delta_fetch", delta)
        monkeypatch.setattr(google_watch, "fetch", watch_api)
        monkeypatch.setattr(google_watch, "get_google_access_token", token)
        monkeypatch.setattr(probe.asyncio, "sleep", sleep)

    def request(self, cid, msg="2", kind="exists", token=None):
        return Request(self.env.GCAL_WEBHOOK_URL, method="POST", headers={
            "X-Goog-Channel-Token": token or self.env.GCAL_WEBHOOK_TOKEN,
            "X-Goog-Channel-ID": cid, "X-Goog-Resource-ID": "resource-" + cid,
            "X-Goog-Message-Number": msg, "X-Goog-Resource-State": kind,
        })

    def prepare(self):
        status, payload = self.call("watch")
        assert status == 200, payload
        status, payload = self.call("verify")
        assert status == 200, payload

    def deliver(self, step):
        status, payload = self.call("watch/trigger")
        assert status == 200, payload
        cid = self.owner()["watches"][-1]["channel_id"]
        response = asyncio.run(self.worker.fetch(self.request(cid, str(step + 1))))
        assert response.status == 204, self.owner()
        self.drain_alarm()
        status, payload = self.call("verify")
        assert status == 200, payload

    def drain_alarm(self):
        from google_webhook_queue import queue_name
        stub = self.env.SYNC_COORDINATOR.getByName(queue_name(self.owner()["run_id"]))
        stub.durable_object.env = self.env
        asyncio.run(stub.durable_object.alarm())


def test_watch_maintenance_real_ingress_shared_sync_and_cleanup(monkeypatch):
    test = WatchScenario(monkeypatch)
    before = deepcopy(test.env.STATE_KV.data)
    test.prepare()
    from e2e_lock_release_probe import CASES, probe_name
    from state import StateStore
    assert all(test.owner()["stages"]["watch_shared_release_" + case] == 200 for case in CASES)
    probe_stub = test.env.SYNC_COORDINATOR.getByName(probe_name(test.owner()["run_id"]))
    assert not (asyncio.run(StateStore._sync_do_rpc(probe_stub, "status")) or {})["lock"].get("owner")
    assert len(test.channels) == 6 and len(test.stops) == 5
    for step in range(1, 4):
        test.deliver(step)
        owner = test.owner()
        assert owner["stages"][f"watch_shared_callback_{step}"] == 204
        assert owner["stages"][f"watch_shared_duplicate_{step}"] == 204
        assert "map:gcal_notion" in test.env.STATE_KV.data
        assert not owner["webhook_armed"]
    status, payload = test.call("cleanup")
    assert status == 200, payload
    assert test.owner()["outcome"] == "passed"
    assert test.owner()["stages"]["watch_shared_busy_retry_recovered"] == 200
    assert test.owner()["stages"]["watch_shared_failure_retry_recovered"] == 200
    assert len(test.stops) == 6
    assert test.env.STATE_KV.data == before
    assert not test.discord
    assert test.owner()["stages"]["watch_shared_cleanup"] == 200
    assert test.owner()["stages"]["watch_shared_release_recovery_cleanup"] == 200


def test_reject_old_watch_wrong_token_foreign_channel_and_manual_advance(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.prepare()
    before = deepcopy(test.env.STATE_KV.data)
    assert asyncio.run(test.worker.fetch(test.request(test.owner()["watches"][0]["channel_id"]))).status == 404
    assert asyncio.run(test.worker.fetch(test.request("foreign"))).status == 404
    assert asyncio.run(test.worker.fetch(test.request(test.owner()["watches"][-1]["channel_id"], token="wrong"))).status == 401
    assert test.call("advance")[0] == 409
    assert test.env.STATE_KV.data == before
    assert test.call("cleanup")[0] == 200


@pytest.mark.parametrize("step", range(4))
def test_cleanup_checkpoints_and_foreign_watch_preservation(monkeypatch, step):
    test = WatchScenario(monkeypatch)
    test.prepare()
    for i in range(1, step + 1):
        test.deliver(i)
    before = test.env.STATE_KV.data["gcal_watch_state"]
    test.env.STATE_KV.data["gcal_watch_state"] = '{"channel_id":"foreign"}'
    assert test.call("cleanup")[0] == 409
    assert test.env.STATE_KV.data["gcal_watch_state"] == '{"channel_id":"foreign"}'
    test.env.STATE_KV.data["gcal_watch_state"] = before
    assert test.call("cleanup")[0] == 200


def test_mode_and_channel_ownership_cannot_change(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.prepare()
    for field, value in (("webhook_sync", False), ("watch_url_sha256", "foreign"), ("watches", [])):
        owner = test.owner()
        owner[field] = value
        with pytest.raises(RuntimeError, match="write_failed"):
            asyncio.run(test.store.put_e2e_manifest("google_sync", owner))
    assert test.call("cleanup")[0] == 200


def test_watch_response_loss_recovers_using_early_sync_resource_id(monkeypatch):
    test = WatchScenario(monkeypatch)
    original = google_watch.fetch

    async def lost_response(url, options):
        response = await original(url, options)
        if url.endswith("/events/watch"):
            raise RuntimeError("response lost")
        return response

    monkeypatch.setattr(google_watch, "fetch", lost_response)
    status, payload = test.call("watch")
    assert status == 409, payload
    assert test.owner()["watches"][0]["resource_id"] == ""
    status, payload = test.call("cleanup")
    assert status == 200, payload
    assert len(test.stops) == 1
    assert test.owner()["outcome"] == "failed_clean"


def test_existing_watch_is_rejected_before_fixture_creation(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.env.STATE_KV.data["gcal_watch_state"] = "foreign"
    assert test.call("watch")[0] == 409
    assert not test.google and not test.channels
    assert test.env.STATE_KV.data["gcal_watch_state"] == "foreign"


def test_callback_is_persisted_before_response_and_completed_by_alarm(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.prepare()
    assert test.call("watch/trigger")[0] == 200
    owner = test.owner()
    response = asyncio.run(test.worker.fetch(test.request(owner["watches"][-1]["channel_id"], "77")))
    assert response.status == 204
    assert "watch_shared_callback_1" not in test.owner()["stages"]
    test.drain_alarm()
    assert test.owner()["stages"]["watch_shared_callback_1"] == 204
    assert test.call("cleanup")[0] == 200


def test_cleanup_reclaims_expired_global_lock_without_forced_release(monkeypatch):
    import sync_lock_do

    test = WatchScenario(monkeypatch)
    test.prepare()
    with monkeypatch.context() as old_clock:
        old_clock.setattr(sync_lock_do.time, "time", lambda: 100.0)
        acquired = asyncio.run(test.store._sync_do_rpc(
            test.store._sync_do_stub(test.env.SYNC_COORDINATOR), "acquire",
            {"owner": "interrupted-watch", "ttl_seconds": 10},
        ))
        assert acquired is not None and acquired["ok"]
    status, payload = test.call("cleanup")
    assert status == 200, payload
    assert test.owner()["outcome"] == "failed_clean"


def test_cleanup_does_not_release_live_global_lock(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.prepare()
    stub = test.store._sync_do_stub(test.env.SYNC_COORDINATOR)
    acquired = asyncio.run(test.store._sync_do_rpc(stub, "acquire", {"owner": "live", "ttl_seconds": 120}))
    assert acquired is not None and acquired["ok"]
    assert test.call("cleanup")[0] == 409
    status = asyncio.run(test.store._sync_do_rpc(stub, "status"))
    assert status is not None and status["lock"]["owner"] == "live"
    assert len(test.stops) == 5


def test_old_notifications_use_normal_handler_and_owned_dedupe_cleanup(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.prepare()
    test.deliver(1)
    stages = test.owner()["stages"]
    assert stages["watch_shared_old_token_rejected"] == 401
    assert stages["watch_shared_old_channel_guard"] == 404
    assert stages["watch_shared_old_channel_accepted"] == 204
    assert stages["watch_shared_old_channel_duplicate"] == 204
    old_channel = test.owner()["watches"][0]["channel_id"]
    storage = test.env.SYNC_COORDINATOR.getByName("global").durable_object.ctx.storage
    key = f"gcal_msg:{old_channel}:900000003"
    marker = json.loads(asyncio.run(storage.get(key)))
    assert marker["owner_run_id"] == test.owner()["run_id"]
    assert test.call("cleanup")[0] == 200
    assert asyncio.run(storage.get(key)) is None


def test_old_token_acceptance_fails_probe_and_remains_recoverable(monkeypatch):
    test = WatchScenario(monkeypatch)
    test.prepare()
    original = entry.Default._handle_gcal_webhook

    async def accept_bad_token(self, request, state, **kwargs):
        if request.headers.get("X-Goog-Channel-Token") != test.env.GCAL_WEBHOOK_TOKEN:
            return Response("", status=204)
        return await original(self, request, state, **kwargs)

    monkeypatch.setattr(entry.Default, "_handle_gcal_webhook", accept_bad_token)
    status, payload = test.call("watch/trigger")
    assert status == 409 and payload["error"] == "watch_shared_old_token_not_rejected"
    assert test.owner()["dirty"] is True
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"
