"""通常の手動・Cron・全体同期で共通ロックを使うことを確認する。"""
import asyncio
import json
from types import SimpleNamespace

import pytest

import entry
from state import StateStore
from tests.fakes import MemoryKV, Request, make_sync_coordinator_namespace


def run(coro):
    return asyncio.run(coro)


def make_worker():
    worker = entry.Default()
    worker.env = SimpleNamespace(
        INTERNAL_API_TOKEN='test-token', STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(), SYNC_DO_LOCK_ENABLED='true',
        CRON_ENABLE_SYNC='false', CRON_ENABLE_DISCORD_NOTION_SYNC='true',
        CRON_ENABLE_GCAL_WATCH_ENSURE='false', CRON_ENABLE_QA='false',
        CRON_ENABLE_REMINDER='false', CRON_ENABLE_AUTO_CLEAN='false',
    )
    return worker


async def invoke(worker, source):
    if source == 'cron':
        return (await worker.scheduled(None, worker.env, None))[0], None
    response = await worker.fetch(Request('https://bot.test/sync/discord-notion', method='POST',
        headers={'Authorization': 'Bearer test-token'}))
    return json.loads(await response.text()), response.status


@pytest.mark.parametrize('source', ['manual', 'cron'])
def test_shared_lock_blocks_poll_and_preserves_last_result(monkeypatch, source):
    worker = make_worker()
    calls = []

    async def poll(env, state):
        calls.append(source)
        return {'ok': True, 'processed_changes': 1}

    monkeypatch.setattr(entry, 'run_discord_notion_poll_sync', poll)
    store = StateStore(worker.env)
    run(store.put_json('result:sync_discord_notion', {'previous': True}))
    original = dict(worker.env.STATE_KV.data)
    lock = run(worker._acquire_sync_lock(source='manual'))
    result, status = run(invoke(worker, source))
    assert result['error'] == 'sync_in_progress' and result['ok'] is False
    assert status == (409 if source == 'manual' else None)
    assert calls == [] and worker.env.STATE_KV.data == original
    # 拒否側が既存ownerのロックを解放していないことも確認する。
    assert run(worker._acquire_sync_lock(source='probe'))['locked'] is True
    run(worker._release_sync_lock(lock['owner']))
    result, status = run(invoke(worker, source))
    assert result['ok'] is True and calls == [source]
    saved = run(store.get_json('result:sync_discord_notion'))
    assert isinstance(saved, dict)
    assert saved['payload']['ok'] is True
    if source == 'cron':
        assert saved['payload']['path'] == '/sync/discord-notion'
    assert run(worker._acquire_sync_lock(source='after'))['ok'] is True


@pytest.mark.parametrize('source', ['manual', 'cron'])
@pytest.mark.parametrize('failure', ['apply', 'result', 'cancel'])
def test_lock_released_after_failure(monkeypatch, source, failure):
    worker = make_worker()
    calls = []

    async def poll(env, state):
        calls.append(source)
        if len(calls) == 1:
            if failure == 'apply':
                raise RuntimeError('injected_apply_failure')
            if failure == 'cancel':
                raise asyncio.CancelledError()
        return {'ok': True}

    original_put = worker.env.STATE_KV.put
    writes = []

    async def failing_put(key, value):
        writes.append(key)
        if failure == 'result' and len(writes) == 1:
            raise RuntimeError('injected_result_failure')
        await original_put(key, value)

    monkeypatch.setattr(entry, 'run_discord_notion_poll_sync', poll)
    monkeypatch.setattr(worker.env.STATE_KV, 'put', failing_put)
    expected = asyncio.CancelledError if failure == 'cancel' else RuntimeError
    with pytest.raises(expected):
        run(invoke(worker, source))
    result, _ = run(invoke(worker, source))
    assert result['ok'] is True and len(calls) == 2


@pytest.mark.parametrize('source', ['manual', 'cron'])
def test_lock_error_does_not_run_sync(monkeypatch, source):
    worker = make_worker()

    async def unavailable(source):
        return {'ok': False, 'error': 'private_rpc_details'}

    async def unexpected(env, state):
        pytest.fail('ロック取得失敗時は同期しない')

    monkeypatch.setattr(worker, '_acquire_sync_lock', unavailable)
    monkeypatch.setattr(entry, 'run_discord_notion_poll_sync', unexpected)
    result, status = run(invoke(worker, source))
    assert result['error'] == 'sync_lock_unavailable'
    assert status == (503 if source == 'manual' else None)
    assert worker.env.STATE_KV.data == {}
    assert 'private_rpc_details' not in str(result)


def test_explicit_lock_disable_keeps_existing_behavior(monkeypatch):
    worker = make_worker()
    worker.env.SYNC_DO_LOCK_ENABLED = 'false'

    async def unexpected(source):
        pytest.fail('明示無効時はDOを呼ばない')

    async def poll(env, state):
        return {'ok': True}

    monkeypatch.setattr(worker, '_acquire_sync_lock', unexpected)
    monkeypatch.setattr(entry, 'run_discord_notion_poll_sync', poll)
    result, status = run(invoke(worker, 'manual'))
    assert result['ok'] is True and status == 200
