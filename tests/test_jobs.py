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


def cleanup_page(page_id: str, *, start: str | None, end: str | None = None) -> dict:
    date = None if start is None else {"start": start, "end": end}
    return {
        "id": page_id,
        "properties": {
            "日時": {"date": date},
        },
    }


def test_auto_clean_page_processor_archives_only_due_page_and_guards_second_run() -> None:
    now_utc = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
    archived_page_ids: list[str] = []

    async def archive_page(_env, page_id: str) -> bool:
        archived_page_ids.append(page_id)
        return True

    env = SimpleNamespace(
        CLEANUP_INTERVAL_SECONDS="300",
        STATE_KV=MemoryKV(),
    )
    state = StateStore(env)
    pages = [
        cleanup_page(
            "due-page-id",
            start=(now_utc - timedelta(hours=1)).isoformat(),
            end=now_utc.isoformat(),
        ),
        cleanup_page(
            "future-page-id",
            start=(now_utc + timedelta(days=1)).isoformat(),
        ),
        cleanup_page("invalid-page-id", start=None),
    ]

    first = run(
        jobs._run_auto_clean_pages(
            env,
            state,
            pages,
            now_utc=now_utc,
            return_detail=True,
            archive_page=archive_page,
        )
    )
    second = run(
        jobs._run_auto_clean_pages(
            env,
            state,
            pages,
            now_utc=now_utc,
            return_detail=True,
            archive_page=archive_page,
        )
    )

    assert first == {
        "ok": True,
        "scanned": 3,
        "archived": 1,
    }
    assert second == {
        "ok": True,
        "skipped": True,
        "reason": "interval_guard",
    }
    assert archived_page_ids == ["due-page-id"]
    assert env.STATE_KV.data["cleanup:last_epoch"] == str(now_utc.timestamp())


def test_auto_clean_job_checks_interval_before_listing_pages(monkeypatch) -> None:
    now_utc = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)

    async def fail_if_listed(_env, _database_id):
        raise AssertionError("interval guard後にだけ一覧を取得する必要がある")

    monkeypatch.setattr(jobs, "_utc_now", lambda: now_utc)
    monkeypatch.setattr(jobs, "_notion_query_all_pages", fail_if_listed)
    env = SimpleNamespace(
        NOTION_EVENT_INTERNAL_ID="internal-database-id",
        CLEANUP_INTERVAL_SECONDS="300",
        STATE_KV=MemoryKV(
            {"cleanup:last_epoch": str(now_utc.timestamp())}
        ),
    )

    result = run(
        jobs.run_auto_clean_job(
            env,
            StateStore(env),
            return_detail=True,
        )
    )

    assert result == {
        "ok": True,
        "skipped": True,
        "reason": "interval_guard",
    }
