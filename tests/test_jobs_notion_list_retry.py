"""一覧の途中失敗を成功扱いせず、次HTTPで先頭から再取得する。"""

import copy
import json
from types import SimpleNamespace

import pytest
from workers import Response

import jobs
from state import StateStore
from tests.test_jobs_retry import invoke
from tests.test_e2e_qa_normal import setup as qa_setup, phase as qa_phase
from tests.test_e2e_notion_cleanup_normal import setup as cleanup_setup, phase as cleanup_phase
from tests.test_e2e_qa_notification_probe import run


@pytest.mark.parametrize("kind", ["qa_first", "qa_notify", "cleanup"])
@pytest.mark.parametrize("offset", [0, 1])
def test_list_failure_preserves_state_and_retry_recovers(monkeypatch, kind, offset):
    setup, phase = (cleanup_setup, cleanup_phase) if kind == "cleanup" else (qa_setup, qa_phase)
    env, request, data = setup(monkeypatch)
    phase(env, request, "prepare")
    phase(env, request, "verify")
    if kind == "qa_notify":
        for step in ("first", "update"):
            phase(env, request, step)
            phase(env, request, "verify")
    store = StateStore(env)
    key = "cleanup:last_epoch" if kind == "cleanup" else "qa_cache"
    before = run(store.get_text(key))
    pages_before = copy.deepcopy(data[0])
    original = jobs.fetch
    requests = []
    scan = [0]

    async def failing(url, options=None):
        if url.endswith("/query"):
            body = json.loads((options or {}).get("body") or "{}")
            requests.append(body)
            if not body.get("start_cursor"):
                scan[0] += 1
            # 通知用の2回目の全件取得にも失敗を注入する。
            target = scan[0] == (2 if kind == "qa_notify" else 1)
            if target:
                if offset == 1 and not body.get("start_cursor"):
                    return Response(json.dumps({"results": [next(iter(data[0].values()))],
                                                "has_more": True, "next_cursor": "next"}))
                return Response('{"message":"private upstream detail"}', status=503)
        return await original(url, options)

    monkeypatch.setattr(jobs, "fetch", failing)
    route = "cleanup" if kind == "cleanup" else "qa-check"
    status, detail = invoke(env, request, route)
    assert status == 500
    assert detail == {"mode": "native", "ok": False, "error": "notion_query_failed_503"}
    assert run(store.get_text(key)) == before
    assert data[0] == pages_before
    if kind != "cleanup":
        assert not data[1]
    result = run(store.get_last_result("job_cleanup" if kind == "cleanup" else "job_qa_check"))
    assert result and result["payload"] == detail
    monkeypatch.setattr(jobs, "fetch", original)
    assert invoke(env, request, route)[0] == 200
    assert invoke(env, request, route)[0] == 200
    if kind == "cleanup":
        assert sum(p["archived"] for p in data[0].values()) == 1
    else:
        assert len(data[1]) == (2 if kind == "qa_notify" else 0)
        assert {jobs._extract_number(p, "質問番号") for p in data[0].values()} == {41, 42, 43}


@pytest.mark.parametrize("payload", [None, [], {}, {"results": {}},
    {"results": [], "has_more": "false"}, {"results": [None], "has_more": False},
    {"results": [], "has_more": True},
    {"results": [], "has_more": True, "next_cursor": 1},
    {"results": [], "has_more": True, "next_cursor": "cycle"}])
def test_invalid_or_cyclic_list_is_rejected(monkeypatch, payload):
    async def fetch(*args):
        return Response(json.dumps(payload))
    monkeypatch.setattr(jobs, "fetch", fetch)
    with pytest.raises(RuntimeError, match="notion_query_invalid_response"):
        run(jobs._notion_query_all_pages(SimpleNamespace(NOTION_TOKEN="test"), "db"))


@pytest.mark.parametrize("failure", ["fetch", "body", "json", "missing_token"])
@pytest.mark.parametrize("detail", [False, True])
def test_list_errors_are_safe_and_do_not_advance_cleanup(monkeypatch, failure, detail):
    from tests.fakes import MemoryKV

    class BrokenBody:
        status = 200

        async def text(self):
            raise OSError("private response body")

    async def fetch(*args):
        if failure == "fetch":
            raise OSError("private request detail")
        return BrokenBody() if failure == "body" else Response("invalid json")

    env = SimpleNamespace(NOTION_EVENT_INTERNAL_ID="db", NOTION_TOKEN="test", STATE_KV=MemoryKV())
    if failure == "missing_token":
        env.NOTION_TOKEN = ""
    state = StateStore(env)
    run(state.put_text("cleanup:last_epoch", "1"))
    monkeypatch.setattr(jobs, "fetch", fetch)
    result = run(jobs.run_auto_clean_job(env, state, return_detail=detail))
    if detail:
        assert isinstance(result, dict)
        assert result["ok"] is False and "private" not in json.dumps(result)
    else:
        assert result is False
    assert run(state.get_text("cleanup:last_epoch")) == "1"


def test_successful_empty_list_remains_valid(monkeypatch):
    async def fetch(*args):
        return Response('{"results": [], "has_more": false, "next_cursor": null}')
    monkeypatch.setattr(jobs, "fetch", fetch)
    assert run(jobs._notion_query_all_pages(SimpleNamespace(NOTION_TOKEN="test"), "db")) == []
