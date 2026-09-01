"""Discord通知がE2E専用roleだけをmentionすることを検証する。"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord_notion_sync
import jobs


def run(coroutine):
    return asyncio.run(coroutine)


def expected_role_mentions(role_id: str) -> dict[str, object]:
    return {
        "parse": [],
        "roles": [role_id],
        "replied_user": False,
    }


def test_event_created_notification_allows_only_configured_role(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def send_message(env, channel_id, content, allowed_mentions=None):
        captured["channel_id"] = channel_id
        captured["allowed_mentions"] = allowed_mentions
        return "message-e2e"

    async def add_reaction(env, channel_id, message_id, emoji):
        return True

    monkeypatch.setattr(discord_notion_sync, "_discord_send_message", send_message)
    monkeypatch.setattr(discord_notion_sync, "_discord_add_reaction", add_reaction)
    env = SimpleNamespace(
        DISCORD_GUILD_ID="guild-e2e",
        EVENT_CREATE_CHANNEL_ID="channel-e2e",
        EVENT_CREATE_ROLE_ID="role-e2e",
    )
    event = {
        "id": "event-e2e",
        "name": "E2E event",
        "scheduled_start_time": "2030-01-01T00:00:00+00:00",
        "scheduled_end_time": "2030-01-01T01:00:00+00:00",
        "entity_metadata": {"location": "E2E"},
    }

    result = run(discord_notion_sync._notify_discord_event_created(env, event))

    assert result is True
    assert captured["channel_id"] == "channel-e2e"
    assert captured["allowed_mentions"] == expected_role_mentions("role-e2e")


def test_reminder_notification_allows_only_configured_role(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 1, 1, tzinfo=timezone.utc)

    class DisabledState:
        def enabled(self) -> bool:
            return False

    captured: dict[str, object] = {}

    async def list_events(env):
        return [
            {
                "id": "event-e2e",
                "name": "E2E reminder",
                "scheduled_start_time": "2030-01-02T00:05:00+00:00",
                "entity_metadata": {"location": "E2E"},
            }
        ]

    async def send_message(env, channel_id, content, allowed_mentions=None):
        captured["channel_id"] = channel_id
        captured["allowed_mentions"] = allowed_mentions
        return True

    monkeypatch.setattr(jobs, "datetime", FixedDateTime)
    monkeypatch.setattr(jobs, "_list_discord_events", list_events)
    monkeypatch.setattr(jobs, "_discord_send_message", send_message)
    env = SimpleNamespace(
        DISCORD_GUILD_ID="guild-e2e",
        REMINDER_CHANNEL_ID="channel-e2e",
        REMINDER_ROLE_ID="role-e2e",
        REMINDER_WINDOW_MINUTES="15",
    )

    result = run(jobs.run_day_before_reminder_job(env, DisabledState()))

    assert result is True
    assert captured["channel_id"] == "channel-e2e"
    assert captured["allowed_mentions"] == expected_role_mentions("role-e2e")
