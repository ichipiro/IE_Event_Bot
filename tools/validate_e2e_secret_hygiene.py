from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

EXPECTED_ENV_KEYS = {
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "INTERNAL_API_TOKEN",
    "E2E_WORKER_URL",
    "E2E_WORKER_URL_SHA256",
    "GCAL_WEBHOOK_TOKEN",
    "GOOGLE_CALENDAR_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON_B64",
    "NOTION_TOKEN",
    "NOTION_EVENT_INTERNAL_ID",
    "NOTION_QA_ID",
    "DISCORD_TOKEN",
    "DISCORD_GUILD_ID",
    "EVENT_CREATE_CHANNEL_ID",
    "EVENT_CREATE_ROLE_ID",
    "QA_CHANNEL_ID",
    "REMINDER_CHANNEL_ID",
    "REMINDER_ROLE_ID",
}

IGNORED_CANDIDATES = (
    ".env.e2e",
    ".dev.vars",
    ".dev.vars.e2e",
    "workers/.dev.vars.e2e",
    "workers/service-account.json",
    "playwright/.auth/user.json",
    "e2e/.auth/state.json",
    "playwright-report/index.html",
    "test-results/trace.zip",
    "blob-report/report.zip",
)

PROTECTED_PREFIXES = (
    "playwright/.auth/",
    "e2e/.auth/",
    "playwright-report/",
    "test-results/",
    "blob-report/",
)


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _tracked_paths() -> list[str]:
    result = _git("ls-files", "-z")
    if result.returncode != 0:
        raise RuntimeError("git_ls_files_failed")
    return [
        path.decode("utf-8")
        for path in result.stdout.split(b"\0")
        if path
    ]


def _is_forbidden_tracked_path(path: str) -> bool:
    name = Path(path).name
    if name == ".env" or (
        name.startswith(".env.") and name not in {".env.example", ".env.sample"}
    ):
        return True
    if name == ".dev.vars" or name.startswith(".dev.vars."):
        return True
    if path == "workers/service-account.json":
        return True
    return path.startswith(PROTECTED_PREFIXES)


def _validate_template() -> list[str]:
    if not ENV_EXAMPLE.is_file():
        return ["missing_env_example"]

    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(
        ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"invalid_env_line:{line_number}")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in seen:
            errors.append(f"duplicate_env_key:{key}")
        seen.add(key)
        if value.strip():
            errors.append(f"nonempty_env_example_value:{key}")

    for key in sorted(EXPECTED_ENV_KEYS - seen):
        errors.append(f"missing_env_key:{key}")
    for key in sorted(seen - EXPECTED_ENV_KEYS):
        errors.append(f"unexpected_env_key:{key}")
    return errors


def main() -> int:
    errors = _validate_template()

    for path in _tracked_paths():
        if _is_forbidden_tracked_path(path):
            errors.append(f"forbidden_tracked_path:{path}")

    for path in IGNORED_CANDIDATES:
        result = _git("check-ignore", "--no-index", "-q", "--", path)
        if result.returncode != 0:
            errors.append(f"not_ignored:{path}")

    env_example_result = _git(
        "check-ignore",
        "--no-index",
        "-q",
        "--",
        ".env.example",
    )
    if env_example_result.returncode == 0:
        errors.append("env_example_is_ignored")

    if errors:
        for error in errors:
            print(error)
        return 1

    print("e2e_secret_hygiene_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
