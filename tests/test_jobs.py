"""定期ジョブの通知判定を外部通信なしで検証する。"""

import asyncio
import json
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
