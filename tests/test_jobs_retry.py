"""失敗対象だけを別HTTPで再試行し、成功済み処理を重複させない。"""

import json

import jobs
from entry import Application
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_qa_normal import setup as qa_setup, phase as qa_phase
from tests.test_e2e_notion_cleanup_normal import setup as cleanup_setup, phase as cleanup_phase
from tests.test_e2e_qa_notification_probe import run


def invoke(env, request, path):
    response = run(Application(env).fetch(Request(
        f"https://e2e.invalid/jobs/{path}", method="POST", headers={"Authorization": request.headers.get("Authorization")},
    )))
    return response.status, json.loads(run(response.text()))


def test_qa_failed_notification_is_retried_without_repeating_success(monkeypatch):
    env, request, (_, messages, _) = qa_setup(monkeypatch)
    for phase in ("prepare", "first", "update"):
        qa_phase(env, request, phase)
        qa_phase(env, request, "verify")
    original = jobs._discord_send_message
    calls = []

    async def fail_once(*args, **kwargs):
        calls.append(args[2])
        return False if len(calls) == 1 else await original(*args, **kwargs)

    monkeypatch.setattr(jobs, "_discord_send_message", fail_once)
    status, detail = invoke(env, request, "qa-check")
    assert status == 500 and detail["failed_count"] == 1 and len(messages) == 1
    failed = detail["failed_page_ids"][0]
    cache = run(StateStore(env).get_json("qa_cache", {}))
    owner = run(StateStore(env).get_e2e_manifest("qa_notification"))
    assert isinstance(cache, dict)
    assert owner and cache[failed] == owner["cache"][failed]
    assert invoke(env, request, "qa-check")[0] == 200
    assert len(messages) == 2 and len(calls) == 3
    assert invoke(env, request, "qa-check")[0] == 200
    assert len(messages) == 2 and len(calls) == 3


def test_cleanup_failure_does_not_advance_success_epoch(monkeypatch):
    env, request, (pages, _, _) = cleanup_setup(monkeypatch)
    cleanup_phase(env, request, "prepare")
    original = jobs._notion_archive_page
    calls = []

    async def fail_once(*args):
        calls.append(args[1])
        return False if len(calls) == 1 else await original(*args)

    monkeypatch.setattr(jobs, "_notion_archive_page", fail_once)
    status, detail = invoke(env, request, "cleanup")
    assert status == 500 and detail["archived"] == 0
    assert run(StateStore(env).get_text("cleanup:last_epoch")) is None
    status, detail = invoke(env, request, "cleanup")
    assert status == 200 and detail["archived"] == 1
    assert sum(p["archived"] for p in pages.values()) == 1
    assert invoke(env, request, "cleanup")[1]["reason"] == "interval_guard"
    assert len(calls) == 2
