"""Google CalendarからE2E Workerへの実Webhook到達を短命watchで検証する。"""

import asyncio
import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import quote, urlparse

from workers import fetch as _runtime_fetch

from google_auth import get_google_access_token


GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE = "webhook_delivery"
GOOGLE_WEBHOOK_DELIVERY_MANIFEST_KIND = "google_webhook_delivery"

_RUN_ID_PATTERN = re.compile(r"^E2E-\d{8}T\d{6}Z-[0-9a-f]{8}$")
_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3/calendars"
_CHANNELS_STOP_URL = "https://www.googleapis.com/calendar/v3/channels/stop"
_WATCH_TTL_SECONDS = 600
_POLL_ATTEMPTS = 20
_POLL_DELAY_SECONDS = 1


class _EphemeralAuthState:
    """Google認証cacheを共有KVへ書かず、1リクエスト内に閉じ込める。"""

    def __init__(self) -> None:
        self._text: dict[str, str] = {}

    @staticmethod
    def enabled() -> bool:
        return True

    async def get_text(self, key: str) -> str | None:
        return self._text.get(key)

    async def put_text_if_changed(self, key: str, value: str) -> bool:
        next_value = str(value)
        changed = self._text.get(key) != next_value
        self._text[key] = next_value
        return changed


async def fetch(url: str, options: dict[str, Any] | None = None) -> Any:
    """Workers fetch の呼び出し形式差を吸収する。"""
    opts = options or {}
    try:
        return await _runtime_fetch(
            url,
            method=opts.get("method"),
            headers=opts.get("headers"),
            body=opts.get("body"),
        )
    except TypeError:
        return await _runtime_fetch(url, opts)


def _env_text(env, key: str) -> str:
    value = getattr(env, key, None)
    return str(value or "").strip()


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _channel_id(run_id: str) -> str:
    return f"e2e-webhook-{_fingerprint(run_id)[:40]}"


def _valid_webhook_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and parsed.path == "/gcal/webhook"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


async def _google_request(
    method: str,
    url: str,
    bearer_token: str,
    payload: dict,
) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        response = await fetch(
            url,
            {
                "method": method,
                "headers": headers,
                "body": json.dumps(payload, ensure_ascii=False),
            },
        )
        status = int(response.status)
        text = await response.text()
    except Exception:
        return 0, {}
    if not text:
        return status, {}
    try:
        data = json.loads(text)
    except Exception:
        return status, {}
    return status, data if isinstance(data, dict) else {}


def _watch_url(calendar_id: str) -> str:
    encoded = quote(calendar_id, safe="")
    return f"{_CALENDAR_API_BASE}/{encoded}/events/watch"


def _dirty_manifest(
    *,
    run_id: str,
    calendar_id: str,
    webhook_url: str,
    channel_id: str,
) -> dict:
    return {
        "version": 1,
        "kind": GOOGLE_WEBHOOK_DELIVERY_MANIFEST_KIND,
        "dirty": True,
        "run_id": run_id,
        "channel_id": channel_id,
        "calendar_id_sha256": _fingerprint(calendar_id),
        "webhook_url_sha256": _fingerprint(webhook_url),
        "channel_id_sha256": _fingerprint(channel_id),
        "create_attempted": {"watch_channel": True},
        "stage": "watch_create_started",
        "stages": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _owned_manifest(
    manifest: dict | None,
    *,
    run_id: str,
    calendar_id: str,
    webhook_url: str,
) -> tuple[bool, str, str]:
    if not isinstance(manifest, dict):
        return False, "", ""
    channel_id = str(manifest.get("channel_id") or "").strip()
    resource_id = str(manifest.get("resource_id") or "").strip()
    notification = manifest.get("notification")
    if not resource_id and isinstance(notification, dict):
        resource_id = str(notification.get("resource_id") or "").strip()
    valid = bool(
        manifest.get("kind") == GOOGLE_WEBHOOK_DELIVERY_MANIFEST_KIND
        and manifest.get("dirty") is True
        and str(manifest.get("run_id") or "") == run_id
        and channel_id
        and len(channel_id) <= 64
        and str(manifest.get("calendar_id_sha256") or "")
        == _fingerprint(calendar_id)
        and str(manifest.get("webhook_url_sha256") or "")
        == _fingerprint(webhook_url)
        and str(manifest.get("channel_id_sha256") or "")
        == _fingerprint(channel_id)
    )
    return valid, channel_id, resource_id


def _notification_received(manifest: dict | None, resource_id: str) -> bool:
    notification = manifest.get("notification") if isinstance(manifest, dict) else None
    return bool(
        isinstance(notification, dict)
        and str(notification.get("resource_id") or "") == resource_id
        and notification.get("resource_state") == "sync"
        and str(notification.get("message_number") or "") == "1"
    )


async def _wait_for_notification(state, resource_id: str) -> dict | None:
    for attempt in range(_POLL_ATTEMPTS):
        manifest = await state.get_e2e_manifest(
            GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE
        )
        if _notification_received(manifest, resource_id):
            return manifest
        if attempt + 1 < _POLL_ATTEMPTS:
            await asyncio.sleep(_POLL_DELAY_SECONDS)
    return None


def _clean_manifest(
    *,
    run_id: str,
    outcome: str,
    stages: dict[str, int],
    calendar_id: str,
    webhook_url: str,
    channel_id: str,
    resource_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = {
        "calendar_id_sha256": _fingerprint(calendar_id),
        "webhook_url_sha256": _fingerprint(webhook_url),
        "watch_channel_id_sha256": _fingerprint(channel_id),
    }
    if resource_id:
        fingerprints["watch_resource_id_sha256"] = _fingerprint(resource_id)
    return {
        "version": 1,
        "kind": GOOGLE_WEBHOOK_DELIVERY_MANIFEST_KIND,
        "dirty": False,
        "last_run_id": run_id,
        "outcome": outcome,
        "cleanup_attempts": 1,
        "stages": dict(stages),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "resource_fingerprints": fingerprints,
    }


async def _access_token(env) -> str:
    try:
        token = await get_google_access_token(env, _EphemeralAuthState())
    except Exception:
        token = None
    return str(token or "").strip()


async def _stop_watch(
    bearer_token: str,
    channel_id: str,
    resource_id: str,
) -> tuple[bool, int]:
    status, _ = await _google_request(
        "POST",
        _CHANNELS_STOP_URL,
        bearer_token,
        {"id": channel_id, "resourceId": resource_id},
    )
    return 200 <= status < 300 or status in (404, 410), status


async def _finalize_stopped_watch(
    state,
    *,
    run_id: str,
    calendar_id: str,
    webhook_url: str,
    channel_id: str,
    resource_id: str,
    operation_ok: bool,
    stop_status: int,
) -> dict:
    latest = await state.get_e2e_manifest(GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE)
    owned, latest_channel_id, latest_resource_id = _owned_manifest(
        latest,
        run_id=run_id,
        calendar_id=calendar_id,
        webhook_url=webhook_url,
    )
    if (
        not owned
        or latest_channel_id != channel_id
        or latest_resource_id != resource_id
    ):
        return {
            "ok": False,
            "dirty": True,
            "error": "dirty_manifest_target_mismatch",
        }
    stages = latest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    stages["watch_stop"] = stop_status
    clean = _clean_manifest(
        run_id=run_id,
        outcome="passed" if operation_ok else "failed_clean",
        stages=stages,
        calendar_id=calendar_id,
        webhook_url=webhook_url,
        channel_id=channel_id,
        resource_id=resource_id,
        started_at=str(latest.get("created_at") or "") or None,
    )
    await state.put_e2e_manifest(GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE, clean)
    return {
        "ok": operation_ok,
        "dirty": False,
        "run_id": run_id,
        "stages": stages,
        "cleanup": {"ok": True, "attempts": 1},
        **({} if operation_ok else {"error": "google_webhook_delivery_not_observed"}),
    }


async def run_google_webhook_delivery_probe(
    env,
    state,
    run_id: str,
) -> dict:
    """短命watchの初回sync通知が専用Workerへ届くことだけを検証する。"""
    run_id = str(run_id or "").strip()
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        return {"ok": False, "dirty": False, "error": "invalid_run_id"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}
    current = await state.get_e2e_manifest(GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE)
    if isinstance(current, dict) and current.get("dirty") is True:
        return {
            "ok": False,
            "dirty": True,
            "error": "environment_dirty",
            "cleanup_required": True,
        }

    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    webhook_url = _env_text(env, "GCAL_WEBHOOK_URL")
    channel_token = _env_text(env, "GCAL_WEBHOOK_TOKEN")
    if not calendar_id:
        return {"ok": False, "dirty": False, "error": "missing_google_calendar_id"}
    if calendar_id.lower() == "primary":
        return {"ok": False, "dirty": False, "error": "primary_calendar_forbidden"}
    if not _valid_webhook_url(webhook_url):
        return {"ok": False, "dirty": False, "error": "invalid_gcal_webhook_url"}
    if not channel_token:
        return {"ok": False, "dirty": False, "error": "missing_gcal_webhook_token"}
    if len(channel_token) > 256:
        return {"ok": False, "dirty": False, "error": "gcal_webhook_token_too_long"}
    bearer_token = await _access_token(env)
    if not bearer_token:
        return {"ok": False, "dirty": False, "error": "missing_google_access_token"}

    channel_id = _channel_id(run_id)
    manifest = _dirty_manifest(
        run_id=run_id,
        calendar_id=calendar_id,
        webhook_url=webhook_url,
        channel_id=channel_id,
    )
    await state.put_e2e_manifest(GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE, manifest)

    watch_status, watch_data = await _google_request(
        "POST",
        _watch_url(calendar_id),
        bearer_token,
        {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,
            "token": channel_token,
            "params": {"ttl": str(_WATCH_TTL_SECONDS)},
        },
    )
    response_channel_id = str(watch_data.get("id") or "").strip()
    response_resource_id = str(watch_data.get("resourceId") or "").strip()
    expiration = str(watch_data.get("expiration") or "").strip()

    if 400 <= watch_status < 500:
        stages = {"watch_create": watch_status}
        await state.put_e2e_manifest(
            GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE,
            _clean_manifest(
                run_id=run_id,
                outcome="failed_clean",
                stages=stages,
                calendar_id=calendar_id,
                webhook_url=webhook_url,
                channel_id=channel_id,
                resource_id="",
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
        return {
            "ok": False,
            "dirty": False,
            "run_id": run_id,
            "stages": stages,
            "cleanup": {"ok": True, "attempts": 0},
            "error": f"google_watch_create_failed_{watch_status}",
        }

    resource_id = response_resource_id
    if 200 <= watch_status < 300 and response_channel_id == channel_id and resource_id:
        try:
            await state.attach_e2e_webhook_watch(
                run_id=run_id,
                channel_id=channel_id,
                resource_id=resource_id,
                expiration=expiration,
                watch_status=watch_status,
            )
        except Exception:
            pass
    else:
        latest = await state.get_e2e_manifest(
            GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE
        )
        owned, _, notified_resource_id = _owned_manifest(
            latest,
            run_id=run_id,
            calendar_id=calendar_id,
            webhook_url=webhook_url,
        )
        if owned and notified_resource_id:
            resource_id = notified_resource_id

    if not resource_id:
        return {
            "ok": False,
            "dirty": True,
            "run_id": run_id,
            "stages": {"watch_create": watch_status},
            "cleanup": {"ok": False, "attempts": 0},
            "error": "google_watch_ownership_unresolved",
            "cleanup_required": True,
        }

    observed = await _wait_for_notification(state, resource_id)
    stop_ok, stop_status = await _stop_watch(bearer_token, channel_id, resource_id)
    if not stop_ok:
        return {
            "ok": False,
            "dirty": True,
            "run_id": run_id,
            "stages": {
                "watch_create": watch_status,
                "watch_stop": stop_status,
            },
            "cleanup": {"ok": False, "attempts": 1},
            "error": f"google_watch_stop_failed_{stop_status}",
            "cleanup_required": True,
        }
    operation_ok = bool(
        200 <= watch_status < 300
        and response_channel_id == channel_id
        and observed is not None
    )
    return await _finalize_stopped_watch(
        state,
        run_id=run_id,
        calendar_id=calendar_id,
        webhook_url=webhook_url,
        channel_id=channel_id,
        resource_id=resource_id,
        operation_ok=operation_ok,
        stop_status=stop_status,
    )


async def cleanup_google_webhook_delivery_probe(
    env,
    state,
    expected_run_id: str,
) -> dict:
    """run所有権とfingerprintが一致する短命watchだけを停止する。"""
    expected_run_id = str(expected_run_id or "").strip()
    if not _RUN_ID_PATTERN.fullmatch(expected_run_id):
        return {"ok": False, "dirty": True, "error": "invalid_run_id"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}
    manifest = await state.get_e2e_manifest(GOOGLE_WEBHOOK_DELIVERY_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}

    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    webhook_url = _env_text(env, "GCAL_WEBHOOK_URL")
    owned, channel_id, resource_id = _owned_manifest(
        manifest,
        run_id=expected_run_id,
        calendar_id=calendar_id,
        webhook_url=webhook_url,
    )
    if not owned:
        return {
            "ok": False,
            "dirty": True,
            "error": "dirty_manifest_target_mismatch",
        }
    if not resource_id:
        return {
            "ok": False,
            "dirty": True,
            "error": "google_watch_ownership_unresolved",
        }
    bearer_token = await _access_token(env)
    if not bearer_token:
        return {"ok": False, "dirty": True, "error": "missing_google_access_token"}
    stop_ok, stop_status = await _stop_watch(bearer_token, channel_id, resource_id)
    if not stop_ok:
        return {
            "ok": False,
            "dirty": True,
            "error": f"google_watch_stop_failed_{stop_status}",
            "cleanup": {"ok": False, "attempts": 1},
        }
    finalized = await _finalize_stopped_watch(
        state,
        run_id=expected_run_id,
        calendar_id=calendar_id,
        webhook_url=webhook_url,
        channel_id=channel_id,
        resource_id=resource_id,
        operation_ok=False,
        stop_status=stop_status,
    )
    if finalized.get("dirty") is not False:
        return finalized
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
