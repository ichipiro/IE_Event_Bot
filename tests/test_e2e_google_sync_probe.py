"""通常Google同期E2Eを実DOロジック・代替外部APIで検証する。"""

import asyncio
import json
from copy import deepcopy
from urllib.parse import unquote, urlparse

import pytest
from workers import Response

import e2e_google_sync_probe as probe
import e2e_discord_probe
import e2e_notion_probe
import google_apply_sync
import google_calendar_sync
from e2e_entry import Default
from e2e_google_sync_state import SERVICE, KEYS, GoogleKV, GoogleStateError
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_sync_lock_probe import environment, RUN

DB = "11111111-1111-4111-8111-111111111111"


class Scenario:
    def __init__(self, monkeypatch):
        self.env = environment()
        self.env.SYNC_ALL_INCLUDE_DISCORD_NOTION = "false"
        self.env.GOOGLE_CALENDAR_ID = "test-calendar"
        self.env.NOTION_EVENT_INTERNAL_ID = DB
        self.env.NOTION_TOKEN = "test-notion-token"
        self.env.DISCORD_GUILD_ID = "test-guild"
        self.env.DISCORD_TOKEN = "test-discord-token"
        self.env.E2E_GOOGLE_SYNC_ENABLED = "true"
        self.worker = Default()
        self.worker.env = self.env
        self.google, self.pages, self.discord = {}, {}, {}
        self.calls = []
        self.fail_create = False
        self.fail_delete = False
        self.hide_lists = False
        self.store = StateStore(self.env)

        async def token(*args):
            return "test-google-token"

        async def valid(*args):
            return ""

        async def google(method, url, token, payload=None):
            self.calls.append(("google", method))
            path = unquote(urlparse(url).path)
            event_id = path.rsplit("/", 1)[-1]
            if method == "POST":
                assert isinstance(payload, dict)
                event = deepcopy(payload)
                event["updated"] = "2026-09-15T01:00:00Z"
                assert event["id"] not in self.google
                self.google[event["id"]] = event
                return 200, deepcopy(event)
            if event_id not in self.google:
                return 404, {}
            if method == "PATCH":
                self.google[event_id].update(payload)
                self.google[event_id]["updated"] = "2026-09-15T01:01:00Z"
            if method == "DELETE":
                self.google[event_id] = {
                    "id": event_id,
                    "status": "cancelled",
                    "updated": "2026-09-15T01:02:00Z",
                }
                return 204, {}
            return 200, deepcopy(self.google[event_id])

        async def calendar_fetch(url, options):
            self.calls.append(("google", "LIST"))
            return Response(
                json.dumps({"items": list(self.google.values())}), status=200
            )

        async def api(url, options=None):
            options = options or {}
            method = options.get("method", "GET")
            payload = json.loads(options.get("body") or "{}")
            parsed = urlparse(url)
            path = parsed.path
            if "discord" in parsed.netloc:
                self.calls.append(("discord", method))
                if method == "GET" and path.endswith("scheduled-events"):
                    return Response(
                        json.dumps(
                            [] if self.hide_lists else list(self.discord.values())
                        )
                    )
                if method == "POST":
                    event_id = f"discord-{len(self.discord)}"
                    self.discord[event_id] = {
                        "id": event_id,
                        "guild_id": self.env.DISCORD_GUILD_ID,
                        **payload,
                    }
                    return Response(json.dumps(self.discord[event_id]))
                event_id = path.rsplit("/", 1)[-1]
                if event_id not in self.discord:
                    return Response("{}", status=404)
                if method == "DELETE":
                    if self.fail_delete:
                        return Response("{}", status=403)
                    del self.discord[event_id]
                    return Response("", status=204)
                if method == "PATCH":
                    if payload.get("name") == "":
                        return Response('{"code":50035}', status=400)
                    self.discord[event_id].update(payload)
                return Response(json.dumps(self.discord[event_id]))
            self.calls.append(("notion", method))
            if path.endswith("/query"):
                filt = payload["filter"]
                matches = [
                    page
                    for page in self.pages.values()
                    if not page.get("archived")
                    and e2e_notion_probe._property_text(
                        page, filt["property"], "rich_text"
                    )
                    == filt["rich_text"].get(
                        "equals", filt["rich_text"].get("contains")
                    )
                ]
                return Response(
                    json.dumps(
                        {
                            "results": [] if self.hide_lists else matches,
                            "has_more": False,
                        }
                    )
                )
            if method == "POST":
                if self.fail_create:
                    return Response("{}", status=403)
                page_id = f"33333333-3333-4333-8333-{len(self.pages):012d}"
                self.pages[page_id] = {
                    "id": page_id,
                    "parent": {"database_id": DB},
                    **payload,
                }
                return Response(json.dumps(self.pages[page_id]))
            page_id = path.rsplit("/", 1)[-1]
            if page_id not in self.pages:
                return Response("{}", status=404)
            if method == "PATCH":
                self.pages[page_id]["properties"].update(payload.pop("properties", {}))
                self.pages[page_id].update(payload)
            return Response(json.dumps(self.pages[page_id]))

        monkeypatch.setattr(probe, "get_google_access_token", token)
        monkeypatch.setattr(google_calendar_sync, "get_google_access_token", token)
        monkeypatch.setattr(probe, "_google_request", google)
        for name in ("_verify_calendar", "_verify_guild", "_verify_database"):
            monkeypatch.setattr(probe, name, valid)
        monkeypatch.setattr(google_calendar_sync, "fetch", calendar_fetch)
        for module in (google_apply_sync, e2e_discord_probe, e2e_notion_probe):
            monkeypatch.setattr(module, "fetch", api)

    def call(self, phase, *, run_id=RUN, version=RUN, auth=True):
        suffix = "" if phase == "prepare" else "/" + phase
        headers = {"X-E2E-Run-ID": run_id, "X-E2E-Version-Tag": version}
        if auth:
            headers["Authorization"] = "Bearer test-token"
        response = asyncio.run(
            self.worker.fetch(
                Request(
                    "https://e2e.test/admin/e2e/google-sync" + suffix,
                    method="POST",
                    headers=headers,
                )
            )
        )
        body = asyncio.run(response.text())
        return response.status, json.loads(body) if body.startswith("{") else {
            "error": body
        }

    def owner(self):
        owner = asyncio.run(self.store.get_e2e_manifest(SERVICE))
        assert isinstance(owner, dict)
        return owner


def test_all_phases_and_cleanup_preserve_unrelated_state(monkeypatch):
    test = Scenario(monkeypatch)
    previous = dict(test.env.STATE_KV.data)
    status, payload = test.call("prepare")
    assert status == 200, payload
    assert payload["status"] == "pending"
    assert len(test.pages) == len(test.discord) == 1
    for phase, expected in (
        ("verify", "pending"),
        ("advance", "drained"),
        ("verify", "drained"),
        ("advance", "updated"),
        ("verify", "updated"),
        ("advance", "deleted"),
        ("verify", "deleted"),
        ("advance", "retry_pending"),
        ("verify", "retry_pending"),
        ("advance", "retried"),
        ("verify", "retried"),
    ):
        status, payload = test.call(phase)
        assert status == 200 and payload["status"] == expected, payload
    assert len(test.discord) == 1
    assert len([p for p in test.pages.values() if p.get("archived")]) == 1
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"
    assert not test.discord and all(p.get("archived") for p in test.pages.values())
    assert test.env.STATE_KV.data == previous
    assert test.call("cleanup")[0] == 200
    assert test.call("prepare")[0] == 409


@pytest.mark.parametrize("mode", ["auth", "version", "disabled", "run"])
def test_invalid_entry_never_writes(monkeypatch, mode):
    test = Scenario(monkeypatch)
    kwargs = {}
    if mode == "auth":
        kwargs["auth"] = False
    elif mode == "version":
        kwargs["version"] = "wrong"
    elif mode == "run":
        kwargs["run_id"] = "wrong"
    else:
        test.env.E2E_GOOGLE_SYNC_ENABLED = "false"
    assert test.call("prepare", **kwargs)[0] >= 400
    assert test.calls == []


def test_failed_apply_requires_cleanup_without_resend(monkeypatch):
    test = Scenario(monkeypatch)
    test.fail_create = True
    assert test.call("prepare")[0] == 409
    before = list(test.calls)
    assert test.call("advance")[0] == 409
    assert test.calls == before
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_stale_kv_blocks_advance_before_external_write(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("prepare")[0] == 200
    assert test.call("verify")[0] == 200
    key = GoogleKV(test.store, test.owner()).prefix + KEYS[3]
    original = test.env.STATE_KV.data[key]
    test.env.STATE_KV.data[key] = "[]"
    before = list(test.calls)
    status, payload = test.call("advance")
    assert status == 409 and payload["error"] == "google_sync_not_ready"
    assert test.calls == before
    test.env.STATE_KV.data[key] = original
    assert test.call("cleanup")[0] == 200


def test_cleanup_failure_retains_dirty_and_can_retry(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("prepare")[0] == 200
    test.fail_delete = True
    assert test.call("cleanup")[0] == 409
    assert test.owner()["dirty"]
    test.fail_delete = False
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_state_forbids_foreign_keys_and_clean_owner(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("prepare")[0] == 200
    kv = GoogleKV(test.store, test.owner())
    with pytest.raises(GoogleStateError):
        asyncio.run(kv.put("unrelated", "overwrite"))
    with pytest.raises(GoogleStateError):
        asyncio.run(kv.put(KEYS[3], '[{"id":"foreign"}]'))
    assert test.call("cleanup")[0] == 200
    with pytest.raises(GoogleStateError):
        asyncio.run(kv.put(KEYS[3], "[]"))


@pytest.mark.parametrize(
    "field", ["scope_id", "run_id", "target_fingerprints", "google_event_id"]
)
def test_do_rejects_owner_replacement(monkeypatch, field):
    test = Scenario(monkeypatch)
    assert test.call("prepare")[0] == 200
    owner = test.owner()
    original = deepcopy(owner)
    if field == "google_event_id":
        owner["fixtures"][0][field] = "foreign"
    elif field == "target_fingerprints":
        owner[field]["calendar_id_sha256"] = "a" * 64
    else:
        owner[field] = "0" * 32 if field == "scope_id" else RUN[:-8] + "87654321"
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest(SERVICE, owner))
    assert test.owner() == original
    assert test.call("cleanup")[0] == 200


def test_source_collision_is_preserved_by_cleanup(monkeypatch):
    from e2e_google_sync_state import source_id

    test = Scenario(monkeypatch)
    event_id = source_id(RUN, 0)
    original = {"id": event_id, "summary": "existing event"}
    test.google[event_id] = original
    assert test.call("prepare")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.google[event_id] == original


def test_missing_create_response_still_cleans_owned_source(monkeypatch):
    test = Scenario(monkeypatch)
    original = probe._google_request

    async def lost_response(method, *args):
        response = await original(method, *args)
        return (500, {}) if method == "POST" else response

    monkeypatch.setattr(probe, "_google_request", lost_response)
    assert test.call("prepare")[0] == 409
    assert len(test.google) == 1
    assert test.call("cleanup")[0] == 200
    assert all(event["status"] == "cancelled" for event in test.google.values())


def test_unowned_calendar_event_is_never_applied(monkeypatch):
    test = Scenario(monkeypatch)
    test.google["foreign"] = {
        "id": "foreign",
        "summary": "unrelated",
        "updated": "2026-09-15T01:05:00Z",
    }
    assert test.call("prepare")[0] == 200
    assert all("unrelated" not in json.dumps(page) for page in test.pages.values())
    assert test.call("cleanup")[0] == 200
    assert test.google["foreign"]["summary"] == "unrelated"


def test_failed_reverify_removes_passed_eligibility(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("prepare")[0] == 200
    for phase in (
        "verify",
        "advance",
        "verify",
        "advance",
        "verify",
        "advance",
        "verify",
    ):
        assert test.call(phase)[0] == 200
    prefix = GoogleKV(test.store, test.owner()).prefix
    test.env.STATE_KV.data[prefix + KEYS[3]] = "bad"
    assert test.call("verify")[0] == 409
    assert test.owner()["stage"] == "ready"
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_api_ids_allow_readback_when_lists_are_stale(monkeypatch):
    test = Scenario(monkeypatch)
    test.hide_lists = True
    status, payload = test.call("prepare")
    assert status == 200, payload
    assert test.owner()["fixtures"][0].get("notion_page_id")
    assert test.owner()["fixtures"][0].get("discord_event_id")
    assert test.call("verify")[0] == 200
    assert test.call("cleanup")[0] == 200
    assert not test.discord
    assert all(page.get("archived") for page in test.pages.values())


def test_cleanup_retries_after_external_deletes_and_kv_failure(monkeypatch):
    test = Scenario(monkeypatch)
    assert test.call("prepare")[0] == 200
    original = test.env.STATE_KV.delete

    async def fail(key):
        raise RuntimeError("injected_delete_failure")

    monkeypatch.setattr(test.env.STATE_KV, "delete", fail)
    assert test.call("cleanup")[0] == 409
    assert test.owner()["dirty"]
    monkeypatch.setattr(test.env.STATE_KV, "delete", original)
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


@pytest.mark.parametrize("missing", ["jsnull", "jsundefined"])
def test_worker_absent_values_are_not_hashed(monkeypatch, missing):
    test = Scenario(monkeypatch)
    original = test.env.STATE_KV.get

    class AbsentValue:
        def __str__(self):
            return missing

    async def get(key):
        value = await original(key)
        return AbsentValue() if value is None else value

    monkeypatch.setattr(test.env.STATE_KV, "get", get)
    status, payload = test.call("prepare")
    assert status == 200, payload
    assert test.call("verify")[0] == 200
    assert test.call("cleanup")[0] == 200


def advance_to(test, step):
    assert test.call("prepare")[0] == 200
    assert test.call("verify")[0] == 200
    for _ in range(step):
        status, payload = test.call("advance")
        assert status == 200, payload
        status, payload = test.call("verify")
        assert status == 200, payload


def test_partial_failure_then_queue_only_retry_reuses_owned_ids(monkeypatch):
    test = Scenario(monkeypatch)
    advance_to(test, 3)
    before = test.owner()
    ids = [
        (s.get("notion_page_id"), s.get("discord_event_id")) for s in before["fixtures"]
    ]
    status, payload = test.call("advance")
    assert status == 200 and payload["status"] == "retry_pending"
    owner = test.owner()
    assert owner["hashes"][KEYS[0]] == before["hashes"][KEYS[0]]
    assert owner["hashes"][KEYS[4]] == before["hashes"][KEYS[4]]
    assert owner["stages"]["google_sync_dispatch"] == 500
    assert owner["stages"]["google_sync_discord_failure_injected"] == 200
    assert owner["stages"]["google_sync_discord_invalid_update"] == 400
    assert owner["stages"]["google_sync_discord_rejection_verified"] == 200
    assert test.call("advance")[0] == 409  # 読戻し前に再試行しない。
    assert test.call("verify")[0] == 200
    assert test.call("advance")[0] == 200
    assert test.call("verify")[0] == 200
    assert ids == [
        (s.get("notion_page_id"), s.get("discord_event_id"))
        for s in test.owner()["fixtures"]
    ]
    assert len(test.pages) == 2 and len(test.discord) == 1
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "passed"


def test_missing_failure_injection_cannot_pass(monkeypatch):
    test = Scenario(monkeypatch)
    advance_to(test, 3)
    original = google_apply_sync.apply_google_events

    async def skip_injection(env, state, events, **kwargs):
        return await original(env, state, events)

    monkeypatch.setattr(google_apply_sync, "apply_google_events", skip_injection)
    assert test.call("advance")[0] == 409
    assert test.call("advance")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_retry_failure_is_not_accepted_as_injected_failure(monkeypatch):
    test = Scenario(monkeypatch)
    advance_to(test, 4)

    async def failed(*args, **kwargs):
        return None

    monkeypatch.setattr(google_apply_sync, "_discord_update_event", failed)
    assert test.call("advance")[0] == 409
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_do_rejects_removing_retry_requirement(monkeypatch):
    test = Scenario(monkeypatch)
    advance_to(test, 3)
    owner = test.owner()
    owner["retry_enabled"] = False
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest(SERVICE, owner))
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_failed_reverify_after_retry_revokes_success(monkeypatch):
    test = Scenario(monkeypatch)
    advance_to(test, 5)
    prefix = GoogleKV(test.store, test.owner()).prefix
    test.env.STATE_KV.data[prefix + KEYS[5]] = "{}"
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


@pytest.mark.parametrize("status", [200, 400, 403, 429, 503])
def test_unexpected_discord_rejection_is_not_accepted(monkeypatch, status):
    test = Scenario(monkeypatch)
    advance_to(test, 3)
    original = probe.discord_request

    async def unexpected(env, stages, fingerprints, stage, method, path, **kwargs):
        if stage == "google_sync_discord_invalid_update":
            return status, {}
        return await original(env, stages, fingerprints, stage, method, path, **kwargs)

    monkeypatch.setattr(probe, "discord_request", unexpected)
    assert test.call("advance")[0] == 409
    assert test.call("verify")[0] == 409
    assert test.call("cleanup")[0] == 200
    assert test.owner()["outcome"] == "failed_clean"


def test_do_rejects_removing_api_rejection_requirement(monkeypatch):
    test = Scenario(monkeypatch)
    advance_to(test, 3)
    owner = test.owner()
    owner["api_rejection_enabled"] = False
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        asyncio.run(test.store.put_e2e_manifest(SERVICE, owner))
    assert test.call("cleanup")[0] == 200
