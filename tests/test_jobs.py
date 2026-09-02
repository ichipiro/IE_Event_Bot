"""定期ジョブの通知判定を外部通信なしで検証する。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from workers import Response

import jobs
from state import StateStore
from tests.fakes import MemoryKV


def run(coroutine):
    return asyncio.run(coroutine)


def qa_page(*, edited_at: str, question: str, answer: str = "") -> dict:
    return {
        "id": "qa-page-id",
        "last_edited_time": edited_at,
        "properties": {
            "質問": {"title": [{"plain_text": question}]},
            "回答": {
                "rich_text": [] if not answer else [{"plain_text": answer}]
            },
            "質問番号": {"number": 7},
        },
    }


def test_qa_page_processor_seeds_then_notifies_only_updated_unanswered_page(
    monkeypatch,
) -> None:
    messages: list[dict] = []

    async def fake_fetch(_url, options=None):
        messages.append(json.loads(str((options or {}).get("body") or "{}")))
        return Response(json.dumps({"id": "message-id"}), status=200)

    monkeypatch.setattr(jobs, "fetch", fake_fetch)
    env = SimpleNamespace(
        DISCORD_TOKEN="bot-token",
        QA_CHANNEL_ID="qa-channel-id",
        STATE_KV=MemoryKV(),
    )
    state = StateStore(env)

    first = run(
        jobs._run_qa_notification_pages(
            env,
            state,
            [
                qa_page(
                    edited_at="2026-09-02T00:00:00.000Z",
                    question="initial question",
                )
            ],
            return_detail=True,
        )
    )
    second = run(
        jobs._run_qa_notification_pages(
            env,
            state,
            [
                qa_page(
                    edited_at="2026-09-02T00:00:01.000Z",
                    question="updated question",
                )
            ],
            return_detail=True,
        )
    )

    assert first == {
        "ok": True,
        "first_run": True,
        "failed_count": 0,
        "failed_page_ids": [],
    }
    assert second == {
        "ok": True,
        "first_run": False,
        "failed_count": 0,
        "failed_page_ids": [],
    }
    assert messages == [
        {
            "content": (
                "❓ 質問番号 #7 に更新があります\n"
                "質問: updated question\n"
                "回答: (回答なし)"
            )
        }
    ]
    assert json.loads(env.STATE_KV.data["qa_cache"]) == {
        "_first_qa_run": False,
        "qa-page-id": "2026-09-02T00:00:01.000Z",
    }


def test_qa_page_processor_does_not_notify_answered_page(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch(url, _options=None):
        calls.append(url)
        return Response("{}", status=200)

    monkeypatch.setattr(jobs, "fetch", fake_fetch)
    env = SimpleNamespace(
        DISCORD_TOKEN="bot-token",
        QA_CHANNEL_ID="qa-channel-id",
        STATE_KV=MemoryKV(
            {
                "qa_cache": json.dumps(
                    {
                        "_first_qa_run": False,
                        "qa-page-id": "2026-09-02T00:00:00.000Z",
                    }
                )
            }
        ),
    )

    result = run(
        jobs._run_qa_notification_pages(
            env,
            StateStore(env),
            [
                qa_page(
                    edited_at="2026-09-02T00:00:01.000Z",
                    question="answered question",
                    answer="answered",
                )
            ],
            return_detail=True,
        )
    )

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["first_run"] is False
    assert calls == []


def test_reminder_event_processor_notifies_once_and_updates_only_local_cache() -> None:
    now_utc = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
    event_start = now_utc + timedelta(hours=24, minutes=5)
    event = {
        "id": "event-owned-by-run",
        "name": "[E2E] reminder [ie-event-bot-e2e:test-run]",
        "scheduled_start_time": event_start.isoformat(),
        "entity_metadata": {"location": "E2E reminder location"},
    }
    sent: list[dict] = []

    async def send_message(env, channel_id, content, allowed_mentions=None):
        sent.append(
            {
                "channel_id": channel_id,
                "content": content,
                "allowed_mentions": allowed_mentions,
            }
        )
        return True

    env = SimpleNamespace(
        DISCORD_GUILD_ID="guild-id",
        REMINDER_CHANNEL_ID="reminder-channel-id",
        REMINDER_ROLE_ID="reminder-role-id",
        REMINDER_WINDOW_MINUTES="15",
        STATE_KV=MemoryKV(),
    )
    state = StateStore(env)

    first = run(
        jobs._run_reminder_events(
            env,
            state,
            [event],
            now_utc=now_utc,
            return_detail=True,
            send_message=send_message,
        )
    )
    second = run(
        jobs._run_reminder_events(
            env,
            state,
            [event],
            now_utc=now_utc,
            return_detail=True,
            send_message=send_message,
        )
    )

    expected_detail = {
        "ok": True,
        "failed_count": 0,
        "failed_event_ids": [],
    }
    assert first == expected_detail
    assert second == expected_detail
    assert sent == [
        {
            "channel_id": "reminder-channel-id",
            "content": (
                "<@&reminder-role-id>\n"
                "🔔 明日開催のイベントがあります\n"
                "イベント名: [E2E] reminder [ie-event-bot-e2e:test-run]\n"
                "開始日時: 2026年9月3日 木曜日 15:05\n"
                "場所: E2E reminder location\n"
                "https://discord.com/events/guild-id/event-owned-by-run"
            ),
            "allowed_mentions": {
                "parse": [],
                "roles": ["reminder-role-id"],
                "replied_user": False,
            },
        }
    ]
    assert json.loads(env.STATE_KV.data["reminder_cache"]) == {
        "event-owned-by-run": now_utc.isoformat()
    }


def test_reminder_event_processor_ignores_events_outside_window() -> None:
    now_utc = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
    calls: list[str] = []

    async def send_message(env, channel_id, content, allowed_mentions=None):
        calls.append(content)
        return True

    env = SimpleNamespace(
        DISCORD_GUILD_ID="guild-id",
        REMINDER_CHANNEL_ID="reminder-channel-id",
        REMINDER_ROLE_ID="reminder-role-id",
        REMINDER_WINDOW_MINUTES="15",
        STATE_KV=MemoryKV(),
    )
    events = [
        {
            "id": "too-early",
            "scheduled_start_time": (now_utc + timedelta(hours=23, minutes=59)).isoformat(),
        },
        {
            "id": "upper-bound",
            "scheduled_start_time": (now_utc + timedelta(hours=24, minutes=15)).isoformat(),
        },
        {"id": "invalid", "scheduled_start_time": "not-a-timestamp"},
    ]

    result = run(
        jobs._run_reminder_events(
            env,
            StateStore(env),
            events,
            now_utc=now_utc,
            return_detail=True,
            send_message=send_message,
        )
    )

    assert result == {
        "ok": True,
        "failed_count": 0,
        "failed_event_ids": [],
    }
    assert calls == []
    assert "reminder_cache" not in env.STATE_KV.data
