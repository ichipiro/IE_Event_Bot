"""通常StateStoreの保存・復元を検証する。KVと外部APIだけを代替する。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
import discord_notion_sync as sync
from state import StateStore
from tests.fakes import MemoryKV

QUEUE = 'sync:discord_notion_queue'
SNAPSHOT = 'discord:snapshot'


class FailingStateKV(MemoryKV):
    fail_queue = False
    fail_snapshot = False

    async def put(self, key, value):
        if key == SNAPSHOT and self.fail_snapshot:
            self.fail_snapshot = False
            raise RuntimeError('injected_snapshot_write_failure')
        if key == QUEUE and self.fail_queue:
            self.fail_queue = False
            raise RuntimeError('injected_queue_write_failure')
        await super().put(key, value)


def run(coro):
    return asyncio.run(coro)


def prepare(monkeypatch, operation, *, fail_queue=False, limit=5):
    kv = FailingStateKV()
    env = SimpleNamespace(STATE_KV=kv, DISCORD_TO_GOOGLE_SYNC_ENABLED='false',
                          DISCORD_NOTION_MAX_CHANGES_PER_RUN=str(limit))
    old = {'id': 'owned-1', 'name': 'before', 'status': 1}
    new = {**old, 'name': 'after'}
    if operation != 'create':
        run(StateStore(env).set_discord_snapshot({'owned-1': sync._fingerprint(old)}))
    run(StateStore(env).put_json(QUEUE, []))
    kv.data['unrelated'] = 'preserve'
    events = [] if operation == 'delete' else [new]
    calls = []

    async def list_events(_env):
        return deepcopy(events), None

    async def apply(_env, event_or_id, token):
        calls.append('apply')
        return len(calls) > 1

    async def no_notification(_env, event):
        return True

    monkeypatch.setattr(sync, '_list_discord_scheduled_events', list_events)
    monkeypatch.setattr(sync, '_sync_discord_event_upsert', apply)
    monkeypatch.setattr(sync, '_sync_discord_event_delete', apply)
    monkeypatch.setattr(sync, '_notify_discord_event_created', no_notification)
    kv.fail_queue = fail_queue
    return env, kv, calls


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_saved_retry_restores_and_does_not_repeat(monkeypatch, operation):
    env, kv, calls = prepare(monkeypatch, operation)
    first = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert first['ok'] is False and first['pending_changes'] == 1
    assert run(StateStore(env).get_json(QUEUE)) == [
        {'id': 'owned-1', 'op': 'delete' if operation == 'delete' else 'upsert'}]
    second = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert second['ok'] is True and second['processed_changes'] == 1
    assert second['pending_changes'] == 0
    third = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert third['ok'] is True and third['processed_changes'] == 0
    assert calls == ['apply', 'apply']
    assert run(StateStore(env).get_json(QUEUE)) == []
    assert kv.data['unrelated'] == 'preserve'


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_retry_survives_queue_write_failure(monkeypatch, operation):
    env, kv, calls = prepare(monkeypatch, operation, fail_queue=True)
    with pytest.raises(RuntimeError, match='injected_queue_write_failure'):
        run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    # 新しいStateStoreが、保存済みKVだけから未処理操作を回復できることを要求する。
    second = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert kv.data['unrelated'] == 'preserve'
    assert second['processed_changes'] == 1, {
        'next_processed': second['processed_changes'], 'next_ok': second['ok'],
        'apply_calls': len(calls), 'saved_queue': run(StateStore(env).get_json(QUEUE)),
    }
    assert calls == ['apply', 'apply']


@pytest.mark.parametrize('fail_queue', [False, True])
def test_unprocessed_limit_remainder_survives_restart(monkeypatch, fail_queue):
    env, kv, calls = prepare(monkeypatch, 'create', fail_queue=fail_queue, limit=1)
    events = [{'id': 'owned-1'}, {'id': 'owned-2'}]
    applied_ids = []

    async def list_events(_env):
        return events, None

    async def succeed(_env, event, token):
        applied_ids.append(event['id'])
        return True

    monkeypatch.setattr(sync, '_list_discord_scheduled_events', list_events)
    monkeypatch.setattr(sync, '_sync_discord_event_upsert', succeed)
    if fail_queue:
        with pytest.raises(RuntimeError, match='injected_queue_write_failure'):
            run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    else:
        result = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
        assert result['pending_changes'] == 1
    result = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert result['processed_changes'] == 1
    if fail_queue:
        # 外部成功後に保存できなかった操作は再実行され得るが、残件は失わない。
        result = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
        assert result['pending_changes'] == 0
        assert set(applied_ids) == {'owned-1', 'owned-2'}
    else:
        assert applied_ids == ['owned-1', 'owned-2']
    assert run(StateStore(env).get_json(QUEUE)) == []


def test_manual_route_serializes_concurrent_updates(monkeypatch):
    import entry
    from tests.fakes import Request, make_sync_coordinator_namespace

    env, kv, calls = prepare(monkeypatch, 'update')
    env.INTERNAL_API_TOKEN = 'local-test-token'
    env.SYNC_COORDINATOR = make_sync_coordinator_namespace()
    env.SYNC_DO_LOCK_ENABLED = 'true'

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        writers = []

        async def held_upsert(_env, event, token):
            writers.append(event['id'])
            entered.set()
            await release.wait()
            return True

        monkeypatch.setattr(sync, '_sync_discord_event_upsert', held_upsert)

        async def invoke():
            worker = entry.Default()
            worker.env = env
            return await worker.fetch(Request('https://bot.test/sync/discord-notion', method='POST',
                headers={'Authorization': 'Bearer local-test-token'}))

        first = asyncio.create_task(invoke())
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = asyncio.create_task(invoke())
        # 2件目が拒否されるか、適用に到達して待機するまでevent loopを進める。
        for _ in range(10):
            await asyncio.sleep(0)
            if second.done() or len(writers) == 2:
                break
        count_while_first_running = len(writers)
        release.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        return count_while_first_running, [response.status for response in responses]

    writers, statuses = run(scenario())
    assert writers == 1, {'concurrent_writers': writers, 'http_statuses': statuses}
    assert sorted(statuses) == [200, 409]


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_retry_survives_snapshot_write_failure(monkeypatch, operation):
    env, kv, calls = prepare(monkeypatch, operation)
    old_snapshot = run(StateStore(env).get_discord_snapshot())
    kv.fail_snapshot = True
    with pytest.raises(RuntimeError, match='injected_snapshot_write_failure'):
        run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert run(StateStore(env).get_discord_snapshot()) == old_snapshot
    assert run(StateStore(env).get_json(QUEUE)) == [
        {'id': 'owned-1', 'op': 'delete' if operation == 'delete' else 'upsert'}]
    result = run(sync.run_discord_notion_poll_sync(env, StateStore(env)))
    assert result['ok'] is True and result['pending_changes'] == 0
    assert calls == ['apply', 'apply']
