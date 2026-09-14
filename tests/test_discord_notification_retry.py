"""通常ポーリングの通知繰越を、外部APIを代替して検証する。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

import discord_notion_sync as sync
from state import StateStore
from tests.fakes import MemoryKV

QUEUE = "sync:discord_notion_queue"


def run(coro):
    return asyncio.run(coro)


def prepare(monkeypatch, *, count=1, limit=5):
    env = SimpleNamespace(
        STATE_KV=MemoryKV(), DISCORD_TO_GOOGLE_SYNC_ENABLED="false",
        DISCORD_NOTION_MAX_CHANGES_PER_RUN=str(limit),
        DISCORD_GUILD_ID="guild", EVENT_CREATE_CHANNEL_ID="channel",
        EVENT_CREATE_ROLE_ID="role",
    )
    events = [{"id": f"event-{index}", "name": f"イベント{index}", "status": 1}
              for index in range(count)]
    calls = {"upsert": [], "delete": [], "post": [], "reaction": []}
    failures = {"upsert": 0, "post": 0, "reaction": 0}

    def succeeds(operation):
        if failures[operation]:
            failures[operation] -= 1
            return False
        return True

    async def list_events(_env):
        return deepcopy(events), None

    async def upsert(_env, event, token):
        calls["upsert"].append(event["id"])
        return succeeds("upsert")

    async def delete(_env, event_id, token):
        calls["delete"].append(event_id)
        return True

    async def discord_api(_env, method, path, payload=None):
        if method == "POST" and path == "/channels/channel/messages":
            calls["post"].append(deepcopy(payload))
            if succeeds("post"):
                return {"id": f"message-{len(calls['post'])}"}, 200
            return None, 500
        if method == "PUT" and path.startswith("/channels/channel/messages/"):
            calls["reaction"].append(path)
            return None, 204 if succeeds("reaction") else 500
        raise AssertionError((method, path))

    monkeypatch.setattr(sync, "_list_discord_scheduled_events", list_events)
    monkeypatch.setattr(sync, "_sync_discord_event_upsert", upsert)
    monkeypatch.setattr(sync, "_sync_discord_event_delete", delete)
    monkeypatch.setattr(sync, "_discord_api_request", discord_api)
    return env, events, calls, failures


def poll(env):
    # 各回でStateStoreを作り直し、保存された状態から再試行する。
    return run(sync.run_discord_notion_poll_sync(env, StateStore(env)))


def test_limit_remainder_keeps_creation_notification(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch, count=2, limit=1)
    assert poll(env)["pending_changes"] == 1
    second = poll(env)
    assert second["created"] == 0 and second["pending_changes"] == 0
    assert calls["upsert"] == ["event-0", "event-1"]
    assert len(calls["post"]) == 2
    assert "イベント1" in calls["post"][1]["content"]
    assert poll(env)["processed_changes"] == 0


def test_failed_upsert_keeps_creation_notification(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch)
    failures["upsert"] = 1
    assert poll(env)["pending_changes"] == 1
    assert not calls["post"]
    assert poll(env)["ok"] is True
    assert len(calls["post"]) == 1
    assert calls["upsert"] == ["event-0", "event-0"]


def test_failed_post_retries_notification_without_reapplying(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch)
    failures["post"] = 1
    first = poll(env)
    assert first["ok"] is False and first["pending_changes"] == 1
    assert first["errors"] == ["create_notify_failed:event-0"]
    assert poll(env)["ok"] is True
    assert calls["upsert"] == ["event-0"]
    assert len(calls["post"]) == 2 and len(calls["reaction"]) == 1
    assert poll(env)["processed_changes"] == 0


def test_failed_reaction_retries_saved_message_without_reposting(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch)
    failures["reaction"] = 1
    first = poll(env)
    assert first["ok"] is False and first["pending_changes"] == 1
    assert poll(env)["ok"] is True
    assert calls["upsert"] == ["event-0"] and len(calls["post"]) == 1
    assert len(calls["reaction"]) == 2
    assert calls["reaction"][0] == calls["reaction"][1]
    assert run(StateStore(env).get_json(QUEUE)) == []


@pytest.mark.parametrize("failure", ["upsert", "post", "reaction"])
def test_removed_event_drops_pending_notification(monkeypatch, failure):
    env, events, calls, failures = prepare(monkeypatch)
    failures[failure] = 1
    poll(env)
    previous_posts = len(calls["post"])
    previous_reactions = len(calls["reaction"])
    events.clear()
    result = poll(env)
    assert result["ok"] is True and result["pending_changes"] == 0
    assert calls["delete"] == ["event-0"]
    assert len(calls["post"]) == previous_posts
    assert len(calls["reaction"]) == previous_reactions


def test_completed_event_disappearing_does_not_turn_notification_into_delete(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch)
    events[0]["status"] = 3
    failures["post"] = 1
    assert poll(env)["pending_changes"] == 1
    events.clear()
    result = poll(env)
    assert result["ok"] is True and result["pending_changes"] == 0
    assert not calls["delete"]
    assert len(calls["post"]) == 1


@pytest.mark.parametrize("failure", ["post", "reaction"])
def test_updated_event_applies_change_before_retrying_notification(monkeypatch, failure):
    env, events, calls, failures = prepare(monkeypatch)
    failures[failure] = 1
    poll(env)
    events[0]["name"] = "変更後のイベント"
    failures["upsert"] = 1
    second = poll(env)
    assert second["updated"] == 1 and second["pending_changes"] == 1
    assert len(calls["post"]) == 1
    assert poll(env)["ok"] is True
    assert len(calls["upsert"]) == 3
    if failure == "post":
        assert len(calls["post"]) == 2
        assert "変更後のイベント" in calls["post"][-1]["content"]
    else:
        assert len(calls["post"]) == 1
        assert calls["reaction"][0] == calls["reaction"][1]


def test_notification_retry_counts_toward_limit(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch, count=2, limit=1)
    failures["post"] = 2
    first = poll(env)
    assert first["pending_changes"] == 2
    second = poll(env)
    assert second["processed_changes"] == 1 and second["pending_changes"] == 2
    assert calls["upsert"] == ["event-0"]
    third = poll(env)
    assert third["processed_changes"] == 1 and third["pending_changes"] == 1
    assert poll(env)["pending_changes"] == 0
    assert calls["upsert"] == ["event-0", "event-1"]
    assert len(calls["post"]) == 4
    assert len(calls["reaction"]) == 2
    for message in calls["post"]:
        assert message["allowed_mentions"] == {
            "parse": [], "roles": ["role"], "replied_user": False,
        }


@pytest.mark.parametrize("channel", ["", "different-channel"])
@pytest.mark.parametrize("failure", ["post", "reaction"])
def test_changed_channel_preserves_pending_delivery_without_retargeting(monkeypatch, channel, failure):
    env, events, calls, failures = prepare(monkeypatch)
    failures[failure] = 1
    poll(env)
    saved_queue = run(StateStore(env).get_json(QUEUE))
    saved_calls = deepcopy(calls)
    env.EVENT_CREATE_CHANNEL_ID = channel
    result = poll(env)
    assert result["ok"] is False and result["pending_changes"] == 1
    assert calls == saved_calls
    assert run(StateStore(env).get_json(QUEUE)) == saved_queue
    env.EVENT_CREATE_CHANNEL_ID = "channel"
    assert poll(env)["pending_changes"] == 0


def test_disabled_notification_keeps_legacy_queue_shape(monkeypatch):
    env, events, calls, failures = prepare(monkeypatch, count=2, limit=1)
    env.EVENT_CREATE_CHANNEL_ID = ""
    assert poll(env)["pending_changes"] == 1
    assert run(StateStore(env).get_json(QUEUE)) == [{"id": "event-1", "op": "upsert"}]
    assert poll(env)["pending_changes"] == 0
    assert not calls["post"] and not calls["reaction"]


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_legacy_queue_remains_readable_without_inventing_creation_notice(monkeypatch, operation):
    env, events, calls, failures = prepare(monkeypatch)
    run(StateStore(env).set_discord_snapshot({"event-0": sync._fingerprint(events[0])}))
    if operation == "delete":
        events.clear()
    run(StateStore(env).put_json(QUEUE, [{"id": "event-0", "op": operation}]))
    assert poll(env)["pending_changes"] == 0
    assert calls[operation] == ["event-0"]
    assert not calls["post"]


@pytest.mark.parametrize("notification", [None, [], {}, {"channel_id": "channel", "message_id": []}])
def test_invalid_notification_state_stops_before_external_writes(monkeypatch, notification):
    env, events, calls, failures = prepare(monkeypatch)
    run(StateStore(env).set_discord_snapshot({"event-0": sync._fingerprint(events[0])}))
    queue = [{"id": "event-0", "op": "notify", "notification": notification}]
    run(StateStore(env).put_json(QUEUE, queue))
    with pytest.raises(RuntimeError, match="discord_notification_state_invalid"):
        poll(env)
    assert all(not values for values in calls.values())
    assert run(StateStore(env).get_json(QUEUE)) == queue
