"""通常HTTPから全件取得・対象選別・共有KV・通知・安全な回収を検証する。"""

import json
from urllib.parse import urlparse

import pytest
from workers import Response

import e2e_entry
import e2e_reminder_normal_probe as normal
import jobs
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_reminder_probe import CHANNEL_ID, GUILD_ID, ROLE_ID, RUN_ID, make_env, run


def setup(monkeypatch):
    events, messages, calls = {}, {}, []
    control = {"lose_create": False, "fail_send": False}

    async def fetch(url, options=None):
        options = options or {}
        method = options.get("method", "GET")
        path = urlparse(url).path.removeprefix("/api/v10")
        data = json.loads(options.get("body") or "{}")
        calls.append((method, path, data))
        status, result = 200, {}
        if path == f"/guilds/{GUILD_ID}":
            result = {"id": GUILD_ID}
        elif path == f"/guilds/{GUILD_ID}/roles":
            result = [{"id": ROLE_ID, "mentionable": True}]
        elif path == f"/channels/{CHANNEL_ID}":
            result = {"id": CHANNEL_ID, "guild_id": GUILD_ID, "type": 0}
        elif path == f"/guilds/{GUILD_ID}/scheduled-events":
            if method == "GET":
                result = list(events.values())
            else:
                event_id = str(len(events) + 1)
                result = {**data, "id": event_id, "guild_id": GUILD_ID, "status": 1}
                events[event_id] = result
                if control["lose_create"]:
                    raise RuntimeError("lost response")
        elif path.startswith(f"/guilds/{GUILD_ID}/scheduled-events/"):
            event_id = path.rsplit("/", 1)[1]
            if method == "DELETE":
                events.pop(event_id, None)
                status = 204
            elif event_id in events:
                result = events[event_id]
            else:
                status = 404
        elif path == f"/channels/{CHANNEL_ID}/messages":
            if method == "GET":
                result = list(messages.values())
            elif control["fail_send"]:
                status = 403
            else:
                assert data["allowed_mentions"] == {"parse": [], "roles": [ROLE_ID], "replied_user": False}
                message_id = str(len(messages) + 1)
                result = {"id": message_id, "channel_id": CHANNEL_ID, "content": data["content"],
                          "mention_everyone": False, "mention_roles": [ROLE_ID]}
                messages[message_id] = result
        elif path.startswith(f"/channels/{CHANNEL_ID}/messages/"):
            message_id = path.rsplit("/", 1)[1]
            if method == "DELETE":
                messages.pop(message_id, None)
                status = 204
            elif message_id in messages:
                result = messages[message_id]
            else:
                status = 404
        else:
            raise AssertionError((method, path))
        return Response(json.dumps(result), status=status)

    monkeypatch.setattr(jobs, "fetch", fetch)
    import e2e_discord_probe
    monkeypatch.setattr(e2e_discord_probe, "fetch", fetch)
    env = make_env()
    async def delete(key):
        env.STATE_KV.data.pop(key, None)
    env.STATE_KV.delete = delete
    env.INTERNAL_API_TOKEN = "test-token"
    env.CF_VERSION_METADATA = {"tag": RUN_ID, "id": "reminder-version"}
    env.E2E_REMINDER_ENABLED = "true"
    env.SYNC_DO_LOCK_ENABLED = "true"
    headers = {"Authorization": "Bearer test-token", "X-E2E-Run-ID": RUN_ID,
               "X-E2E-Version-Tag": RUN_ID}
    request = Request("https://e2e.invalid/admin/e2e/reminder-normal/prepare", method="POST", headers=headers)
    return env, request, (events, messages, calls, control)


def phase(env, request, name):
    return run(normal.run(env, StateStore(env), RUN_ID, name, request))


def test_normal_http_selects_two_of_four_and_preserves_cache_on_second_request(monkeypatch):
    env, request, (events, messages, calls, _) = setup(monkeypatch)
    app = e2e_entry.Default()
    app.env = env
    cache = None
    for name in normal.PHASES:
        for step in (name, "verify"):
            request.url = f"https://e2e.invalid/admin/e2e/reminder-normal/{step}"
            response = run(app.fetch(request))
            assert response.status == 200, run(response.text())
        assert len(events) == 4
        if name == "prepare":
            assert not messages
        else:
            assert len(messages) == 2
            current = run(StateStore(env).get_json("reminder_cache", {}))
            assert isinstance(current, dict)
            assert set(current) == {"1", "2"}
            if cache is not None:
                assert current == cache
            cache = current
    assert len([c for c in calls if c[0] == "POST" and c[1].endswith("/messages")]) == 2
    request.url = "https://e2e.invalid/admin/e2e/reminder/cleanup"
    response = run(app.fetch(request))
    assert response.status == 200, run(response.text())
    assert not events and not messages
    assert all(run(StateStore(env).get_text(k)) is None for k in normal.KEYS)
    manifest = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert manifest and manifest["outcome"] == "passed" and manifest["dirty"] is False


@pytest.mark.parametrize("key", normal.KEYS)
def test_existing_shared_state_is_never_overwritten(monkeypatch, key):
    env, request, (events, _, _, _) = setup(monkeypatch)
    run(env.STATE_KV.put(key, "foreign"))
    with pytest.raises(RuntimeError, match="shared_state_not_empty"):
        phase(env, request, "prepare")
    assert not events and run(StateStore(env).get_text(key)) == "foreign"


@pytest.mark.parametrize("when", ("before", "after"))
def test_foreign_event_prevents_job_and_survives_cleanup(monkeypatch, when):
    env, request, (events, messages, _, _) = setup(monkeypatch)
    if when == "after":
        phase(env, request, "prepare")
        phase(env, request, "verify")
    events["foreign"] = {"id": "foreign"}
    with pytest.raises(RuntimeError, match="guild_not_empty|foreign_input"):
        phase(env, request, "prepare" if when == "before" else "notify")
    assert not messages
    if when == "after":
        assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    assert events == {"foreign": {"id": "foreign"}}


def test_cleanup_preserves_foreign_kv_and_wrong_run(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="run_id_mismatch"):
        run(normal.cleanup(env, StateStore(env), "E2E-20260925T000000Z-ffffffff"))
    run(env.STATE_KV.put("reminder_cache", "foreign"))
    with pytest.raises(RuntimeError, match="foreign_kv"):
        run(normal.cleanup(env, StateStore(env), RUN_ID))
    assert run(StateStore(env).get_text("reminder_cache")) == "foreign"
    manifest = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert manifest and manifest["dirty"] is True


def test_unverified_or_replayed_phase_rejected(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="previous_unverified"):
        phase(env, request, "notify")
    phase(env, request, "verify")
    phase(env, request, "notify")
    with pytest.raises(RuntimeError, match="phase_mismatch"):
        phase(env, request, "notify")


@pytest.mark.parametrize("header,value,status", (("X-E2E-Version-Tag", "old", 409), ("Authorization", "wrong", 401), ("X-E2E-Run-ID", "bad", 400)))
def test_route_requires_authorization_run_and_revision(monkeypatch, header, value, status):
    env, request, (events, _, _, _) = setup(monkeypatch)
    headers = {key: request.headers.get(key) for key in ("Authorization", "X-E2E-Run-ID", "X-E2E-Version-Tag")}
    headers[header] = value
    request = Request(request.url, method="POST", headers=headers)
    app = e2e_entry.Default()
    app.env = env
    response = run(app.fetch(request))
    assert response.status == status
    assert not events


def test_other_scenario_cannot_claim_during_shared_reminder(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
        run(StateStore(env).put_e2e_manifest("notion", {
            "version": 1, "kind": "notion_pages", "dirty": True, "run_id": RUN_ID,
        }))


@pytest.mark.parametrize("failure", ("lose_create", "fail_send"))
def test_partial_failure_retains_owner_and_can_cleanup(monkeypatch, failure):
    env, request, (events, messages, _, control) = setup(monkeypatch)
    if failure == "fail_send":
        phase(env, request, "prepare")
        phase(env, request, "verify")
    control[failure] = True
    with pytest.raises(RuntimeError):
        phase(env, request, "prepare" if failure == "lose_create" else "notify")
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    assert not events and not messages
    manifest = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert manifest and manifest["outcome"] == "failed_clean"


def test_normal_list_failure_is_not_reported_as_success(monkeypatch):
    env, _, _ = setup(monkeypatch)
    async def rejected(*args, **kwargs):
        return None, 429
    monkeypatch.setattr(jobs, "_discord_api_request", rejected)
    result = run(jobs.run_day_before_reminder_job(env, StateStore(env), return_detail=True))
    assert isinstance(result, dict) and result["ok"] is False
    assert result["error"] == "discord_event_list_failed_429"


def test_discord_job_request_includes_bot_user_agent(monkeypatch):
    env, _, _ = setup(monkeypatch)
    async def require_agent(url, options):
        assert options["headers"].get("User-Agent", "").startswith("DiscordBot (")
        return Response("[]", status=200)
    monkeypatch.setattr(jobs, "fetch", require_agent)
    assert run(jobs._list_discord_events(env)) == []
