from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".codex" / "config.toml"
PACKAGE_PATH = ROOT / "package.json"
LOCK_PATH = ROOT / "package-lock.json"

E2E_TOOLS = [
    "preflight",
    "deploy_e2e",
    "seed_fixture",
    "trigger_sync",
    "trigger_webhook",
    "trigger_job",
    "read_status",
    "assert_external_state",
    "collect_evidence",
    "cleanup_run",
]
E2E_READ_TOOLS = {
    "preflight",
    "read_status",
    "assert_external_state",
    "collect_evidence",
}
E2E_WRITE_TOOLS = set(E2E_TOOLS) - E2E_READ_TOOLS

NOTION_TOOLS = [
    "notion_search",
    "notion_fetch",
    "notion_query_data_sources",
    "notion_create_pages",
    "notion_update_page",
]
NOTION_READ_TOOLS = set(NOTION_TOOLS[:3])
NOTION_WRITE_TOOLS = set(NOTION_TOOLS[3:])

PLAYWRIGHT_TOOLS = [
    "browser_close",
    "browser_console_messages",
    "browser_handle_dialog",
    "browser_find",
    "browser_fill_form",
    "browser_press_key",
    "browser_type",
    "browser_navigate",
    "browser_navigate_back",
    "browser_take_screenshot",
    "browser_snapshot",
    "browser_click",
    "browser_hover",
    "browser_select_option",
    "browser_tabs",
    "browser_wait_for",
]
PLAYWRIGHT_READ_TOOLS = {
    "browser_find",
    "browser_snapshot",
    "browser_wait_for",
}
PLAYWRIGHT_WRITE_TOOLS = set(PLAYWRIGHT_TOOLS) - PLAYWRIGHT_READ_TOOLS
PLAYWRIGHT_DISABLED_TOOLS = {
    "browser_drag",
    "browser_drop",
    "browser_evaluate",
    "browser_file_upload",
    "browser_network_request",
    "browser_network_requests",
    "browser_resize",
    "browser_run_code_unsafe",
}

PINNED_PACKAGES = {
    "@modelcontextprotocol/sdk": "1.30.0",
    "@playwright/mcp": "0.0.80",
    "wrangler": "4.127.1",
    "zod": "4.5.4",
}


def _expect(errors: list[str], condition: bool, code: str) -> None:
    if not condition:
        errors.append(code)


def _approval_modes(server: dict[str, Any]) -> dict[str, str]:
    tools = server.get("tools")
    if not isinstance(tools, dict):
        return {}
    return {
        name: settings.get("approval_mode")
        for name, settings in tools.items()
        if isinstance(name, str) and isinstance(settings, dict)
    }


def _check_approvals(
    errors: list[str],
    server: dict[str, Any],
    read_tools: set[str],
    write_tools: set[str],
    prefix: str,
) -> None:
    modes = _approval_modes(server)
    for name in sorted(read_tools):
        _expect(errors, modes.get(name) == "auto", f"{prefix}_read_not_auto:{name}")
    for name in sorted(write_tools):
        _expect(errors, modes.get(name) == "prompt", f"{prefix}_write_not_prompt:{name}")


def _check_common_server(
    errors: list[str],
    server: dict[str, Any],
    prefix: str,
) -> None:
    _expect(errors, server.get("enabled") is True, f"{prefix}_not_enabled")
    _expect(
        errors,
        isinstance(server.get("enabled_tools"), list) and bool(server["enabled_tools"]),
        f"{prefix}_missing_enabled_tools",
    )
    _expect(
        errors,
        isinstance(server.get("disabled_tools"), list),
        f"{prefix}_missing_disabled_tools",
    )
    _expect(
        errors,
        isinstance(server.get("startup_timeout_sec"), int)
        and server["startup_timeout_sec"] > 0,
        f"{prefix}_invalid_startup_timeout",
    )
    _expect(
        errors,
        isinstance(server.get("tool_timeout_sec"), int)
        and server["tool_timeout_sec"] > 0,
        f"{prefix}_invalid_tool_timeout",
    )


def _check_project_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return ["missing_mcp_servers"]

    e2e = servers.get("ie_event_bot_e2e")
    notion = servers.get("notion")
    playwright = servers.get("playwright_e2e")
    for name, server in (
        ("e2e", e2e),
        ("notion", notion),
        ("playwright", playwright),
    ):
        if not isinstance(server, dict):
            errors.append(f"missing_server:{name}")
            continue
        _check_common_server(errors, server, name)

    if not all(isinstance(server, dict) for server in (e2e, notion, playwright)):
        return errors

    _expect(errors, e2e.get("command") == "node", "e2e_invalid_command")
    _expect(
        errors,
        e2e.get("args") == ["tools/e2e_mcp_server.mjs"],
        "e2e_invalid_args",
    )
    _expect(
        errors,
        e2e.get("env_vars")
        == [
            "E2E_WORKER_URL",
            "E2E_WORKER_URL_SHA256",
            "INTERNAL_API_TOKEN",
            "CLOUDFLARE_ACCOUNT_ID",
            "CLOUDFLARE_API_TOKEN",
        ],
        "e2e_invalid_env_vars",
    )
    _expect(errors, "env" not in e2e, "e2e_static_env_forbidden")
    _expect(errors, e2e.get("enabled_tools") == E2E_TOOLS, "e2e_tool_allowlist_changed")
    _expect(errors, e2e.get("disabled_tools") == [], "e2e_disabled_tools_changed")
    _expect(
        errors,
        e2e.get("default_tools_approval_mode") == "prompt",
        "e2e_default_approval_not_prompt",
    )
    _check_approvals(errors, e2e, E2E_READ_TOOLS, E2E_WRITE_TOOLS, "e2e")

    _expect(
        errors,
        notion.get("url") == "https://mcp.notion.com/mcp",
        "notion_endpoint_changed",
    )
    _expect(errors, notion.get("auth") == "oauth", "notion_auth_changed")
    _expect(errors, "http_headers" not in notion, "notion_static_headers_forbidden")
    _expect(
        errors,
        "bearer_token_env_var" not in notion,
        "notion_bearer_token_forbidden",
    )
    _expect(
        errors,
        notion.get("enabled_tools") == NOTION_TOOLS,
        "notion_tool_allowlist_changed",
    )
    _expect(
        errors,
        notion.get("default_tools_approval_mode") == "writes",
        "notion_default_approval_changed",
    )
    _check_approvals(
        errors,
        notion,
        NOTION_READ_TOOLS,
        NOTION_WRITE_TOOLS,
        "notion",
    )

    _expect(
        errors,
        playwright.get("command") == "./node_modules/.bin/playwright-mcp",
        "playwright_invalid_command",
    )
    _expect(
        errors,
        playwright.get("enabled_tools") == PLAYWRIGHT_TOOLS,
        "playwright_tool_allowlist_changed",
    )
    _expect(
        errors,
        set(playwright.get("disabled_tools", [])) == PLAYWRIGHT_DISABLED_TOOLS,
        "playwright_disabled_tools_changed",
    )
    _expect(
        errors,
        playwright.get("default_tools_approval_mode") == "writes",
        "playwright_default_approval_changed",
    )
    _check_approvals(
        errors,
        playwright,
        PLAYWRIGHT_READ_TOOLS,
        PLAYWRIGHT_WRITE_TOOLS,
        "playwright",
    )

    args = playwright.get("args", [])
    _expect(errors, isinstance(args, list), "playwright_args_invalid")
    if isinstance(args, list):
        required_args = {
            "--isolated",
            "--block-service-workers",
            "--image-responses",
            "omit",
            "--output-max-size",
            "52428800",
            "--timeout-action",
            "10000",
            "--timeout-navigation",
            "60000",
        }
        for value in sorted(required_args):
            _expect(errors, value in args, f"playwright_missing_arg:{value}")
        for value in ("--user-data-dir", "--storage-state", "--save-session", "--secrets"):
            _expect(errors, value not in args, f"playwright_forbidden_arg:{value}")

    plugins = config.get("plugins")
    cloudflare = None
    if isinstance(plugins, dict):
        plugin = plugins.get("cloudflare@openai-curated-remote")
        if isinstance(plugin, dict):
            plugin_servers = plugin.get("mcp_servers")
            if isinstance(plugin_servers, dict):
                cloudflare = plugin_servers.get("cloudflare-api")
    if not isinstance(cloudflare, dict):
        errors.append("missing_cloudflare_plugin_policy")
    else:
        _expect(errors, cloudflare.get("enabled") is True, "cloudflare_not_enabled")
        _expect(
            errors,
            cloudflare.get("enabled_tools") == ["search", "execute"],
            "cloudflare_tool_allowlist_changed",
        )
        _expect(
            errors,
            cloudflare.get("disabled_tools") == [],
            "cloudflare_disabled_tools_changed",
        )
        _expect(
            errors,
            cloudflare.get("default_tools_approval_mode") == "prompt",
            "cloudflare_default_approval_changed",
        )
        modes = _approval_modes(cloudflare)
        _expect(errors, modes.get("search") == "auto", "cloudflare_search_not_auto")
        _expect(errors, modes.get("execute") == "prompt", "cloudflare_execute_not_prompt")
        _expect(
            errors,
            isinstance(cloudflare.get("startup_timeout_sec"), int),
            "cloudflare_missing_startup_timeout",
        )
        _expect(
            errors,
            isinstance(cloudflare.get("tool_timeout_sec"), int),
            "cloudflare_missing_tool_timeout",
        )

    serialized = json.dumps(config, sort_keys=True)
    _expect(
        errors,
        re.search(
            r'(?i)"[^\"]*(?:api[_-]?token|bearer[_-]?token|secret|password)[^\"]*"'
            r'\s*:\s*"(?!")',
            serialized,
        )
        is None,
        "possible_secret_literal_in_config",
    )
    _expect(
        errors,
        re.search(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32}(?![A-Za-z0-9])", serialized)
        is None,
        "possible_account_or_resource_id_in_config",
    )
    return errors


def _check_packages(package: dict[str, Any], lock: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dependencies = package.get("devDependencies")
    if not isinstance(dependencies, dict):
        return ["missing_dev_dependencies"]

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        return ["missing_lock_packages"]
    lock_root = packages.get("")
    if not isinstance(lock_root, dict):
        return ["missing_lock_root"]
    lock_dependencies = lock_root.get("devDependencies")
    if not isinstance(lock_dependencies, dict):
        return ["missing_lock_dev_dependencies"]

    for name, version in PINNED_PACKAGES.items():
        _expect(
            errors,
            dependencies.get(name) == version,
            f"package_not_pinned:{name}",
        )
        _expect(
            errors,
            lock_dependencies.get(name) == version,
            f"lock_root_not_pinned:{name}",
        )
        installed = packages.get(f"node_modules/{name}")
        _expect(
            errors,
            isinstance(installed, dict) and installed.get("version") == version,
            f"lock_package_version_mismatch:{name}",
        )
    return errors


def main() -> int:
    errors: list[str] = []
    for path, code in (
        (CONFIG_PATH, "missing_project_mcp_config"),
        (PACKAGE_PATH, "missing_package_json"),
        (LOCK_PATH, "missing_package_lock"),
    ):
        if not path.is_file():
            errors.append(code)
    if errors:
        for error in errors:
            print(error)
        return 1

    with CONFIG_PATH.open("rb") as file:
        config = tomllib.load(file)
    package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    errors.extend(_check_project_config(config))
    errors.extend(_check_packages(package, lock))
    if errors:
        for error in errors:
            print(error)
        return 1

    print("e2e_mcp_config_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
