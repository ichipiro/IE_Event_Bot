"""外部成功後は同じKV値だけを再保存し、外部処理を繰り返さない。"""

import pytest

import state
from state import StateStore
from tests.test_jobs_retry import invoke
from tests.test_e2e_qa_normal import setup as qa_setup, phase as qa_phase
from tests.test_e2e_reminder_normal import setup as reminder_setup, phase as reminder_phase
from tests.test_e2e_notion_cleanup_normal import setup as cleanup_setup, phase as cleanup_phase
from tests.test_e2e_qa_notification_probe import run


CASES = [
    (qa_setup, qa_phase, "qa-check", "qa_cache", "result:job_qa_check"),
    (reminder_setup, reminder_phase, "reminder", "reminder_cache", "result:job_reminder"),
    (cleanup_setup, cleanup_phase, "cleanup", "cleanup:last_epoch", "result:job_cleanup"),
]


@pytest.mark.parametrize("setup,phase,route,cache_key,result_key", CASES)
@pytest.mark.parametrize("target", ["cache", "result"])
@pytest.mark.parametrize("after_write", [False, True])
def test_kv_failure_retries_only_identical_write(monkeypatch, setup, phase, route, cache_key, result_key, target, after_write):
    env, request, data = setup(monkeypatch)
    phase(env, request, "prepare")
    phase(env, request, "verify")
    if route == "qa-check":
        for step in ("first", "update"):
            phase(env, request, step)
            phase(env, request, "verify")
    key = cache_key if target == "cache" else result_key
    original = env.STATE_KV.put
    writes, sleeps = [], []

    async def pause(delay):
        sleeps.append(delay)

    async def failing(write_key, value):
        if write_key != key:
            return await original(write_key, value)
        writes.append(value)
        if len(writes) == 1:
            if after_write:
                await original(write_key, value)
            raise RuntimeError("private upstream detail")
        return await original(write_key, value)

    # asyncioモジュールは既存でもimport可能。修正前も同じ再現テストを実行する。
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", pause)
    monkeypatch.setattr(env.STATE_KV, "put", failing)
    assert invoke(env, request, route)[0] == 200
    assert len(writes) == 2 and writes[0] == writes[1]
    assert sleeps == [1]
    if route != "cleanup":
        ids = set(data[1])
        assert len(ids) == 2
    assert invoke(env, request, route)[0] == 200
    if route != "cleanup":
        assert set(data[1]) == ids
    else:
        assert sum(p["archived"] for p in data[0].values()) == 1
        assert invoke(env, request, route)[1]["reason"] == "interval_guard"
    assert run(StateStore(env).get_text(cache_key)) is not None


@pytest.mark.parametrize("setup,phase,route,cache_key,result_key", CASES)
@pytest.mark.parametrize("target", ["cache", "result"])
def test_exhausted_write_returns_safe_500(monkeypatch, setup, phase, route, cache_key, result_key, target):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    key = cache_key if target == "cache" else result_key
    original = env.STATE_KV.put
    writes, sleeps = [], []

    async def pause(delay):
        sleeps.append(delay)

    async def failing(write_key, value):
        if write_key == key:
            writes.append(value)
            raise RuntimeError("private upstream detail")
        return await original(write_key, value)

    import asyncio
    monkeypatch.setattr(asyncio, "sleep", pause)
    monkeypatch.setattr(env.STATE_KV, "put", failing)
    status, detail = invoke(env, request, route)
    assert status == 500 and detail["ok"] is False
    assert detail["error"] == "job_kv_write_failed"
    assert "private" not in str(detail)
    assert len(writes) == 3 and len(set(writes)) == 1 and sleeps == [1, 2]


def test_unrelated_state_write_is_not_retried(monkeypatch):
    from types import SimpleNamespace
    calls = []

    async def failing(key, value):
        calls.append(key)
        raise RuntimeError("original")

    store = state.StateStore(SimpleNamespace(STATE_KV=SimpleNamespace(put=failing)))
    with pytest.raises(RuntimeError, match="original"):
        run(store.put_text("discord:snapshot", "{}"))
    assert calls == ["discord:snapshot"]


def test_cancelled_write_is_not_retried():
    import asyncio
    from types import SimpleNamespace
    calls = []

    async def cancelled(key, value):
        calls.append(key)
        raise asyncio.CancelledError()

    store = StateStore(SimpleNamespace(STATE_KV=SimpleNamespace(put=cancelled)))
    with pytest.raises(asyncio.CancelledError):
        run(store.put_text("qa_cache", "{}"))
    assert calls == ["qa_cache"]


def test_cron_result_failure_does_not_skip_remaining_jobs(monkeypatch):
    import asyncio
    import entry
    from types import SimpleNamespace
    from tests.fakes import MemoryKV

    env = SimpleNamespace(STATE_KV=MemoryKV(), CRON_ENABLE_SYNC="false")
    calls, writes = [], []

    async def job(env, store, return_detail=False):
        calls.append("job")
        return {"ok": True}

    async def pause(delay):
        pass

    put = env.STATE_KV.put

    async def failing(key, value):
        if key == "result:job_qa_check":
            writes.append(value)
            raise RuntimeError("private detail")
        await put(key, value)

    monkeypatch.setattr(env.STATE_KV, "put", failing)
    monkeypatch.setattr(asyncio, "sleep", pause)
    for name in ("run_qa_notification_job", "run_day_before_reminder_job", "run_auto_clean_job"):
        monkeypatch.setattr(entry, name, job)
    result = run(entry.Application(env).scheduled(None, env, None))
    assert len(calls) == 3 and len(writes) == 3
    assert [r["status"] for r in result] == [500, 200, 200]
    for name in ("job_reminder", "job_cleanup"):
        saved = run(StateStore(env).get_last_result(name))
        assert saved and saved["payload"]["source"] == "cron"
