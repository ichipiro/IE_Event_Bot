"""queueが古くてもsnapshotに残した未処理操作を復元する。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

import discord_notion_sync as sync
from state import StateStore
from tests.fakes import MemoryKV
from tests.test_discord_notification_retry import poll, prepare

QUEUE = "sync:discord_notion_queue"


class StaleKV(MemoryKV):
    stale_queue = False

    async def get(self, key):
        if self.stale_queue and key == QUEUE:
            return "[]"
        return await super().get(key)


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_failed_operation_survives_stale_queue(operation):
    async def scenario():
        kv = StaleKV()
        env = SimpleNamespace(
            STATE_KV=kv,
            DISCORD_TO_GOOGLE_SYNC_ENABLED="false",
            DISCORD_NOTION_MAX_CHANGES_PER_RUN="1",
        )
        old = {"id": "first", "name": "before", "status": 1}
        new = {**old, "name": "after"}
        extra = {"id": "second", "status": 1}
        if operation != "create":
            await StateStore(env).set_discord_snapshot(
                {"first": sync._fingerprint(old)}
            )
        events = [] if operation == "delete" else [new]
        fail = True
        calls = []

        async def apply(env, event, token):
            calls.append(event["id"] if isinstance(event, dict) else event)
            return not fail and not (kv.stale_queue and calls[-1] == "second")

        async def step(items):
            return await sync._apply_discord_event_diff(
                env, StateStore(env), items, upsert_runner=apply, delete_runner=apply
            )

        assert (await step(events))["pending_changes"] == 1
        kv.stale_queue = True
        fail = False
        await step([*events, extra])
        kv.stale_queue = False
        for _ in range(3):
            await step([*events, extra])
        assert calls.count("first") == 2, calls
        assert calls.count("second") == 1, calls
        assert await StateStore(env).get_json(QUEUE) == []

    asyncio.run(scenario())


def test_limit_remainder_survives_stale_queue():
    async def scenario():
        kv = StaleKV()
        env = SimpleNamespace(
            STATE_KV=kv,
            DISCORD_TO_GOOGLE_SYNC_ENABLED="false",
            DISCORD_NOTION_MAX_CHANGES_PER_RUN="1",
        )
        events = [{"id": str(i), "status": 1} for i in range(3)]
        calls = []

        async def apply(env, event, token):
            calls.append(event["id"])
            return not (kv.stale_queue and event["id"] == "2")

        async def step(items):
            return await sync._apply_discord_event_diff(
                env, StateStore(env), items, upsert_runner=apply
            )

        assert (await step(events[:2]))["pending_changes"] == 1
        kv.stale_queue = True
        await step(events)
        kv.stale_queue = False
        await step(events)
        assert calls == ["0", "1", "2"]

    asyncio.run(scenario())


def test_notification_message_survives_stale_queue(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch)
    failures["reaction"] = 1
    assert poll(env)["pending_changes"] == 1
    original_get = env.STATE_KV.get

    async def stale(key):
        return "[]" if key == QUEUE else await original_get(key)

    monkeypatch.setattr(env.STATE_KV, "get", stale)
    assert poll(env)["ok"] is True
    assert len(calls["reaction"]) == 2
    assert calls["reaction"][0] == calls["reaction"][1]
    assert len(calls["post"]) == 1 and calls["upsert"] == ["event-0"]
    monkeypatch.setattr(env.STATE_KV, "get", original_get)
    # 古いqueueが一時的に残っていれば再試行し得るが、再投稿しない。
    poll(env)
    assert len(calls["post"]) == 1


def test_notification_recovers_from_stale_snapshot(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch)
    failures["post"] = 1
    poll(env)
    previous = deepcopy(env.STATE_KV.data["discord:snapshot"])
    failures["reaction"] = 1
    poll(env)
    original_get = env.STATE_KV.get

    async def stale(key):
        return previous if key == "discord:snapshot" else await original_get(key)

    monkeypatch.setattr(env.STATE_KV, "get", stale)
    assert poll(env)["ok"] is True
    assert len(calls["post"]) == 2  # 失敗した初回と成功した2回目だけ。
    assert len(calls["reaction"]) == 2
    assert calls["reaction"][0] == calls["reaction"][1]


@pytest.mark.parametrize(
    "mutation", ["foreign_id", "unknown_op", "extra_field", "bad_value"]
)
def test_corrupt_snapshot_retry_stops_before_external_apply(mutation):
    import json
    from discord_retry_state import PENDING_FIELD

    async def scenario():
        event = {"id": "first", "status": 1}
        fingerprint = sync._fingerprint(event)
        assert isinstance(fingerprint, str)
        value = json.loads(fingerprint)
        op = {"id": "first", "op": "upsert"}
        if mutation == "foreign_id":
            op["id"] = "other"
        elif mutation == "unknown_op":
            op["op"] = "unknown"
        elif mutation == "extra_field":
            op["arbitrary"] = "forbidden"
        value[PENDING_FIELD] = [] if mutation == "bad_value" else op
        kv = MemoryKV({"discord:snapshot": json.dumps({"first": json.dumps(value)})})
        env = SimpleNamespace(STATE_KV=kv, DISCORD_TO_GOOGLE_SYNC_ENABLED="false")

        async def forbidden(*args):
            pytest.fail("破損した残件情報では外部適用を開始しない")

        with pytest.raises(RuntimeError, match="discord_snapshot_pending_invalid"):
            await sync._apply_discord_event_diff(
                env, StateStore(env), [event], upsert_runner=forbidden
            )
        assert kv.put_calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["channel_id", "message_id"])
def test_conflicting_notification_targets_fail_closed(field):
    from discord_retry_state import merge_retry_ops

    op = {
        "id": "first",
        "op": "notify",
        "notification": {"channel_id": "channel", "message_id": "message"},
    }
    other = deepcopy(op)
    other["notification"][field] = "other"
    with pytest.raises(RuntimeError, match="discord_notification_state_conflict"):
        merge_retry_ops([op], [other])


def test_visible_queue_keeps_retry_priority_and_snapshot_fills_missing():
    from discord_retry_state import merge_retry_ops

    queue = [{"op": "upsert", "id": "retry"}, {"op": "upsert", "id": "later"}]
    snapshot = [queue[1], queue[0], {"op": "delete", "id": "missing"}]
    assert merge_retry_ops(snapshot, queue) == [
        *queue,
        {"op": "delete", "id": "missing"},
    ]
