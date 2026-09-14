"""固定の障害モデルで現行同期の保証限界を記録する。外部通信は行わない。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import discord_notion_sync as sync
import sync_lock_do
from e2e_entry import Default
from state import StateStore
from tests.test_discord_notification_retry import poll, prepare
from tests.test_e2e_sync_lock_probe import environment

QUEUE = "sync:discord_notion_queue"
SNAPSHOT = "discord:snapshot"


@pytest.mark.parametrize("legacy", [False, True])
def test_invisible_delete_pending_cannot_be_recovered(monkeypatch, legacy):
    env, events, calls, failures = prepare(monkeypatch)
    assert poll(env)["ok"]
    event_id = events.pop()["id"]

    async def failed_delete(_env, target, token):
        calls["delete"].append(target)
        return False

    monkeypatch.setattr(sync, "_sync_discord_event_delete", failed_delete)
    assert poll(env)["pending_changes"] == 1
    original_get = env.STATE_KV.get
    # 旧形式は削除を観測済みの空snapshotだけを保存していた。
    # 新形式も、作成前の空snapshotと空queueを同時に読むと残件が見えない。
    if legacy:
        env.STATE_KV.data[SNAPSHOT] = "{}"

    async def hidden_pending(key):
        if key == QUEUE:
            return "[]"
        if not legacy and key == SNAPSHOT:
            return "{}"
        return await original_get(key)

    monkeypatch.setattr(env.STATE_KV, "get", hidden_pending)
    # 別イベントの失敗残件を保存し、空値の同値書込み省略を避ける。
    events.append({"id": "event-new", "status": 1})
    failures["upsert"] = 1
    result = poll(env)
    assert not result["ok"] and result["pending_changes"] == 1
    assert calls["delete"] == [event_id]
    monkeypatch.setattr(env.STATE_KV, "get", original_get)
    # 古い状態を基に成功保存すると、最新の残件も上書きされる。
    assert poll(env)["pending_changes"] == 0
    assert calls["delete"] == [event_id]
    assert json.loads(env.STATE_KV.data[QUEUE]) == []


def test_stale_empty_state_replays_apply_and_creation_notification(monkeypatch):
    env, _, calls, _ = prepare(monkeypatch)
    assert poll(env)["ok"]
    original_get = env.STATE_KV.get

    async def stale(key):
        if key == SNAPSHOT:
            return "{}"
        if key == QUEUE:
            return "[]"
        return await original_get(key)

    monkeypatch.setattr(env.STATE_KV, "get", stale)
    assert poll(env)["ok"]
    assert calls["upsert"] == ["event-0", "event-0"]
    assert len(calls["post"]) == 2
    assert calls["reaction"][0] != calls["reaction"][1]


def test_missing_post_response_retries_with_another_message(monkeypatch):
    env, _, calls, _ = prepare(monkeypatch)
    original_api = sync._discord_api_request
    lose_response = True

    async def api(_env, method, path, payload=None):
        nonlocal lose_response
        result = await original_api(_env, method, path, payload)
        if method == "POST" and lose_response:
            lose_response = False
            # 代替サービスでは投稿済みだが、呼出元にはmessage IDが届かない。
            return None, 500
        return result

    monkeypatch.setattr(sync, "_discord_api_request", api)
    assert poll(env)["pending_changes"] == 1
    assert poll(env)["ok"]
    assert calls["upsert"] == ["event-0"]
    assert len(calls["post"]) == 2
    assert len(calls["reaction"]) == 1
    assert "/messages/message-2/" in calls["reaction"][0]


def test_queue_save_failure_after_notification_allows_duplicate(monkeypatch):
    env, _, calls, _ = prepare(monkeypatch)
    original_put = env.STATE_KV.put

    async def fail_queue(key, value, **kwargs):
        if key == QUEUE:
            raise RuntimeError("injected_queue_save_failure")
        return await original_put(key, value, **kwargs)

    monkeypatch.setattr(env.STATE_KV, "put", fail_queue)
    with pytest.raises(RuntimeError, match="injected_queue_save_failure"):
        poll(env)
    assert len(calls["post"]) == 1
    monkeypatch.setattr(env.STATE_KV, "put", original_put)
    assert poll(env)["ok"]
    assert calls["upsert"] == ["event-0", "event-0"]
    assert len(calls["post"]) == 2


@pytest.mark.parametrize("source", ["manual", "cron", "all"])
def test_expiry_during_result_put_can_overwrite_new_result(monkeypatch, source):
    clock = [1000.0]
    monkeypatch.setattr(sync_lock_do, "time", SimpleNamespace(time=lambda: clock[0]))
    worker = Default()
    worker.env = environment()
    state = StateStore(worker.env)
    kv = worker.env.STATE_KV
    original_put = kv.put
    result_key = "result:sync_all" if source == "all" else "result:sync_discord_notion"

    async def scenario():
        intercepted = False
        replacement_owner = None
        new_value = None
        old_value = None

        async def invoke(body):
            if source != "all":
                return await worker._invoke_lock_probe(source, state, body)

            async def fetch(_env, _state, *, commit_cursor):
                return {**await body(), "items": []}

            async def no_external(*args):
                return {"ok": True}

            response = await worker._run_sync_dispatch(
                None, state, "manual", google_fetcher=fetch,
                google_applier=no_external, discord_runner=no_external,
            )
            return json.loads(await response.text()), response.status

        async def body():
            return {"ok": True, "writer": "old"}

        async def delayed_put(key, value, **kwargs):
            nonlocal intercepted, replacement_owner, new_value, old_value
            if key == result_key and not intercepted:
                intercepted = True
                old_value = value
                # owner確認済み・KV保存未完了の隙間で期限が切れる。
                clock[0] += 121

                async def replacement():
                    return {"ok": False, "writer": "new"}

                result, status = await invoke(replacement)
                assert status == 500 and not result["ok"]
                new_value = kv.data[result_key]
                replacement_owner = await worker._acquire_sync_lock("replacement")
                assert replacement_owner["ok"]
            return await original_put(key, value, **kwargs)

        monkeypatch.setattr(kv, "put", delayed_put)
        result, status = await invoke(body)
        assert intercepted and status == 200 and result["ok"]
        assert new_value is not None and new_value != old_value
        assert json.loads(new_value)["payload"]["ok"] is False
        assert kv.data[result_key] == old_value
        assert json.loads(kv.data[result_key])["payload"]["ok"] is True
        assert replacement_owner is not None
        assert (await worker._sync_lock_status())["lock"]["owner"] == replacement_owner["owner"]
        await worker._release_sync_lock(replacement_owner["owner"])

    asyncio.run(scenario())
