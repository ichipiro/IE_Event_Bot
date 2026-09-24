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
        status, payload = self.call("verify")
        assert status == 200, payload


def test_watch_maintenance_real_ingress_shared_sync_and_cleanup(monkeypatch):
    test = WatchScenario(monkeypatch)
    before = deepcopy(test.env.STATE_KV.data)
    test.prepare()
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
    assert test.owner()["stages"]["watch_shared_busy_retry_lost"] == 409
    assert test.owner()["stages"]["watch_shared_failure_retry_lost"] == 409
    assert len(test.stops) == 6
    assert test.env.STATE_KV.data == before
    assert not test.discord
    assert test.owner()["stages"]["watch_shared_cleanup"] == 200


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
