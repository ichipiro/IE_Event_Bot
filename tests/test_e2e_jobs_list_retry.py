"""実ページ送り後の固定一覧失敗・独立読戻し・再試行・所有資源回収。"""

import pytest

import e2e_entry
import e2e_qa_normal_probe as qa
import e2e_notion_cleanup_normal as cleanup
from state import StateStore
from tests.test_e2e_qa_normal import setup as qa_setup
from tests.test_e2e_notion_cleanup_normal import setup as cleanup_setup
from tests.test_e2e_qa_notification_probe import run


CASES = [
    (qa, qa_setup, "qa-normal", "qa_normal", ("prepare", "list_fail_first", "first", "update", "list_fail", "notify", "duplicate")),
    (cleanup, cleanup_setup, "notion-cleanup-normal", "cleanup_normal", ("prepare", "list_fail", "execute", "duplicate")),
]


@pytest.mark.parametrize("module,setup,path,prefix,phases", CASES)
@pytest.mark.parametrize("stop", [False, True])
def test_list_failure_retry_and_cleanup(monkeypatch, module, setup, path, prefix, phases, stop):
    env, request, data = setup(monkeypatch)
    app = e2e_entry.Default()
    app.env = env
    store = StateStore(env)
    run_id = request.headers.get("X-E2E-Run-ID")
    for phase in phases:
        for step in (phase, "verify"):
            request.url = f"https://e2e.invalid/admin/e2e/{path}/{step}"
            response = run(app.fetch(request))
            assert response.status == 200, run(response.text())
        if phase.startswith("list_fail"):
            owner = run(store.get_e2e_manifest(module.SERVICE))
            assert owner and owner["stages"][f"{prefix}_{phase}_http"] == 500
            assert owner["stages"][f"{prefix}_{phase}_injected"] == 503
            assert not hasattr(env, "_job_notion_query_fetch")
            if stop:
                break
    assert run(module.cleanup(env, store, run_id))["ok"]
    owner = run(store.get_e2e_manifest(module.SERVICE))
    assert owner and owner["dirty"] is False
    assert owner["outcome"] == ("failed_clean" if stop else "passed")
    assert all(p["archived"] for p in data[0].values())
    assert all(run(store.get_text(k)) is None for k in module.KEYS)


@pytest.mark.parametrize("module,setup,path,prefix,phases", CASES)
def test_unverified_list_failure_blocks_retry(monkeypatch, module, setup, path, prefix, phases):
    env, request, _ = setup(monkeypatch)
    store = StateStore(env)
    run_id = request.headers.get("X-E2E-Run-ID")
    run(module.run(env, store, run_id, "prepare", request))
    run(module.run(env, store, run_id, "verify", request))
    run(module.run(env, store, run_id, phases[1], request))
    with pytest.raises(RuntimeError, match="previous_unverified"):
        run(module.run(env, store, run_id, phases[2], request))
    with pytest.raises(RuntimeError, match="phase_mismatch"):
        run(module.run(env, store, run_id, phases[1], request))
    assert run(module.cleanup(env, store, run_id))["ok"]
    owner = run(store.get_e2e_manifest(module.SERVICE))
    assert owner and owner["outcome"] == "failed_clean"
