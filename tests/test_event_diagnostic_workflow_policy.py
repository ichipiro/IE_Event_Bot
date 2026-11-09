"""本番通知の診断を、固定プログラムによる読み取りだけへ制限する。"""

from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / "tools/validate_e2e_workflow.py"))


def test_production_diagnostics_workflow_is_read_only():
    text = (ROOT / ".github/workflows/e2e-staging.yml").read_text()
    assert POLICY["_check_workflow"](text) == []


@pytest.mark.parametrize("before,after,expected", [
    ("if: ${{ inputs.mode == 'read-only-event-notification-diagnostics' }}",
     "if: ${{ always() }}", "event_diagnostic_mode_guard_missing"),
    (" && inputs.mode != 'read-only-event-notification-diagnostics'", "",
     "event_diagnostic_run_id_not_skipped"),
    ("run: node tools/diagnose_event_notifications.mjs", "run: npm run wrangler -- deploy",
     "event_diagnostic_command_changed"),
    ("START_UTC: ${{ inputs.diagnostic_start_utc }}", "START_UTC: arbitrary",
     "event_diagnostic_window_binding_changed"),
    ("END_UTC: ${{ inputs.diagnostic_end_utc }}", "END_UTC: arbitrary",
     "event_diagnostic_window_binding_changed"),
    ("START_UTC: ${{ inputs.diagnostic_start_utc }}",
     "INTERNAL_API_TOKEN: ${{ secrets.INTERNAL_API_TOKEN }}\n          START_UTC: ${{ inputs.diagnostic_start_utc }}",
     "event_diagnostic_sensitive_env:INTERNAL_API_TOKEN"),
    ("path: test-results/event-notification-diagnostics/report.json",
     "path: test-results/**", "event_diagnostic_artifact_scope_changed"),
    ("${{ inputs.mode == 'deploy-and-crud-smoke' ||",
     "${{ inputs.mode == 'read-only-event-notification-diagnostics' || inputs.mode == 'deploy-and-crud-smoke' ||",
     "event_diagnostic_in_write_step:deploy"),
])
def test_production_diagnostic_guards(before, after, expected):
    text = (ROOT / ".github/workflows/e2e-staging.yml").read_text()
    assert before in text
    assert expected in POLICY["_check_workflow"](text.replace(before, after, 1))
