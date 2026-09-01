import json
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from workers import fetch as _runtime_fetch

from google_auth import get_google_access_token


GOOGLE_CRUD_MANIFEST_SERVICE = "google"

_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3/calendars"
_CLEANUP_MAX_ATTEMPTS = 4
_JST = timezone(timedelta(hours=9))


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
    if value is None:
        return ""
    return str(value).strip()


def _event_collection_url(calendar_id: str) -> str:
    return f"{_CALENDAR_API_BASE}/{quote(calendar_id, safe='')}/events"


def _event_item_url(calendar_id: str, event_id: str) -> str:
    return (
        f"{_event_collection_url(calendar_id)}/"
        f"{quote(event_id, safe='')}"
    )


def _calendar_fingerprint(calendar_id: str) -> str:
    return sha256(calendar_id.encode("utf-8")).hexdigest()


async def _google_request(
    method: str,
    url: str,
    bearer_token: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    """Google Calendar APIへ小さなJSONリクエストを送り、statusと辞書を返す。"""
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json",
    }
    options: dict[str, Any] = {
        "method": method,
        "headers": headers,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
        options["body"] = json.dumps(payload, ensure_ascii=False)
    try:
        response = await fetch(url, options)
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


def _event_payload(run_id: str, event_id: str) -> dict:
    start = (datetime.now(_JST) + timedelta(days=7)).replace(
        second=0,
        microsecond=0,
    )
    end = start + timedelta(minutes=30)
    return {
        "id": event_id,
        "summary": f"[E2E] Google CRUD {run_id}",
        "description": "IE Event Bot E2E automated fixture; safe to delete.",
        "location": "E2E initial location",
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "Asia/Tokyo",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "Asia/Tokyo",
        },
        "extendedProperties": {
            "private": {
                "ie_event_bot_e2e_run": run_id,
            }
        },
        "reminders": {"useDefault": False},
        "transparency": "transparent",
        "visibility": "private",
    }


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"E2E-{timestamp}-{uuid4().hex[:8]}"


def _event_has_run(event: dict, *, event_id: str, run_id: str) -> bool:
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return (
        str(event.get("id") or "") == event_id
        and str(private.get("ie_event_bot_e2e_run") or "") == run_id
    )


def _event_matches(event: dict, *, event_id: str, run_id: str, summary: str) -> bool:
    return (
        _event_has_run(event, event_id=event_id, run_id=run_id)
        and str(event.get("summary") or "") == summary
    )


async def _delete_event(
    calendar_id: str,
    event_id: str,
    run_id: str,
    bearer_token: str,
) -> tuple[bool, int, int, str]:
    """run ID一致を確認してから削除し、失敗時は3回まで再試行する。"""
    item_url = _event_item_url(calendar_id, event_id)
    delete_url = f"{item_url}?sendUpdates=none"
    last_status = 0
    last_error = "google_cleanup_verify_failed"
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        verify_status, event = await _google_request("GET", item_url, bearer_token)
        last_status = verify_status
        if verify_status in (404, 410):
            return True, attempt, verify_status, ""
        if not (200 <= verify_status < 300):
            continue
        if not _event_has_run(event, event_id=event_id, run_id=run_id):
            return False, attempt, verify_status, "cleanup_target_mismatch"

        last_status, _ = await _google_request("DELETE", delete_url, bearer_token)
        last_error = "google_delete_failed"
        if 200 <= last_status < 300 or last_status in (404, 410):
            return True, attempt, last_status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, last_error


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    calendar_id: str,
    event_id: str,
    started_at: str | None,
) -> dict:
    return {
        "version": 1,
        "kind": "google_calendar_event",
        "dirty": False,
        "last_run_id": run_id,
        "outcome": outcome,
        "cleanup_attempts": cleanup_attempts,
        "stages": dict(stages),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "resource_fingerprints": {
            "calendar_id_sha256": _calendar_fingerprint(calendar_id),
            "google_event_id_sha256": sha256(event_id.encode("utf-8")).hexdigest(),
        },
    }


async def run_google_calendar_crud_probe(
    env,
    state,
    run_id: str | None = None,
) -> dict:
    """専用Calendarでeventのcreate/read/update/deleteを確認して即時削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}
    if await state.get_legacy_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE) is not None:
        return {
            "ok": False,
            "dirty": True,
            "error": "legacy_e2e_manifest_review_required",
        }

    current_manifest = await state.get_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE)
    if isinstance(current_manifest, dict) and current_manifest.get("dirty") is True:
        return {
            "ok": False,
            "dirty": True,
            "error": "environment_dirty",
            "cleanup_required": True,
        }

    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    if not calendar_id:
        return {"ok": False, "dirty": False, "error": "missing_google_calendar_id"}
    if calendar_id.lower() == "primary":
        return {"ok": False, "dirty": False, "error": "primary_calendar_forbidden"}

    try:
        bearer_token = await get_google_access_token(env, state)
    except Exception:
        bearer_token = None
    if not bearer_token:
        return {"ok": False, "dirty": False, "error": "missing_google_access_token"}

    run_id = str(run_id or "").strip() or _new_run_id()
    event_id = f"iee2e{uuid4().hex}"
    event = _event_payload(run_id, event_id)
    manifest = {
        "version": 1,
        "kind": "google_calendar_event",
        "dirty": True,
        "run_id": run_id,
        "calendar_id_sha256": _calendar_fingerprint(calendar_id),
        "event_id": event_id,
        "stage": "planned",
        "stages": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)

    stages: dict[str, int] = {}
    error = ""
    collection_url = f"{_event_collection_url(calendar_id)}?sendUpdates=none"
    item_url = _event_item_url(calendar_id, event_id)

    create_status, created = await _google_request(
        "POST",
        collection_url,
        bearer_token,
        event,
    )
    stages["create"] = create_status
    if not (200 <= create_status < 300):
        error = f"google_create_failed_{create_status}"
    elif not _event_matches(
        created,
        event_id=event_id,
        run_id=run_id,
        summary=str(event["summary"]),
    ):
        error = "google_create_verification_failed"
    else:
        manifest["stage"] = "created"
        await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)

    if not error:
        read_status, read_event = await _google_request("GET", item_url, bearer_token)
        stages["read"] = read_status
        if not (200 <= read_status < 300) or not _event_matches(
            read_event,
            event_id=event_id,
            run_id=run_id,
            summary=str(event["summary"]),
        ):
            error = f"google_read_verification_failed_{read_status}"
        else:
            manifest["stage"] = "read"
            await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)

    updated_summary = f"[E2E] Google CRUD updated {run_id}"
    if not error:
        updated_event = dict(event)
        updated_event["summary"] = updated_summary
        updated_event["location"] = "E2E updated location"
        update_status, updated = await _google_request(
            "PUT",
            f"{item_url}?sendUpdates=none",
            bearer_token,
            updated_event,
        )
        stages["update"] = update_status
        if not (200 <= update_status < 300) or not _event_matches(
            updated,
            event_id=event_id,
            run_id=run_id,
            summary=updated_summary,
        ):
            error = f"google_update_verification_failed_{update_status}"
        else:
            manifest["stage"] = "updated"
            await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)

    if not error:
        read_status, updated_read = await _google_request("GET", item_url, bearer_token)
        stages["read_updated"] = read_status
        if not (200 <= read_status < 300) or not _event_matches(
            updated_read,
            event_id=event_id,
            run_id=run_id,
            summary=updated_summary,
        ):
            error = f"google_updated_read_verification_failed_{read_status}"
        else:
            manifest["stage"] = "verified"
            await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)

    operation_ok = not error
    cleanup_ok, cleanup_attempts, delete_status, cleanup_error = await _delete_event(
        calendar_id,
        event_id,
        run_id,
        bearer_token,
    )
    stages["delete"] = delete_status
    if cleanup_ok:
        await state.put_e2e_manifest(
            GOOGLE_CRUD_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                calendar_id=calendar_id,
                event_id=event_id,
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
    else:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = error or cleanup_error
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["cleanup_status"] = delete_status
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)

    result = {
        "ok": operation_ok and cleanup_ok,
        "dirty": not cleanup_ok,
        "run_id": run_id,
        "stages": stages,
        "cleanup": {
            "ok": cleanup_ok,
            "attempts": cleanup_attempts,
        },
    }
    if error:
        result["error"] = error
    elif not cleanup_ok:
        result["error"] = (
            cleanup_error
            if cleanup_error == "cleanup_target_mismatch"
            else f"{cleanup_error}_{delete_status}"
        )
    return result


async def cleanup_google_calendar_crud_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """本人確認後にdirty manifestのevent削除を再実行する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}
    if await state.get_legacy_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE) is not None:
        return {
            "ok": False,
            "dirty": True,
            "error": "legacy_e2e_manifest_review_required",
        }
    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "google_calendar_event":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    manifest_calendar_fingerprint = str(manifest.get("calendar_id_sha256") or "")
    event_id = str(manifest.get("event_id") or "")
    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and run_id != expected_run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    if not calendar_id or calendar_id.lower() == "primary":
        return {"ok": False, "dirty": True, "error": "invalid_google_calendar_id"}
    if (
        not event_id
        or not run_id
        or manifest_calendar_fingerprint != _calendar_fingerprint(calendar_id)
    ):
        return {"ok": False, "dirty": True, "error": "dirty_manifest_target_mismatch"}

    try:
        bearer_token = await get_google_access_token(env, state)
    except Exception:
        bearer_token = None
    if not bearer_token:
        return {"ok": False, "dirty": True, "error": "missing_google_access_token"}

    cleanup_ok, attempts, delete_status, cleanup_error = await _delete_event(
        calendar_id,
        event_id,
        run_id,
        bearer_token,
    )
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    stages["delete"] = delete_status
    if not cleanup_ok:
        manifest["stage"] = "cleanup_failed"
        manifest["cleanup_attempts"] = attempts
        manifest["cleanup_status"] = delete_status
        manifest["failure"] = cleanup_error
        manifest["stages"] = stages
        await state.put_e2e_manifest(GOOGLE_CRUD_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": (
                cleanup_error
                if cleanup_error == "cleanup_target_mismatch"
                else f"{cleanup_error}_{delete_status}"
            ),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        GOOGLE_CRUD_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            calendar_id=calendar_id,
            event_id=event_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
