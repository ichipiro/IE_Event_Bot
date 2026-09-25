from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "e2e-staging.yml"

ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}
FORBIDDEN_TRIGGERS = (
    "pull_request",
    "pull_request_target",
    "push",
    "schedule",
    "workflow_call",
    "workflow_run",
)
FORBIDDEN_RUNTIME_SECRETS = (
    "DISCORD_TOKEN",
    "GCAL_WEBHOOK_TOKEN",
    "GOOGLE_CALENDAR_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON_B64",
    "NOTION_EVENT_INTERNAL_ID",
    "NOTION_QA_ID",
    "NOTION_TOKEN",
)


def _expect(errors: list[str], condition: bool, code: str) -> None:
    if not condition:
        errors.append(code)


def _step_block(text: str, name: str) -> str:
    pattern = re.compile(
        rf"^      - name: {re.escape(name)}\n(?P<body>.*?)(?=^      - name: |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group("body") if match else ""


def _check_action_pins(errors: list[str], text: str) -> None:
    uses_lines = re.findall(r"^\s+-?\s*uses:\s+([^\s#]+)", text, re.MULTILINE)
    _expect(errors, bool(uses_lines), "missing_actions")
    for reference in uses_lines:
        if "@" not in reference:
            errors.append("action_reference_invalid")
            continue
        name, revision = reference.rsplit("@", 1)
        _expect(errors, name in ACTION_PINS, f"action_not_allowlisted:{name}")
        expected = ACTION_PINS.get(name)
        if expected is not None:
            _expect(errors, revision == expected, f"action_pin_changed:{name}")
        _expect(
            errors,
            re.fullmatch(r"[0-9a-f]{40}", revision) is not None,
            f"action_not_full_sha:{name}",
        )


def _check_workflow(text: str) -> list[str]:
    errors: list[str] = []
    _expect(errors, text.startswith("name: E2E Staging\n"), "workflow_name_changed")
    _expect(errors, text.count("  workflow_dispatch:\n") == 1, "manual_trigger_missing")
    for trigger in FORBIDDEN_TRIGGERS:
        _expect(
            errors,
            re.search(rf"^  {re.escape(trigger)}:\s*$", text, re.MULTILINE) is None,
            f"forbidden_trigger:{trigger}",
        )

    _expect(
        errors,
        "permissions:\n  contents: read\n\nconcurrency:" in text,
        "least_privilege_permissions_changed",
    )
    _expect(errors, "group: ie-event-bot-e2e" in text, "concurrency_group_changed")
    _expect(errors, "cancel-in-progress: false" in text, "concurrency_cancel_changed")
    _expect(errors, "environment: e2e" in text, "e2e_environment_missing")
    _expect(errors, "default: preflight" in text, "write_mode_became_default")
    _expect(errors, text.count("deploy-and-discord-state-smoke") == 3, "discord_state_mode_contract_changed")
    _expect(errors, text.count("deploy-and-discord-kv-smoke") == 3, "discord_kv_mode_contract_changed")
    _expect(errors, text.count("deploy-and-discord-batch-smoke") == 3, "discord_batch_mode_contract_changed")
    _expect(errors, text.count("deploy-and-discord-batch-google-smoke") == 3, "discord_batch_google_mode_contract_changed")
    _expect(errors, text.count("deploy-and-sync-lock-smoke") == 3, "sync_lock_mode_contract_changed")
    _expect(errors, text.count("deploy-and-google-calendar-check") == 2, "google_calendar_check_contract_changed")
    _expect(errors, text.count("deploy-and-watch-shared-smoke") == 5, "watch_shared_mode_contract_changed")
    _expect(errors, text.count("deploy-and-jobs-list-retry-smoke") == 3, "jobs_list_retry_mode_contract_changed")
    _expect(errors, text.count("deploy-and-jobs-kv-retry-smoke") == 3, "jobs_kv_retry_mode_contract_changed")
    _expect(errors, text.count("deploy-and-jobs-retry-smoke") == 3, "jobs_retry_mode_contract_changed")
    _expect(errors, text.count("deploy-and-qa-normal-smoke") == 3, "qa_normal_mode_contract_changed")
    _expect(errors, text.count("deploy-and-reminder-normal-smoke") == 3, "reminder_normal_mode_contract_changed")
    _expect(errors, text.count("deploy-and-notion-cleanup-normal-smoke") == 3, "cleanup_normal_mode_contract_changed")
    _expect(errors, text.count("deploy-and-all-http-smoke") == 3, "all_http_mode_contract_changed")
    _expect(errors, text.count("deploy-and-all-sync-smoke") == 3, "all_sync_mode_contract_changed")
    _expect(errors, text.count("deploy-and-google-matrix-smoke") == 3, "google_matrix_mode_contract_changed")
    _expect(errors, text.count("deploy-and-google-full-smoke") == 3, "google_full_mode_contract_changed")
    _expect(errors, text.count("deploy-and-google-sync-smoke") == 3, "google_sync_mode_contract_changed")
    _expect(errors, text.count("deploy-and-google-sync-recovery") == 3, "google_sync_recovery_contract_changed")
    _expect(errors, text.count("deploy-and-sync-faults-smoke") == 3, "sync_faults_mode_contract_changed")
    _expect(errors, text.count("deploy-and-discord-batch-notification-smoke") == 3, "discord_batch_notification_mode_contract_changed")
    _expect(
        errors,
        text.count("deploy-and-discord-google-smoke") == 3,
        "discord_google_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-discord-notion-smoke") == 3,
        "discord_notion_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-discord-delta-smoke") == 3,
        "discord_delta_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-google-notion-smoke") == 3,
        "google_notion_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-google-discord-smoke") == 3,
        "google_discord_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-qa-notification-smoke") == 3,
        "qa_notification_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-reminder-smoke") == 3,
        "reminder_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-notion-cleanup-smoke") == 3,
        "notion_cleanup_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-webhook-simulation-smoke") == 3,
        "webhook_simulation_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-webhook-delivery-smoke") == 3,
        "webhook_delivery_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("deploy-and-webhook-change-smoke") == 3,
        "webhook_change_mode_contract_changed",
    )
    _expect(
        errors,
        text.count("timeout-minutes:") == 4,
        "job_timeout_count_changed",
    )
    _expect(errors, "always()" in text, "always_cleanup_missing")
    _expect(
        errors,
        text.count("retention-days: 14") == 5,
        "artifact_retention_changed",
    )
    _expect(
        errors,
        text.count("persist-credentials: false") == 3,
        "checkout_credentials_changed",
    )
    for secret in FORBIDDEN_RUNTIME_SECRETS:
        _expect(errors, secret not in text, f"runtime_secret_copied_to_github:{secret}")
    _expect(errors, "vars.E2E_WORKER_URL" not in text, "worker_url_not_masked")
    for name in ("E2E_WORKER_URL", "E2E_WORKER_URL_SHA256"):
        _expect(
            errors,
            text.count(f"${{{{ secrets.{name} }}}}") == 4,
            f"worker_origin_secret_scope_changed:{name}",
        )

    deploy_block = _step_block(text, "Deploy and run selected write smoke")
    cleanup_block = _step_block(text, "Always cleanup resources created by this run")
    _expect(errors, "inputs.mode == 'deploy-and-discord-state-smoke'" in cleanup_block, "cleanup_discord_state_mode_guard_missing")
    _expect(errors, "inputs.mode == 'deploy-and-discord-kv-smoke'" in cleanup_block, "cleanup_discord_kv_mode_guard_missing")
    _expect(errors, "inputs.mode == 'deploy-and-discord-batch-smoke'" in cleanup_block, "cleanup_discord_batch_mode_guard_missing")
    _expect(errors, "inputs.mode == 'deploy-and-discord-batch-google-smoke'" in cleanup_block, "cleanup_discord_batch_google_mode_guard_missing")
    _expect(errors, "inputs.mode == 'deploy-and-sync-lock-smoke'" in cleanup_block, "cleanup_sync_lock_mode_guard_missing")
    _expect(errors, "inputs.mode == 'deploy-and-sync-faults-smoke'" in cleanup_block, "cleanup_sync_faults_mode_guard_missing")
    _expect(errors, "inputs.mode == 'deploy-and-discord-batch-notification-smoke'" in cleanup_block, "cleanup_discord_batch_notification_mode_guard_missing")
    evidence_block = _step_block(text, "Collect redacted evidence")
    diagnostic_block = _step_block(text, "Read-only Google lock diagnostics")
    recovery_logs = _step_block(text, "Collect lock recovery logs")
    _expect(errors, "inputs.mode == 'deploy-and-watch-shared-smoke'" in recovery_logs
            and "steps.run_id.outcome == 'success'" in recovery_logs
            and "E2E_DIAGNOSTIC_RUN_ID: ${{ steps.run_id.outputs.run_id }}" in recovery_logs
            and "run: node tools/diagnose_google_lock.mjs" in recovery_logs,
            "release_recovery_log_guard_missing")
    cron_deploy = _step_block(text, "Deploy isolated Worker and wait for real Cron")
    cron_cleanup = _step_block(text, "Always remove Cron Worker and owned KV")
    _expect(errors, "name: Approved real Cron E2E\n    if: ${{ inputs.mode == 'deploy-and-real-cron-smoke' || inputs.mode == 'deploy-and-real-cron-contention' || inputs.mode == 'read-only-real-cron-diagnostics' }}" in text,
            "cron_mode_guard_missing")
    _expect(errors, "name: Approved E2E\n    if: ${{ inputs.mode != 'deploy-and-real-cron-smoke' && inputs.mode != 'deploy-and-real-cron-contention' && inputs.mode != 'read-only-real-cron-diagnostics' }}" in text,
            "cron_normal_job_not_excluded")
    cron_job = text.split("  cron-e2e:\n", 1)[-1].split("\n  e2e:\n", 1)[0]
    _expect(errors, "environment: e2e" in cron_job and "needs: local-validation" in cron_job,
            "cron_approval_gate_missing")
    _expect(errors, "node tools/run_cron_e2e.mjs run --run-id" in cron_deploy,
            "cron_runner_missing")
    _expect(errors, "always() && steps.cron_run.outcome == 'success'" in cron_cleanup
            and "node tools/run_cron_e2e.mjs cleanup --run-id" in cron_cleanup,
            "cron_cleanup_missing")
    for block in (cron_deploy, cron_cleanup):
        _expect(errors, "inputs.mode == 'deploy-and-real-cron-smoke'" in block,
                "cron_diagnostic_write_guard_missing")
    for block in (cron_deploy, cron_cleanup):
        _expect(errors, "inputs.mode == 'deploy-and-real-cron-contention'" in block
                and "E2E_CRON_CONTENTION:" in block and "INTERNAL_API_TOKEN:" in block,
                "cron_contention_guard_missing")
    cron_diagnostic = _step_block(text, "Read-only real Cron diagnostics")
    _expect(errors, "inputs.mode == 'read-only-real-cron-diagnostics'" in cron_diagnostic
            and "node tools/run_cron_e2e.mjs diagnose" in cron_diagnostic, "cron_diagnostic_missing")
    diagnostic_mode = "read-only-google-lock-diagnostics"
    _expect(errors, f"inputs.mode == '{diagnostic_mode}'" in diagnostic_block, "diagnostic_mode_guard_missing")
    _expect(errors, "run: node tools/diagnose_google_lock.mjs" in diagnostic_block, "diagnostic_command_changed")
    _expect(errors, f"inputs.mode != '{diagnostic_mode}'" in _step_block(text, "Create run ID"), "diagnostic_run_id_not_skipped")
    for name, block in (("deploy", deploy_block), ("cleanup", cleanup_block)):
        _expect(errors, diagnostic_mode not in block, f"diagnostic_in_write_step:{name}")
    _expect(errors, bool(deploy_block), "deploy_step_missing")
    _expect(errors, bool(cleanup_block), "cleanup_step_missing")
    _expect(errors, bool(evidence_block), "evidence_step_missing")
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        assignments = re.findall(rf"^\s+{re.escape(name)}:", text, re.MULTILINE)
        _expect(errors, len(assignments) == 6, f"cloudflare_secret_scope_changed:{name}")
        _expect(errors, name in recovery_logs, f"cloudflare_secret_not_in_recovery_logs:{name}")
        _expect(errors, name in cron_deploy and name in cron_cleanup, f"cron_secret_missing:{name}")
        _expect(errors, name in deploy_block, f"cloudflare_secret_not_in_deploy:{name}")
        _expect(errors, name in diagnostic_block, f"cloudflare_secret_not_in_diagnostic:{name}")
        _expect(errors, name not in cleanup_block, f"cloudflare_secret_in_cleanup:{name}")
        _expect(errors, name not in evidence_block, f"cloudflare_secret_in_evidence:{name}")
    _expect(
        errors,
        "steps.run_id.outcome == 'success'" in cleanup_block,
        "cleanup_run_id_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-crud-smoke'" in cleanup_block,
        "cleanup_crud_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-discord-google-smoke'" in cleanup_block,
        "cleanup_discord_google_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-discord-notion-smoke'" in cleanup_block,
        "cleanup_discord_notion_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-discord-delta-smoke'" in cleanup_block,
        "cleanup_discord_delta_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-google-notion-smoke'" in cleanup_block,
        "cleanup_google_notion_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-google-discord-smoke'" in cleanup_block,
        "cleanup_google_discord_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-qa-notification-smoke'" in cleanup_block,
        "cleanup_qa_notification_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-reminder-smoke'" in cleanup_block,
        "cleanup_reminder_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-notion-cleanup-smoke'" in cleanup_block,
        "cleanup_notion_cleanup_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-webhook-simulation-smoke'" in cleanup_block,
        "cleanup_webhook_simulation_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-webhook-delivery-smoke'" in cleanup_block,
        "cleanup_webhook_delivery_mode_guard_missing",
    )
    _expect(
        errors,
        "inputs.mode == 'deploy-and-webhook-change-smoke'" in cleanup_block,
        "cleanup_webhook_change_mode_guard_missing",
    )
    _check_action_pins(errors, text)
    return errors


def main() -> int:
    if not WORKFLOW_PATH.is_file():
        print("missing_e2e_workflow")
        return 1

    errors = _check_workflow(WORKFLOW_PATH.read_text(encoding="utf-8"))
    if errors:
        for error in errors:
            print(error)
        return 1

    print("e2e_workflow_policy_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
