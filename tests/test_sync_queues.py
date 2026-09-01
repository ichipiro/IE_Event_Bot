"""同期件数上限と失敗時のキュー繰り越しを検証する。"""

import asyncio
from types import SimpleNamespace

import discord_notion_sync
import google_apply_sync
from state import StateStore
from tests.fakes import MemoryKV


def run(coroutine):
    return asyncio.run(coroutine)


def test_google_apply_retries_failure_before_remaining_events(monkeypatch) -> None:
    async def fail_query(env, database_id, google_event_id):
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(
        google_apply_sync,
        "_notion_query_by_google_event_id",
        fail_query,
    )
    kv = MemoryKV()
    env = SimpleNamespace(
        STATE_KV=kv,
        NOTION_TOKEN="test-notion-token",
        NOTION_EVENT_INTERNAL_ID="test-database",
        GOOGLE_APPLY_MAX_EVENTS_PER_RUN="1",
    )
    store = StateStore(env)
    events = [{"id": "google-1"}, {"id": "google-2"}]

    result = run(google_apply_sync.apply_google_events(env, store, events))
    queue = run(store.get_json("sync:google_apply_queue"))

    assert result["ok"] is False
    assert result["processed"] == 1
    assert result["pending_events"] == 2
    assert isinstance(queue, list)
    assert [event["id"] for event in queue] == ["google-1", "google-2"]


def test_google_apply_processes_saved_queue_on_next_run(monkeypatch) -> None:
    async def no_page(env, database_id, google_event_id):
        return None

    monkeypatch.setattr(
        google_apply_sync,
        "_notion_query_by_google_event_id",
        no_page,
    )
    kv = MemoryKV()
    env = SimpleNamespace(
        STATE_KV=kv,
        NOTION_TOKEN="test-notion-token",
        NOTION_EVENT_INTERNAL_ID="test-database",
        GOOGLE_APPLY_MAX_EVENTS_PER_RUN="1",
    )
    store = StateStore(env)

    first = run(
        google_apply_sync.apply_google_events(
            env,
            store,
            [{"id": "google-1"}, {"id": "google-2"}],
        )
    )
    second = run(google_apply_sync.apply_google_events(env, store, []))

    assert first["pending_events"] == 1
    assert second["processed"] == 1
    assert second["pending_events"] == 0
    assert run(store.get_json("sync:google_apply_queue")) == []


def test_discord_sync_retries_failure_before_remaining_changes(monkeypatch) -> None:
    events = [{"id": "discord-1"}, {"id": "discord-2"}]

    async def list_events(env):
        return events, None

    async def fail_upsert(env, event, google_token):
        return False

    monkeypatch.setattr(discord_notion_sync, "_list_discord_scheduled_events", list_events)
    monkeypatch.setattr(discord_notion_sync, "_sync_discord_event_upsert", fail_upsert)
    kv = MemoryKV()
    env = SimpleNamespace(
        STATE_KV=kv,
        DISCORD_TO_GOOGLE_SYNC_ENABLED="false",
        DISCORD_NOTION_MAX_CHANGES_PER_RUN="1",
    )
    store = StateStore(env)

    result = run(discord_notion_sync.run_discord_notion_poll_sync(env, store))
    queue = run(store.get_json("sync:discord_notion_queue"))

    assert result["ok"] is False
    assert result["processed_changes"] == 1
    assert result["pending_changes"] == 2
    assert queue == [
        {"op": "upsert", "id": "discord-1"},
        {"op": "upsert", "id": "discord-2"},
    ]
