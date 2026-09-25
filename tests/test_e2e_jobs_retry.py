"""3通常ジョブの固定失敗・別HTTP再試行・重複抑止・回収を検証する。"""

import pytest

import e2e_entry
import e2e_qa_normal_probe as qa
import e2e_reminder_normal_probe as reminder
import e2e_notion_cleanup_normal as cleanup
from state import StateStore
from tests.test_e2e_qa_normal import setup as qa_setup
from tests.test_e2e_reminder_normal import setup as reminder_setup
from tests.test_e2e_notion_cleanup_normal import setup as cleanup_setup
from tests.test_e2e_qa_notification_probe import run


CASES = [(qa, qa_setup, "qa-normal", "notify", "qa_normal"),
         (reminder, reminder_setup, "reminder-normal", "notify", "reminder_normal"),
         (cleanup, cleanup_setup, "notion-cleanup-normal", "execute", "cleanup_normal")]


@pytest.mark.parametrize("module,setup,path,before,prefix", CASES)
def test_failed_http_readback_retry_duplicate_and_cleanup(monkeypatch, module, setup, path, before, prefix):
    env, request, _ = setup(monkeypatch)
    run_id = request.headers.get("X-E2E-Run-ID")
    app = e2e_entry.Default()
    app.env = env
    phases = list(module.PHASES)
    phases.insert(phases.index(before), "fail")
    for phase in phases:
        for step in (phase, "verify"):
            request.url = f"https://e2e.invalid/admin/e2e/{path}/{step}"
            response = run(app.fetch(request))
            assert response.status == 200, run(response.text())
        if phase == "fail":
            owner = run(StateStore(env).get_e2e_manifest(module.SERVICE))
            assert owner and owner["failure_detail"]["ok"] is False
            assert owner["stages"][f"{prefix}_failed_http"] == 500
            if module != cleanup:
                assert len(owner["messages"]) == 1
    assert run(module.cleanup(env, StateStore(env), run_id))["ok"]
    owner = run(StateStore(env).get_e2e_manifest(module.SERVICE))
    assert owner and owner["dirty"] is False and owner["outcome"] == "passed"
    assert owner["stages"][f"{prefix}_verify_fail"] == 200
    assert all(run(StateStore(env).get_text(key)) is None for key in module.KEYS)
    assert not hasattr(env, "_job_send_message") and not hasattr(env, "_job_archive_page")


@pytest.mark.parametrize("module,setup,path,before,prefix", CASES)
def test_unverified_failure_blocks_retry_and_cleanup_reports_failed_clean(monkeypatch, module, setup, path, before, prefix):
    env, request, _ = setup(monkeypatch)
    run_id = request.headers.get("X-E2E-Run-ID")
    for phase in module.PHASES[:module.PHASES.index(before)]:
        run(module.run(env, StateStore(env), run_id, phase, request))
        run(module.run(env, StateStore(env), run_id, "verify", request))
    run(module.run(env, StateStore(env), run_id, "fail", request))
    with pytest.raises(RuntimeError, match="previous_unverified"):
        run(module.run(env, StateStore(env), run_id, before, request))
    with pytest.raises(RuntimeError, match="phase_mismatch"):
        run(module.run(env, StateStore(env), run_id, "fail", request))
    assert run(module.cleanup(env, StateStore(env), run_id))["ok"]
    owner = run(StateStore(env).get_e2e_manifest(module.SERVICE))
    assert owner and owner["outcome"] == "failed_clean"
