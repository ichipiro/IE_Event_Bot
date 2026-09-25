"""通常HTTPでKV保存前後の例外を回復し、別HTTP検証・回収まで通す。"""

import pytest

import e2e_entry
from state import StateStore
from tests.test_e2e_jobs_retry import CASES
from tests.test_e2e_qa_notification_probe import run


@pytest.mark.parametrize("module,setup,path,before,prefix", CASES)
@pytest.mark.parametrize("stop", [False, True])
def test_kv_retry_readback_duplicate_cleanup(monkeypatch, module, setup, path, before, prefix, stop):
    env, request, _ = setup(monkeypatch)
    sleeps = []

    async def pause(delay):
        sleeps.append(delay)

    import asyncio
    monkeypatch.setattr(asyncio, "sleep", pause)
    app = e2e_entry.Default()
    app.env = env
    store = StateStore(env)
    run_id = request.headers.get("X-E2E-Run-ID")
    for phase in module.PHASES:
        for step in ("kv_prepare" if phase == "prepare" else phase, "verify"):
            request.url = f"https://e2e.invalid/admin/e2e/{path}/{step}"
            response = run(app.fetch(request))
            assert response.status == 200, run(response.text())
        if phase == before:
            owner = run(store.get_e2e_manifest(module.SERVICE))
            assert owner and owner["kv_retry"] is True
            for label in ("cache", "result"):
                for failure in ("before", "after", "recovered"):
                    assert owner["stages"][f"{prefix}_kv_{label}_{failure}"] == 200
            assert sleeps == [1, 2, 1, 2]
            if stop:
                break
    assert run(module.cleanup(env, store, run_id))["ok"]
    clean = run(store.get_e2e_manifest(module.SERVICE))
    assert clean and clean["dirty"] is False
    assert clean["outcome"] == ("failed_clean" if stop else "passed")
    assert all(run(store.get_text(key)) is None for key in module.KEYS)


@pytest.mark.parametrize("module,setup,path,before,prefix", CASES)
def test_kv_retry_does_not_pass_without_failure_evidence(monkeypatch, module, setup, path, before, prefix):
    env, request, _ = setup(monkeypatch)
    store = StateStore(env)
    run_id = request.headers.get("X-E2E-Run-ID")
    for phase in module.PHASES:
        run(module.run(env, store, run_id, phase, request))
        run(module.run(env, store, run_id, "verify", request))
    owner = run(store.get_e2e_manifest(module.SERVICE))
    assert owner
    owner["kv_retry"] = True
    run(store.put_e2e_manifest(module.SERVICE, owner))
    run(module.cleanup(env, store, run_id))
    clean = run(store.get_e2e_manifest(module.SERVICE))
    assert clean and clean["outcome"] == "failed_clean"
