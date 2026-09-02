import re
from hashlib import sha256
from urllib.parse import urlparse

from workers import Response

from e2e_discord_probe import (
    DISCORD_CRUD_MANIFEST_SERVICE,
    cleanup_discord_crud_probe,
    run_discord_crud_probe,
)
from e2e_discord_google_probe import (
    DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE,
    cleanup_discord_google_sync_probe,
    run_discord_google_sync_probe,
)
from e2e_discord_notion_probe import (
    DISCORD_NOTION_SYNC_MANIFEST_SERVICE,
    cleanup_discord_notion_sync_probe,
    run_discord_notion_sync_probe,
)
from e2e_google_probe import (
    GOOGLE_CRUD_MANIFEST_SERVICE,
    cleanup_google_calendar_crud_probe,
    run_google_calendar_crud_probe,
)
from e2e_google_discord_probe import (
    GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE,
    cleanup_google_discord_sync_probe,
    run_google_discord_sync_probe,
)
from e2e_google_notion_probe import (
    GOOGLE_NOTION_SYNC_MANIFEST_SERVICE,
    cleanup_google_notion_sync_probe,
    run_google_notion_sync_probe,
)
from e2e_notion_probe import (
    NOTION_CRUD_MANIFEST_SERVICE,
    cleanup_notion_crud_probe,
    run_notion_crud_probe,
)
from e2e_notion_cleanup_probe import (
    NOTION_CLEANUP_MANIFEST_SERVICE,
    cleanup_notion_cleanup_probe,
    run_notion_cleanup_probe,
)
from e2e_qa_notification_probe import (
    QA_NOTIFICATION_MANIFEST_SERVICE,
    cleanup_qa_notification_probe,
    run_qa_notification_probe,
)
from e2e_reminder_probe import (
    REMINDER_MANIFEST_SERVICE,
    cleanup_reminder_probe,
    run_reminder_probe,
)
from e2e_webhook_probe import (
    WEBHOOK_DISPATCH_MANIFEST_SERVICE,
    cleanup_webhook_dispatch_probe,
    run_webhook_dispatch_probe,
)
from entry import Default as ApplicationDefault
from entry import _json_response
from google_auth import describe_google_auth_sources
from state import StateStore
from sync_lock_do import SyncCoordinator


__all__ = ["Default", "SyncCoordinator"]

_GOOGLE_CRUD_PATH = "/admin/e2e/google-crud"
_GOOGLE_CLEANUP_PATH = "/admin/e2e/google-crud/cleanup"
_GOOGLE_DISCORD_SYNC_PATH = "/admin/e2e/google-discord-sync"
_GOOGLE_DISCORD_CLEANUP_PATH = "/admin/e2e/google-discord-sync/cleanup"
_GOOGLE_NOTION_SYNC_PATH = "/admin/e2e/google-notion-sync"
_GOOGLE_NOTION_CLEANUP_PATH = "/admin/e2e/google-notion-sync/cleanup"
_DISCORD_GOOGLE_SYNC_PATH = "/admin/e2e/discord-google-sync"
_DISCORD_GOOGLE_CLEANUP_PATH = "/admin/e2e/discord-google-sync/cleanup"
_DISCORD_NOTION_SYNC_PATH = "/admin/e2e/discord-notion-sync"
_DISCORD_NOTION_CLEANUP_PATH = "/admin/e2e/discord-notion-sync/cleanup"
_DISCORD_CRUD_PATH = "/admin/e2e/discord-crud"
_DISCORD_CLEANUP_PATH = "/admin/e2e/discord-crud/cleanup"
_NOTION_CRUD_PATH = "/admin/e2e/notion-crud"
_NOTION_CLEANUP_PATH = "/admin/e2e/notion-crud/cleanup"
_NOTION_AUTO_CLEAN_PATH = "/admin/e2e/notion-cleanup"
_NOTION_AUTO_CLEAN_CLEANUP_PATH = "/admin/e2e/notion-cleanup/cleanup"
_QA_NOTIFICATION_PATH = "/admin/e2e/qa-notification"
_QA_NOTIFICATION_CLEANUP_PATH = "/admin/e2e/qa-notification/cleanup"
_REMINDER_PATH = "/admin/e2e/reminder"
_REMINDER_CLEANUP_PATH = "/admin/e2e/reminder/cleanup"
_STATUS_PATH = "/admin/e2e/status"
_TRIGGER_WEBHOOK_PATH = "/admin/e2e/trigger-webhook"
_TRIGGER_WEBHOOK_CLEANUP_PATH = "/admin/e2e/trigger-webhook/cleanup"
_ORCHESTRATED_WRITE_PATHS = frozenset(
    {
        "/sync/all",
        "/gcal/sync",
        "/sync/discord-notion",
        "/jobs/qa-check",
        "/jobs/reminder",
        "/jobs/cleanup",
        "/jobs/run-all",
    }
)
_BLOCKED_APPLICATION_WRITE_PATHS = frozenset(
    {
        "/admin/google-token",
        "/admin/gcal/watch/ensure",
        "/gcal/webhook",
        "/admin/migration-status",
    }
)
_RUN_ID_PATTERN = re.compile(r"^E2E-\d{8}T\d{6}Z-[0-9a-f]{8}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}_sha256$")


def _request_run_id(request) -> str:
    value = request.headers.get("X-E2E-Run-ID")
    run_id = str(value or "").strip()
    return run_id if _RUN_ID_PATTERN.fullmatch(run_id) else ""


def _manifest_summary(value) -> dict:
    if not isinstance(value, dict):
        return {"present": False, "dirty": False, "run_id": None}
    dirty = value.get("dirty") is True
    run_id = str(value.get("run_id" if dirty else "last_run_id") or "")
    stages = value.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    summary = {
        "present": True,
        "dirty": dirty,
        "run_id": run_id if _RUN_ID_PATTERN.fullmatch(run_id) else None,
        "outcome": str(value.get("outcome") or "") or None,
        "stage": str(value.get("stage") or "") or None,
        "cleanup_attempts": value.get("cleanup_attempts"),
        "stages": {
            str(key): status
            for key, status in stages.items()
            if isinstance(key, str) and isinstance(status, int)
        },
    }
    fingerprints = value.get("resource_fingerprints")
    if isinstance(fingerprints, dict):
        safe_fingerprints = {
            str(key): str(fingerprint).lower()
            for key, fingerprint in fingerprints.items()
            if _FINGERPRINT_KEY_PATTERN.fullmatch(str(key))
            and _FINGERPRINT_PATTERN.fullmatch(str(fingerprint).lower())
        }
        if safe_fingerprints:
            summary["resource_fingerprints"] = safe_fingerprints
    return summary


def _binding_value(binding, key: str) -> str:
    """Workers binding の辞書/属性形式を安全な文字列へ正規化する。"""
    if isinstance(binding, dict):
        value = binding.get(key)
    else:
        value = getattr(binding, key, None)
    text = str(value or "").strip()
    return "" if text in ("jsnull", "jsundefined") else text


def _worker_version_summary(env) -> dict:
    metadata = getattr(env, "CF_VERSION_METADATA", None)
    version_id = _binding_value(metadata, "id") if metadata is not None else ""
    timestamp = _binding_value(metadata, "timestamp") if metadata is not None else ""
    if not version_id:
        return {"present": False}
    return {
        "present": True,
        "id_sha256": sha256(version_id.encode("utf-8")).hexdigest(),
        "timestamp": timestamp or None,
    }


def _watch_summary(value) -> dict:
    if not isinstance(value, dict):
        return {"present": False}
    summary: dict[str, bool | str] = {"present": True}
    for key in ("channel_id", "resource_id"):
        raw_value = str(value.get(key) or "").strip()
        if raw_value:
            summary[f"{key}_sha256"] = sha256(raw_value.encode("utf-8")).hexdigest()
    return summary


def _required_env_summary(env) -> dict[str, bool]:
    """E2E の全公開操作に必要な外部設定を値なしで要約する。"""
    required = {
        "notion_token": "NOTION_TOKEN",
        "notion_internal_db": "NOTION_EVENT_INTERNAL_ID",
        "notion_qa_db": "NOTION_QA_ID",
        "google_calendar_id": "GOOGLE_CALENDAR_ID",
        "discord_token": "DISCORD_TOKEN",
        "discord_guild_id": "DISCORD_GUILD_ID",
        "discord_event_channel": "EVENT_CREATE_CHANNEL_ID",
        "discord_event_role": "EVENT_CREATE_ROLE_ID",
        "discord_qa_channel": "QA_CHANNEL_ID",
        "discord_reminder_channel": "REMINDER_CHANNEL_ID",
        "discord_reminder_role": "REMINDER_ROLE_ID",
    }
    return {
        public_name: bool(str(getattr(env, binding_name, "") or "").strip())
        for public_name, binding_name in required.items()
    }


def _google_auth_summary(value) -> dict[str, bool]:
    raw = value if isinstance(value, dict) else {}
    raw_cache = raw.get("cache")
    cache = raw_cache if isinstance(raw_cache, dict) else {}
    return {
        "direct_env": bool(raw.get("direct_env")),
        "broker_configured": bool(raw.get("broker_configured")),
        "service_account_json_configured": bool(
            raw.get("service_account_json_configured")
        ),
        "cache_present": bool(cache.get("present")),
        "cache_valid": bool(cache.get("valid")),
    }


def _sync_lock_summary(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(raw.get("enabled")),
        "ok": raw.get("ok") is True,
        "status": raw.get("status") if isinstance(raw.get("status"), int) else None,
    }


def _e2e_google_crud_enabled(env) -> bool:
    value = getattr(env, "E2E_GOOGLE_CRUD_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_discord_crud_enabled(env) -> bool:
    value = getattr(env, "E2E_DISCORD_CRUD_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_notion_crud_enabled(env) -> bool:
    value = getattr(env, "E2E_NOTION_CRUD_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_orchestration_enabled(env) -> bool:
    value = getattr(env, "E2E_ORCHESTRATED_WRITES_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_google_notion_sync_enabled(env) -> bool:
    value = getattr(env, "E2E_GOOGLE_NOTION_SYNC_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_google_discord_sync_enabled(env) -> bool:
    value = getattr(env, "E2E_GOOGLE_DISCORD_SYNC_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_discord_notion_sync_enabled(env) -> bool:
    value = getattr(env, "E2E_DISCORD_NOTION_SYNC_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_discord_google_sync_enabled(env) -> bool:
    value = getattr(env, "E2E_DISCORD_GOOGLE_SYNC_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_qa_notification_enabled(env) -> bool:
    value = getattr(env, "E2E_QA_NOTIFICATION_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_reminder_enabled(env) -> bool:
    value = getattr(env, "E2E_REMINDER_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_notion_cleanup_enabled(env) -> bool:
    value = getattr(env, "E2E_NOTION_CLEANUP_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _e2e_webhook_simulation_enabled(env) -> bool:
    value = getattr(env, "E2E_WEBHOOK_SIMULATION_ENABLED", "false")
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class Default(ApplicationDefault):
    """通常WorkerをE2E専用の明示的な公開面へ制限する。"""

    async def fetch(self, request):
        path = urlparse(request.url).path
        method = str(request.method or "GET").upper()
        google_route = path in (_GOOGLE_CRUD_PATH, _GOOGLE_CLEANUP_PATH)
        google_discord_route = path in (
            _GOOGLE_DISCORD_SYNC_PATH,
            _GOOGLE_DISCORD_CLEANUP_PATH,
        )
        google_notion_route = path in (
            _GOOGLE_NOTION_SYNC_PATH,
            _GOOGLE_NOTION_CLEANUP_PATH,
        )
        discord_google_route = path in (
            _DISCORD_GOOGLE_SYNC_PATH,
            _DISCORD_GOOGLE_CLEANUP_PATH,
        )
        discord_notion_route = path in (
            _DISCORD_NOTION_SYNC_PATH,
            _DISCORD_NOTION_CLEANUP_PATH,
        )
        discord_route = path in (_DISCORD_CRUD_PATH, _DISCORD_CLEANUP_PATH)
        notion_route = path in (_NOTION_CRUD_PATH, _NOTION_CLEANUP_PATH)
        qa_notification_route = path in (
            _QA_NOTIFICATION_PATH,
            _QA_NOTIFICATION_CLEANUP_PATH,
        )
        reminder_route = path in (_REMINDER_PATH, _REMINDER_CLEANUP_PATH)
        notion_cleanup_route = path in (
            _NOTION_AUTO_CLEAN_PATH,
            _NOTION_AUTO_CLEAN_CLEANUP_PATH,
        )
        status_route = path == _STATUS_PATH
        webhook_route = path in (
            _TRIGGER_WEBHOOK_PATH,
            _TRIGGER_WEBHOOK_CLEANUP_PATH,
        )
        orchestrated_write_route = path in _ORCHESTRATED_WRITE_PATHS
        if path in _BLOCKED_APPLICATION_WRITE_PATHS:
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if orchestrated_write_route and not _e2e_orchestration_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if not any(
            (
                google_route,
                google_discord_route,
                google_notion_route,
                discord_google_route,
                discord_notion_route,
                discord_route,
                notion_route,
                qa_notification_route,
                reminder_route,
                notion_cleanup_route,
                status_route,
                webhook_route,
                orchestrated_write_route,
            )
        ):
            return await super().fetch(request)
        if google_route and not _e2e_google_crud_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if google_discord_route and not _e2e_google_discord_sync_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if google_notion_route and not _e2e_google_notion_sync_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if discord_google_route and not _e2e_discord_google_sync_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if discord_notion_route and not _e2e_discord_notion_sync_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if discord_route and not _e2e_discord_crud_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if notion_route and not _e2e_notion_crud_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if qa_notification_route and not _e2e_qa_notification_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if reminder_route and not _e2e_reminder_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if notion_cleanup_route and not _e2e_notion_cleanup_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if webhook_route and not _e2e_webhook_simulation_enabled(self.env):
            return _json_response({"ok": False, "error": "not_found"}, status=404)
        if not self._authorized(request):
            return Response("unauthorized", status=401)
        if status_route:
            if method != "GET":
                return _json_response({"ok": False, "error": "method_not_allowed"}, status=405)
            state = StateStore(self.env)
            if not state.e2e_manifest_enabled():
                return _json_response(
                    {"ok": False, "error": "sync_coordinator_required"},
                    status=503,
                )
            try:
                manifests = {
                    "google": await state.get_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE),
                    "discord": await state.get_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE),
                    "notion": await state.get_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE),
                }
                scenario_manifests = {
                    "discord_google": await state.get_e2e_manifest(
                        DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE
                    ),
                    "google_discord": await state.get_e2e_manifest(
                        GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE
                    ),
                    "google_notion": await state.get_e2e_manifest(
                        GOOGLE_NOTION_SYNC_MANIFEST_SERVICE
                    ),
                    "discord_notion": await state.get_e2e_manifest(
                        DISCORD_NOTION_SYNC_MANIFEST_SERVICE
                    ),
                    "qa_notification": await state.get_e2e_manifest(
                        QA_NOTIFICATION_MANIFEST_SERVICE
                    ),
                    "reminder": await state.get_e2e_manifest(
                        REMINDER_MANIFEST_SERVICE
                    ),
                    "notion_cleanup": await state.get_e2e_manifest(
                        NOTION_CLEANUP_MANIFEST_SERVICE
                    ),
                    "webhook_dispatch": await state.get_e2e_manifest(
                        WEBHOOK_DISPATCH_MANIFEST_SERVICE
                    ),
                }
                legacy_manifests = {
                    service: await state.get_legacy_e2e_manifest(service)
                    for service in ("google", "discord", "notion")
                }
            except Exception:
                return _json_response(
                    {"ok": False, "error": "e2e_manifest_unavailable"},
                    status=503,
                )
            watch_state = (
                await state.get_json("gcal_watch_state", None) if state.enabled() else None
            )
            google_auth = await describe_google_auth_sources(self.env, state)
            sync_lock = await self._sync_lock_status()
            return _json_response(
                {
                    "ok": True,
                    "mode": "e2e",
                    "kv_enabled": state.enabled(),
                    "e2e_manifest_enabled": state.e2e_manifest_enabled(),
                    "legacy_manifest_check_complete": True,
                    "legacy_manifests": {
                        service: {
                            "present": isinstance(value, dict),
                            "dirty": isinstance(value, dict) and value.get("dirty") is True,
                        }
                        for service, value in legacy_manifests.items()
                    },
                    "worker_version": _worker_version_summary(self.env),
                    "watch": _watch_summary(watch_state),
                    "required_envs": _required_env_summary(self.env),
                    "google_auth": _google_auth_summary(google_auth),
                    "sync_lock": _sync_lock_summary(sync_lock),
                    "orchestrated_writes_enabled": _e2e_orchestration_enabled(self.env),
                    "routes_enabled": {
                        "google": _e2e_google_crud_enabled(self.env),
                        "discord": _e2e_discord_crud_enabled(self.env),
                        "notion": _e2e_notion_crud_enabled(self.env),
                    },
                    "scenario_routes_enabled": {
                        "discord_google": _e2e_discord_google_sync_enabled(self.env),
                        "google_discord": _e2e_google_discord_sync_enabled(self.env),
                        "google_notion": _e2e_google_notion_sync_enabled(self.env),
                        "discord_notion": _e2e_discord_notion_sync_enabled(self.env),
                        "qa_notification": _e2e_qa_notification_enabled(self.env),
                        "reminder": _e2e_reminder_enabled(self.env),
                        "notion_cleanup": _e2e_notion_cleanup_enabled(self.env),
                        "webhook_dispatch": _e2e_webhook_simulation_enabled(
                            self.env
                        ),
                    },
                    "services": {
                        service: _manifest_summary(manifests.get(service))
                        for service in ("google", "discord", "notion")
                    },
                    "scenarios": {
                        scenario: _manifest_summary(scenario_manifests.get(scenario))
                        for scenario in (
                            "discord_google",
                            "discord_notion",
                            "google_discord",
                            "google_notion",
                            "qa_notification",
                            "reminder",
                            "notion_cleanup",
                            "webhook_dispatch",
                        )
                    },
                }
            )
        if method != "POST":
            return _json_response({"ok": False, "error": "method_not_allowed"}, status=405)

        run_id = _request_run_id(request)
        if not run_id:
            return _json_response({"ok": False, "error": "invalid_run_id"}, status=400)
        if orchestrated_write_route:
            return await super().fetch(request)

        state = StateStore(self.env)
        if path == _TRIGGER_WEBHOOK_PATH:
            async def dispatch(sync_state, google_applier):
                return await self._run_sync_dispatch(
                    request,
                    sync_state,
                    source="e2e-webhook",
                    google_applier=google_applier,
                )

            try:
                result = await run_webhook_dispatch_probe(
                    self.env,
                    state,
                    dispatch,
                    run_id=run_id,
                )
            except Exception:
                result = {
                    "ok": False,
                    "dirty": True,
                    "error": "e2e_probe_exception",
                    "cleanup_required": True,
                }
            if result.get("ok"):
                status = 200
            elif result.get("dirty") or result.get("error") == "environment_dirty":
                status = 409
            else:
                status = 500
            return _json_response(result, status=status)
        if google_route:
            lock_source = "e2e-google-crud"
        elif google_discord_route:
            lock_source = "e2e-google-discord-sync"
        elif google_notion_route:
            lock_source = "e2e-google-notion-sync"
        elif discord_google_route:
            lock_source = "e2e-discord-google-sync"
        elif discord_notion_route:
            lock_source = "e2e-discord-notion-sync"
        elif qa_notification_route:
            lock_source = "e2e-qa-notification"
        elif reminder_route:
            lock_source = "e2e-reminder"
        elif notion_cleanup_route:
            lock_source = "e2e-notion-cleanup"
        elif webhook_route:
            lock_source = "e2e-webhook-simulation"
        elif discord_route:
            lock_source = "e2e-discord-crud"
        else:
            lock_source = "e2e-notion-crud"
        lock = await self._acquire_sync_lock(source=lock_source)
        if not lock.get("ok"):
            payload = {"ok": False, "error": "e2e_lock_unavailable"}
            for key in ("error", "stage", "error_type"):
                if lock.get(key):
                    payload[f"lock_{key}"] = lock[key]
            return _json_response(
                payload,
                status=409 if lock.get("locked") else 503,
            )
        lock_owner = lock.get("owner")
        try:
            if path == _GOOGLE_CLEANUP_PATH:
                result = await cleanup_google_calendar_crud_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _GOOGLE_CRUD_PATH:
                result = await run_google_calendar_crud_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _GOOGLE_NOTION_CLEANUP_PATH:
                result = await cleanup_google_notion_sync_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _GOOGLE_NOTION_SYNC_PATH:
                result = await run_google_notion_sync_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _GOOGLE_DISCORD_CLEANUP_PATH:
                result = await cleanup_google_discord_sync_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _GOOGLE_DISCORD_SYNC_PATH:
                result = await run_google_discord_sync_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _DISCORD_GOOGLE_CLEANUP_PATH:
                result = await cleanup_discord_google_sync_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _DISCORD_GOOGLE_SYNC_PATH:
                result = await run_discord_google_sync_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _DISCORD_NOTION_CLEANUP_PATH:
                result = await cleanup_discord_notion_sync_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _DISCORD_NOTION_SYNC_PATH:
                result = await run_discord_notion_sync_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _QA_NOTIFICATION_CLEANUP_PATH:
                result = await cleanup_qa_notification_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _QA_NOTIFICATION_PATH:
                result = await run_qa_notification_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _REMINDER_CLEANUP_PATH:
                result = await cleanup_reminder_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _REMINDER_PATH:
                result = await run_reminder_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _NOTION_AUTO_CLEAN_CLEANUP_PATH:
                result = await cleanup_notion_cleanup_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _NOTION_AUTO_CLEAN_PATH:
                result = await run_notion_cleanup_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _TRIGGER_WEBHOOK_CLEANUP_PATH:
                result = await cleanup_webhook_dispatch_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _DISCORD_CLEANUP_PATH:
                result = await cleanup_discord_crud_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            elif path == _DISCORD_CRUD_PATH:
                result = await run_discord_crud_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
            elif path == _NOTION_CLEANUP_PATH:
                result = await cleanup_notion_crud_probe(
                    self.env,
                    state,
                    expected_run_id=run_id,
                )
            else:
                result = await run_notion_crud_probe(
                    self.env,
                    state,
                    run_id=run_id,
                )
        except Exception:
            result = {
                "ok": False,
                "dirty": True,
                "error": "e2e_probe_exception",
                "cleanup_required": True,
            }
        finally:
            if lock_owner:
                await self._release_sync_lock(lock_owner)

        if result.get("ok"):
            status = 200
        elif result.get("dirty") or result.get("error") == "environment_dirty":
            status = 409
        else:
            status = 500
        return _json_response(result, status=status)

    async def scheduled(self, controller, env, ctx):
        """E2E Worker では設定値にかかわらず Cron 実行を無効化する。"""
        return []
