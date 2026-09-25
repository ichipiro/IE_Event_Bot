"""Googleを含む所有2件の通常ポーリングと部分失敗回収を検証する。"""

import json
from copy import deepcopy
from urllib.parse import urlparse

import pytest
from workers import Response

import discord_notion_sync
import e2e_google_probe
from e2e_discord_batch_state import GOOGLE_SERVICE, key_prefix
from e2e_discord_kv_state import KEYS
from e2e_entry import Default
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_discord_batch_probe import env_for_batch
from tests.test_e2e_discord_notion_probe import RUN_ID, install_api_stub, run


def environment():
    env = env_for_batch()
    env.E2E_DISCORD_BATCH_GOOGLE_ENABLED = "true"
    env.GOOGLE_CALENDAR_ID = "isolated-calendar"
    env.GOOGLE_API_BEARER_TOKEN = "fixture-access-token"
    return env


def owner(env):
    result = run(StateStore(env).get_e2e_manifest(GOOGLE_SERVICE))
    assert isinstance(result, dict)
    return result


def call(env, phase="", *, run_id=RUN_ID):
    worker = Default()
    worker.env = env
    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-batch-google" + phase,
                method="POST",
                headers={
                    "Authorization": "Bearer test-token",
                    "X-E2E-Run-ID": run_id,
                    "X-E2E-Version-Tag": run_id,
                },
            )
        )
    )
    return response.status, json.loads(run(response.text()))


def install(monkeypatch, env):
    events, pages, calls, control = install_api_stub(monkeypatch, multiple=True)
    google_events = {}
    normal_fetch = discord_notion_sync.fetch
    control.update(
        google_lose_response=False, google_delete_fail=False, google_collision=False
    )

    async def fetch(url, options=None):
        request = options or {}
        parsed = urlparse(url)
        method = request.get("method", "GET")
        if parsed.netloc != "www.googleapis.com":
            if (
                parsed.netloc == "api.notion.com"
                and parsed.path == "/v1/pages"
                and method == "POST"
            ):
                payload = json.loads(request["body"])
                event_id = payload["properties"]["メッセージID"]["rich_text"][0][
                    "text"
                ]["content"]
                manifest = await StateStore(env).get_e2e_manifest(GOOGLE_SERVICE)
                assert isinstance(manifest, dict)
                slot = next(
                    s for s in manifest["fixtures"] if s["discord_event_id"] == event_id
                )
                assert slot["create_attempted"]["notion_page"] is True
                assert slot["create_attempted"]["google_event"] is True
                assert slot["google_event_id"] in google_events
            return await normal_fetch(url, options)
        calls.append((method, str(url)))
        collection = "/calendar/v3/calendars/isolated-calendar/events"
        if parsed.path == collection.removesuffix("/events"):
            return Response(json.dumps({"id": env.GOOGLE_CALENDAR_ID}), status=200)
        if parsed.path == collection and method == "POST":
            payload = json.loads(request["body"])
            manifest = await StateStore(env).get_e2e_manifest(GOOGLE_SERVICE)
            assert isinstance(manifest, dict)
            slot = next(
                s for s in manifest["fixtures"] if s["google_event_id"] == payload["id"]
            )
            assert slot["create_attempted"]["google_event"] is True
            assert slot["create_attempted"]["notion_page"] is False
            assert payload["id"] not in google_events
            assert "attendees" not in payload
            google_events[payload["id"]] = {**payload, "status": "confirmed"}
            if control["google_lose_response"]:
                control["google_lose_response"] = False
                raise RuntimeError("lost response after committed insert")
            return Response(json.dumps(google_events[payload["id"]]), status=200)
        event_id = parsed.path.rsplit("/", 1)[-1]
        if method == "GET":
            if control["google_collision"]:
                return Response(
                    json.dumps({"id": event_id, "summary": "foreign"}), status=200
                )
            return Response(
                json.dumps(google_events.get(event_id, {})),
                status=200 if event_id in google_events else 404,
            )
        if method == "DELETE":
            assert parsed.query == "sendUpdates=none"
            if control["google_delete_fail"]:
                return Response("{}", status=500)
            google_events.pop(event_id, None)
            return Response("", status=204)
        raise AssertionError((method, parsed.path))

    monkeypatch.setattr(discord_notion_sync, "fetch", fetch)
    monkeypatch.setattr(e2e_google_probe, "fetch", fetch)
    return events, pages, google_events, calls, control


def test_google_notion_mapping_and_kv_queue_across_http_then_cleanup(monkeypatch):
    env = environment()
    events, pages, google, calls, _ = install(monkeypatch, env)
    original = dict(env.STATE_KV.data)
    assert call(env)[0] == 200
    assert len(events) == 2 and len(google) == len(pages) == 1
    current = owner(env)
    assert key_prefix(current).startswith("e2e:discord_batch_google:")
    assert call(env, "/verify")[0] == 200
    assert call(env, "/advance")[0] == 200
    assert len(google) == len(pages) == 2
    assert call(env, "/verify")[0] == 200
    for page in pages.values():
        google_id = page["properties"]["GoogleイベントID"]["rich_text"][0]["text"][
            "content"
        ]
        source_id = page["properties"]["メッセージID"]["rich_text"][0]["text"][
            "content"
        ]
        assert (
            google[google_id]["extendedProperties"]["private"]["ie_discord_event_id"]
            == source_id
        )
    assert call(env, "/advance")[0] == 409
    assert call(env, "/cleanup")[0] == 200
    assert call(env, "/cleanup")[0] == 200
    assert not google and not events and all(p["archived"] for p in pages.values())
    assert env.STATE_KV.data == original
    clean = owner(env)
    assert clean["outcome"] == "passed" and clean["dirty"] is False
    assert (
        clean["stages"]["batch_google_pending"]
        == clean["stages"]["batch_google_final"]
        == 200
    )
    assert not any("/channels/" in url for _, url in calls)
    assert "fixture-access-token" not in json.dumps(clean)
    assert not any(
        s["google_event_id"] in json.dumps(clean) for s in current["fixtures"]
    )


@pytest.mark.parametrize("phase", ["", "/advance"])
@pytest.mark.parametrize("lost", ["google", "notion"])
def test_lost_create_response_preserves_planned_owner_and_recovers(
    monkeypatch, phase, lost
):
    env = environment()
    events, pages, google, _, control = install(monkeypatch, env)
    if phase:
        assert call(env)[0] == 200
        assert call(env, "/verify")[0] == 200
    control[
        "google_lose_response" if lost == "google" else "lose_notion_create_response"
    ] = True
    assert call(env, phase)[0] == 409
    assert owner(env)["dirty"] is True
    assert call(env, "/cleanup")[0] == 200
    assert not events and not google and all(p["archived"] for p in pages.values())
    assert owner(env)["outcome"] == "failed_clean"


def test_google_collision_is_not_adopted_or_deleted(monkeypatch):
    env = environment()
    events, pages, _, calls, control = install(monkeypatch, env)
    control["google_collision"] = True
    assert call(env)[0] == 409
    assert call(env, "/cleanup")[0] == 200
    assert not pages and not events
    assert not any(
        method in ("POST", "DELETE") and "googleapis.com" in url
        for method, url in calls
    )


def test_google_delete_failure_keeps_owner_until_retry(monkeypatch):
    env = environment()
    events, pages, google, _, control = install(monkeypatch, env)
    for phase in ("", "/verify", "/advance", "/verify"):
        assert call(env, phase)[0] == 200
    control["google_delete_fail"] = True
    assert call(env, "/cleanup")[0] == 409
    assert not events and all(p["archived"] for p in pages.values())
    assert len(google) == 2 and owner(env)["dirty"] is True
    control["google_delete_fail"] = False
    assert call(env, "/cleanup")[0] == 200
    assert not google and owner(env)["outcome"] == "passed"


@pytest.mark.parametrize("resource", ["google", "mapping"])
def test_readback_rejects_changed_content_or_mapping(monkeypatch, resource):
    env = environment()
    _, pages, google, _, _ = install(monkeypatch, env)
    for phase in ("", "/verify", "/advance", "/verify"):
        assert call(env, phase)[0] == 200
    if resource == "google":
        next(iter(google.values()))["location"] = "changed"
    else:
        next(iter(pages.values()))["properties"]["GoogleイベントID"]["rich_text"][0][
            "text"
        ]["content"] = "wrong-id"
    assert call(env, "/verify")[0] == 409
    assert call(env, "/cleanup")[0] == 200
    assert owner(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize("field", ["google_event_id", "calendar_id_sha256"])
def test_do_rejects_changed_google_owner(monkeypatch, field):
    env = environment()
    install(monkeypatch, env)
    assert call(env)[0] == 200
    current = owner(env)
    changed = deepcopy(current)
    if field == "google_event_id":
        changed["fixtures"][0][field] = "foreign"
    else:
        changed["target_fingerprints"][field] = "b" * 64
    with pytest.raises(RuntimeError):
        run(StateStore(env).put_e2e_manifest(GOOGLE_SERVICE, changed))
    assert owner(env) == current


@pytest.mark.parametrize(
    "phase,key",
    [
        ("", KEYS[0]),
        ("", "sync:discord_notion_queue"),
        ("/advance", "sync:discord_notion_queue"),
    ],
)
def test_kv_save_failure_recovers_google_notion_discord(monkeypatch, phase, key):
    env = environment()
    events, pages, google, _, _ = install(monkeypatch, env)
    original = dict(env.STATE_KV.data)
    if phase:
        assert call(env)[0] == 200
        assert call(env, "/verify")[0] == 200
    env.STATE_KV.fail_put = key
    assert call(env, phase)[0] == 409
    assert call(env, "/cleanup")[0] == 200
    assert not events and not google and all(p["archived"] for p in pages.values())
    assert env.STATE_KV.data == original and owner(env)["outcome"] == "failed_clean"


def test_stale_kv_returns_scenario_specific_retry_code(monkeypatch):
    env = environment()
    install(monkeypatch, env)
    assert call(env)[0] == 200
    current = owner(env)
    env.STATE_KV.data[key_prefix(current) + "sync:discord_notion_queue"] = "[]"
    status, result = call(env, "/verify")
    assert status == 409 and result["error"] == "discord_batch_google_not_ready"
    assert call(env, "/cleanup")[0] == 200


def test_foreign_google_ownership_refuses_delete_and_keeps_dirty(monkeypatch):
    env = environment()
    _, _, google, calls, _ = install(monkeypatch, env)
    assert call(env)[0] == 200
    event = next(iter(google.values()))
    original = deepcopy(event)
    event["extendedProperties"]["private"]["ie_discord_event_id"] = "foreign-source"
    before = len(calls)
    assert call(env, "/cleanup")[0] == 409
    assert not any(
        method == "DELETE" and "googleapis.com" in url for method, url in calls[before:]
    )
    assert google and owner(env)["dirty"] is True
    google[event["id"]] = original
    assert call(env, "/cleanup")[0] == 200


@pytest.mark.parametrize("change", ["calendar", "flag", "version"])
def test_changed_target_or_gate_stops_before_external_requests(monkeypatch, change):
    env = environment()
    _, _, _, calls, _ = install(monkeypatch, env)
    assert call(env)[0] == 200
    before = len(calls)
    if change == "calendar":
        env.GOOGLE_CALENDAR_ID = "other-calendar"
    if change == "flag":
        env.E2E_DISCORD_BATCH_GOOGLE_ENABLED = "false"
    run_id = "E2E-20260902T000001Z-1234abcd" if change == "version" else RUN_ID
    assert call(env, "/advance", run_id=run_id)[0] in (404, 409)
    assert len(calls) == before


def test_auth_failure_during_reverification_revokes_passed_outcome(monkeypatch):
    import e2e_discord_batch_google

    env = environment()
    install(monkeypatch, env)
    for phase in ("", "/verify", "/advance", "/verify"):
        assert call(env, phase)[0] == 200
    original = e2e_discord_batch_google.get_google_access_token

    async def missing_token(env, state):
        assert state.enabled() is False
        return None

    monkeypatch.setattr(
        e2e_discord_batch_google, "get_google_access_token", missing_token
    )
    assert call(env, "/verify")[0] == 409
    assert owner(env)["verification_passed"] is False
    monkeypatch.setattr(e2e_discord_batch_google, "get_google_access_token", original)
    assert call(env, "/cleanup")[0] == 200
    assert owner(env)["outcome"] == "failed_clean"


def test_service_account_resolution_uses_disabled_cache_state(monkeypatch):
    import google_auth
    from e2e_discord_batch_google import GoogleBatch

    env = environment()
    env.GOOGLE_API_BEARER_TOKEN = ""
    original = dict(env.STATE_KV.data)

    async def service_account(env, state):
        assert state.enabled() is False
        await google_auth._save_cached_token(state, "fixture-access-token", 123456789)
        return "fixture-access-token"

    monkeypatch.setattr(
        google_auth, "_fetch_token_from_service_account", service_account
    )
    context = run(GoogleBatch.connect(env))
    assert context.env.GOOGLE_API_BEARER_TOKEN == "fixture-access-token"
    assert env.STATE_KV.data == original
