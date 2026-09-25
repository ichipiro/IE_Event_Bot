from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / "tools/validate_e2e_workflow.py"))


@pytest.mark.parametrize("before,after,expected", [
    ("name: Approved real Cron E2E\n    if: ${{ inputs.mode == 'deploy-and-real-cron-smoke' || inputs.mode == 'read-only-real-cron-diagnostics' }}",
     "name: Approved real Cron E2E", "cron_mode_guard_missing"),
    ("always() && steps.cron_run.outcome == 'success'", "success()", "cron_cleanup_missing"),
    ("node tools/run_cron_e2e.mjs run --run-id", "echo", "cron_runner_missing"),
])
def test_cron_workflow_guards(before, after, expected):
    text = (ROOT / ".github/workflows/e2e-staging.yml").read_text()
    assert POLICY["_check_workflow"](text) == []
    assert expected in POLICY["_check_workflow"](text.replace(before, after))
