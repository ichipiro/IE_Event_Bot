"""固定条件のモデルで現行同期の保証限界を記録する。外部通信は行わない。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import discord_notion_sync as sync
import google_calendar_sync as calendar
import sync_lock_do
from e2e.e2e_entry import Default
from entry import Application
from state import StateStore
from tests.conftest import Response
from tests.fakes import make_sync_coordinator_namespace
from tests.test_discord_notification_retry import poll, prepare
from tests.test_discord_sync_failures import DiscordAPI
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


@pytest.mark.parametrize("race,stale,source,skip_status", [
    (True, True, "cron", 200), (True, False, "webhook", 503), (False, True, "cron", 200),
], ids=["race-stale", "race-fresh", "serial-stale"])
def test_post_lock_cooldown_prevents_duplicate_notification(monkeypatch, race, stale, source, skip_status):
    """ロック取得後は成功時刻を再読込みし、古いsnapshotを読む前に停止する。"""
    api = DiscordAPI(monkeypatch)
    env = api.env
    env.SYNC_COORDINATOR = make_sync_coordinator_namespace()
    env.SYNC_DO_LOCK_ENABLED = "true"
    env.SYNC_DO_LOCK_TTL_SECONDS = "120"
    env.SYNC_ALL_INCLUDE_DISCORD_NOTION = "true"
    env.KV_SYNC_COOLDOWN_ENABLED = "true"
    env.SYNC_INTERVAL_SECONDS = "300"
    env.DISCORD_NOTION_MAX_CHANGES_PER_RUN = "2"
    env.EVENT_CREATE_CHANNEL_ID = "test-channel"
    first_app, second_app = Application(env), Application(env)
    posts, reactions, posting_locks, acquired_owners = [], [], [], []
    stale_reads = []
    original_get = env.STATE_KV.get
    original_acquire = second_app._acquire_sync_lock
    expose_stale_snapshot = False

    async def calendar_token(*args):
        return "test-google-token"

    async def calendar_fetch(*args):
        # Google差分は空、Discordの同一予定1件を毎回正常取得する有限モデル。
        return Response(json.dumps({"items": []}), status=200)

    async def discord_request(_env, method, path, payload=None):
        lock = (await first_app._sync_lock_status())["lock"]
        assert lock["owner"] and lock["now"] < lock["expires_at"]
        if method == "POST" and path == "/channels/test-channel/messages":
            posts.append(deepcopy(payload))
            posting_locks.append(lock)
            return {"id": f"message-{len(posts)}"}, 200
        if method == "PUT" and path.startswith("/channels/test-channel/messages/"):
            reactions.append(path)
            return None, 204
        raise AssertionError((method, path))

    async def snapshot_read(key):
        if expose_stale_snapshot and key == SNAPSHOT:
            stale_reads.append(key)
            return "{}"
        return await original_get(key)

    monkeypatch.setattr(calendar, "get_google_access_token", calendar_token)
    monkeypatch.setattr(calendar, "fetch", calendar_fetch)
    monkeypatch.setattr(sync, "_discord_api_request", discord_request)
    monkeypatch.setattr(env.STATE_KV, "get", snapshot_read)

    async def scenario():
        nonlocal expose_stale_snapshot
        started = asyncio.get_running_loop().time()
        before_acquire, proceed = asyncio.Event(), asyncio.Event()

        async def delayed_acquire(source, **kwargs):
            # 後続がcooldown=falseを取得した後、実DO acquireの直前だけ保留する。
            before_acquire.set()
            await proceed.wait()
            previous = (await second_app._sync_lock_status())["lock"]
            assert not previous.get("owner")
            acquired = await original_acquire(source, **kwargs)
            assert acquired["ok"] and acquired["owner"]
            acquired_owners.append(acquired["owner"])
            return acquired

        monkeypatch.setattr(second_app, "_acquire_sync_lock", delayed_acquire)
        second_task = None
        if race:
            second_task = asyncio.create_task(second_app._run_sync_dispatch(None, StateStore(env), source))
            await asyncio.wait_for(before_acquire.wait(), timeout=5)
        try:
            # 公開Webhookの204受付ではなく、Alarmが呼ぶ通常同期本体を実行する。
            first = await first_app._run_sync_dispatch(None, StateStore(env), "webhook")
            first_body = json.loads(await first.text())
            assert first.status == 200 and first_body["ok"] and len(posts) == 1
            assert "discord-one" in json.loads(env.STATE_KV.data[SNAPSHOT])
            assert json.loads(env.STATE_KV.data[QUEUE]) == []
            # 最終成功時刻は同じDOから最新値を読める。古くするのはsnapshotだけ。
            assert await StateStore(env).should_skip_sync_by_cooldown(300)
            expose_stale_snapshot = stale
            proceed.set()
            if second_task is None:
                second = await second_app._run_sync_dispatch(None, StateStore(env), source)
            else:
                second = await asyncio.wait_for(second_task, timeout=5)
            second_body = json.loads(await second.text())
            assert second.status == skip_status
            assert second_body == {
                "ok": True, "status": "cooldown_skip", "interval_seconds": 300, "source": source,
            }
            assert first_body["google"]["ok"] and first_body["google_apply"]["ok"]
            assert first_body["discord_notion"]["ok"]
            assert first_body["discord_notion"]["error_count"] == 0
            assert first_body["discord_notion"]["errors"] == []
            assert first_body["discord_notion"]["pending_changes"] == 0
            if race:
                assert len(acquired_owners) == 1
                assert acquired_owners[0] != posting_locks[0]["owner"]
            else:
                assert not acquired_owners
            assert not stale_reads
            assert len(posts) == len(reactions) == 1
            assert len({lock["owner"] for lock in posting_locks}) == 1
            assert all(payload == posts[0] for payload in posts)
            assert "https://discord.com/events/test-guild/discord-one" in posts[0]["content"]
            assert len(api.pages) == len(api.google) == 1
            assert json.loads(env.STATE_KV.data[QUEUE]) == []
            assert set(env.SYNC_COORDINATOR.requested_names) == {"global"}
            assert not (await first_app._sync_lock_status())["lock"].get("owner")
            # 時刻操作やlease失効を使わず、300秒のcooldown内に全処理を終える。
            assert asyncio.get_running_loop().time() - started < 60
        finally:
            if second_task is not None and not second_task.done():
                second_task.cancel()
                await asyncio.gather(second_task, return_exceptions=True)

    asyncio.run(scenario())


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
