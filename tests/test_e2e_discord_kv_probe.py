"""外部fixtureと通常KVの同run所有・別HTTP検証・回収を代替APIで検証する。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from e2e_discord_fixture_state import SERVICE, FixtureDiscordKV
from e2e_discord_kv_state import KEYS
from e2e_entry import Default
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_discord_kv_state import KV
from tests.test_e2e_discord_notion_probe import (
    DISCORD_EVENT_ID, GUILD_ID, PAGE_ID, RUN_ID, install_api_stub, make_env, run,
)

PATH = "/admin/e2e/discord-kv"


def environment():
    env = make_env()
    env.STATE_KV = KV({KEYS[0]: "original", KEYS[1]: "original", "foreign": "original"})
    env.E2E_STATE_SCOPE = "fixture-scope"
    env.E2E_DISCORD_KV_ENABLED = "true"
    env.E2E_DISCORD_DELTA_ENABLED = "true"
    env.INTERNAL_API_TOKEN = "test-token"
    env.CF_VERSION_METADATA = SimpleNamespace(tag=RUN_ID, id="version-id")
    env.EVENT_CREATE_CHANNEL_ID = "must-not-send"
    return env


def request(env, suffix="", *, run_id=RUN_ID, token="test-token", version=RUN_ID, method="POST"):
    worker = Default()
    worker.env = env
    response = run(worker.fetch(Request("https://bot.test" + PATH + suffix, method=method, headers={
        "Authorization": "Bearer " + token, "X-E2E-Run-ID": run_id, "X-E2E-Version-Tag": version,
    })))
    text = run(response.text())
    return response.status, json.loads(text) if text.startswith("{") else {"error": text}


def manifest(env):
    result = run(StateStore(env).get_e2e_manifest(SERVICE))
    assert isinstance(result, dict)
    return result


def test_fixture_and_normal_kv_survive_requests_and_cleanup_together(monkeypatch):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    events['foreign'] = {"id": "foreign", "guild_id": GUILD_ID, "name": "Other run"}
    foreign = deepcopy(events['foreign'])
    env = environment()
    original = dict(env.STATE_KV.data)
    status, prepared = request(env)
    assert status == 200, prepared
    assert prepared['dirty'] is True
    owner = manifest(env)
    assert owner['stage'] == 'kv_prepared'
    assert owner['discord_event_id'] == DISCORD_EVENT_ID and owner['notion_page_id'] == PAGE_ID
    assert not any(k in owner for k in ('snapshot', 'queue', 'delta_checkpoint'))
    adapter = FixtureDiscordKV(StateStore(env), owner)
    assert set(env.STATE_KV.data) == set(original) | {adapter.prefix + k for k in KEYS}
    assert request(env, '/verify')[0] == 200
    assert request(env, '/cleanup')[0] == 200
    assert request(env, '/cleanup')[0] == 200
    assert env.STATE_KV.data == original
    assert events == {'foreign': foreign} and pages[PAGE_ID]['archived'] is True
    clean = manifest(env)
    assert clean['outcome'] == 'passed' and clean['dirty'] is False
    assert not any(value in json.dumps(clean) for value in (DISCORD_EVENT_ID, PAGE_ID, GUILD_ID))
    assert not any('/channels/' in path or 'googleapis.com' in path for _, path in calls)
    with pytest.raises(ValueError, match='owner_mismatch'):
        run(adapter.state().get_discord_snapshot())


@pytest.mark.parametrize('key', KEYS)
def test_kv_save_failure_recovers_external_fixture_and_partial_keys(monkeypatch, key):
    events, pages, _, _ = install_api_stub(monkeypatch)
    env = environment()
    original = dict(env.STATE_KV.data)
    env.STATE_KV.fail_put = key
    status, result = request(env)
    assert status != 200 and result['ok'] is False
    assert not events and pages[PAGE_ID]['archived'] is True
    assert env.STATE_KV.data == original
    assert manifest(env)['outcome'] == 'failed_clean'


@pytest.mark.parametrize('key', KEYS)
def test_kv_delete_failure_keeps_owner_until_retry(monkeypatch, key):
    events, pages, _, _ = install_api_stub(monkeypatch)
    env = environment()
    assert request(env)[0] == 200
    assert request(env, '/verify')[0] == 200
    before = manifest(env)
    env.STATE_KV.fail_delete = key
    assert request(env, '/cleanup')[0] != 200
    assert manifest(env)['dirty'] is True
    assert manifest(env)['scope_id'] == before['scope_id']
    assert not events and pages[PAGE_ID]['archived'] is True
    env.STATE_KV.fail_delete = None
    assert request(env, '/cleanup')[0] == 200
    assert manifest(env)['dirty'] is False


def test_stale_read_does_not_repeat_external_apply_or_reuse_success(monkeypatch):
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = environment()
    assert request(env)[0] == 200
    assert request(env, '/verify')[0] == 200
    owner = manifest(env)
    adapter = FixtureDiscordKV(StateStore(env), owner)
    env.STATE_KV.data[adapter.prefix + KEYS[0]] = '{}'
    status, result = request(env, '/verify')
    assert status == 409 and result['error'] == 'discord_kv_not_ready'
    assert request(env, '/cleanup')[0] == 200
    assert manifest(env)['outcome'] == 'failed_clean'
    assert sum(method == 'POST' and path.endswith('/pages') for method, path in calls) == 1


@pytest.mark.parametrize('changed', ['run', 'scope', 'guild', 'page'])
def test_owner_mismatch_stops_before_external_cleanup(monkeypatch, changed):
    events, pages, calls, _ = install_api_stub(monkeypatch)
    env = environment()
    assert request(env)[0] == 200
    before = len(calls)
    if changed == 'scope':
        env.E2E_STATE_SCOPE = 'changed'
    elif changed == 'guild':
        env.DISCORD_GUILD_ID = 'changed'
    elif changed == 'page':
        pages[PAGE_ID]['properties']['イベント名']['title'][0]['text']['content'] = 'foreign'
    options = {'run_id': 'E2E-20260902T000001Z-1234abcd'} if changed == 'run' else {}
    status, result = request(env, '/cleanup', **options)
    assert status != 200 and result['ok'] is False
    assert manifest(env)['dirty'] is True
    if changed != 'page':
        assert len(calls) == before and DISCORD_EVENT_ID in events
    assert pages[PAGE_ID].get('archived') is not True


@pytest.mark.parametrize('options,expected', [({'token':'wrong'},401), ({'method':'GET'},405), ({'version':'wrong'},409)])
def test_route_requires_auth_post_and_revision_before_external_calls(monkeypatch, options, expected):
    _, _, calls, _ = install_api_stub(monkeypatch)
    assert request(environment(), **options)[0] == expected
    assert calls == []


@pytest.mark.parametrize('key,value', [('scope_id','b'*32), ('discord_event_id','other'), ('notion_page_id','other'), ('run_id','E2E-20260902T000001Z-1234abcd')])
def test_do_refuses_owner_replacement(monkeypatch, key, value):
    install_api_stub(monkeypatch)
    env = environment()
    assert request(env)[0] == 200
    before = manifest(env)
    replaced = deepcopy(before)
    replaced[key] = value
    with pytest.raises(RuntimeError):
        run(StateStore(env).put_e2e_manifest(SERVICE, replaced))
    assert manifest(env) == before


@pytest.mark.parametrize('failure', ['existing', 'unavailable'])
def test_apply_lookup_never_updates_foreign_page_or_creates_on_error(monkeypatch, failure):
    import discord_notion_sync

    events, pages, calls, _ = install_api_stub(monkeypatch)

    async def query(env, db, event_id, *, strict=False):
        assert strict is True
        if failure == 'unavailable':
            raise RuntimeError('notion_query_failed')
        return {'id': 'foreign-page'}

    monkeypatch.setattr(discord_notion_sync, '_notion_query_by_message_id', query)
    env = environment()
    status, result = request(env)
    assert status != 200 and result['ok'] is False
    assert not any(method in ('POST', 'PATCH') and '/pages' in path for method, path in calls)
    assert pages == {} and events == {}
    assert manifest(env)['dirty'] is True


@pytest.mark.parametrize('body,status', [({'results': []}, 503), ({'unexpected': []}, 200)])
def test_strict_notion_lookup_rejects_failed_or_malformed_response(monkeypatch, body, status):
    import discord_notion_sync
    from workers import Response

    async def fetch(url, options):
        return Response(json.dumps(body), status=status)

    monkeypatch.setattr(discord_notion_sync, 'fetch', fetch)
    with pytest.raises(RuntimeError, match='notion_query_'):
        run(discord_notion_sync._notion_query_by_message_id(environment(), 'test-db', 'test-event', strict=True))


@pytest.mark.parametrize('missing', ['E2E_DISCORD_KV_ENABLED', 'E2E_STATE_SCOPE', 'SYNC_COORDINATOR'])
def test_missing_binding_or_enable_flag_stops_external_calls(monkeypatch, missing):
    _, _, calls, _ = install_api_stub(monkeypatch)
    env = environment()
    setattr(env, missing, None)
    assert request(env)[0] in (404, 503)
    assert calls == []
