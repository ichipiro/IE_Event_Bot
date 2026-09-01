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
    _expect(
        errors,
        text.count("timeout-minutes:") == 2,
        "job_timeout_count_changed",
    )
    _expect(errors, "always()" in text, "always_cleanup_missing")
    _expect(
        errors,
        text.count("retention-days: 14") == 2,
        "artifact_retention_changed",
    )
    _expect(
        errors,
        text.count("persist-credentials: false") == 2,
        "checkout_credentials_changed",
    )
    for secret in FORBIDDEN_RUNTIME_SECRETS:
        _expect(errors, secret not in text, f"runtime_secret_copied_to_github:{secret}")

    deploy_block = _step_block(text, "Deploy and run service CRUD smoke")
    cleanup_block = _step_block(text, "Always cleanup resources created by this run")
    evidence_block = _step_block(text, "Collect redacted evidence")
    _expect(errors, bool(deploy_block), "deploy_step_missing")
    _expect(errors, bool(cleanup_block), "cleanup_step_missing")
    _expect(errors, bool(evidence_block), "evidence_step_missing")
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        assignments = re.findall(rf"^\s+{re.escape(name)}:", text, re.MULTILINE)
        _expect(errors, len(assignments) == 1, f"cloudflare_secret_scope_changed:{name}")
        _expect(errors, name in deploy_block, f"cloudflare_secret_not_deploy_only:{name}")
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
        "cleanup_write_mode_guard_missing",
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
