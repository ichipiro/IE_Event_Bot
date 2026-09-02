"""Google Calendar→Notion適用処理の自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import quote
from uuid import uuid4

from e2e_google_probe import (
    _calendar_fingerprint,
    _delete_event,
    _event_collection_url,
    _event_item_url,
    _event_matches,
    _google_request,
)
from e2e_notion_probe import (
    _EVENT_SCHEMA,
    _canonical_id,
    _event_page_matches,
    _find_page_by_marker,
    _fingerprint,
    _page_archived,
    _page_database_id,
    _property_text,
    _request_stage,
    _verify_database,
)
from google_apply_sync import apply_google_events
from google_auth import get_google_access_token


GOOGLE_NOTION_SYNC_MANIFEST_SERVICE = "google_notion"

_JST = timezone(timedelta(hours=9))
_CLEANUP_MAX_ATTEMPTS = 4
_NOTION_PROPERTY_DEFAULTS = {
    "NOTION_PROP_TITLE": "イベント名",
    "NOTION_PROP_CONTENT": "内容",
    "NOTION_PROP_DATE": "日時",
    "NOTION_PROP_MESSAGE_ID": "メッセージID",
    "NOTION_PROP_CREATOR_ID": "作成者ID",
    "NOTION_PROP_PAGE_ID": "ページID",
    "NOTION_PROP_EVENT_URL": "イベントURL",
    "NOTION_PROP_GOOGLE_EVENT_ID": "GoogleイベントID",
    "NOTION_PROP_LOCATION": "場所",
}


class _EphemeralApplyState:
    """通常同期のKVマップと再試行キューを変更させない状態境界。"""

    @staticmethod
    def enabled() -> bool:
        return False


def _env_text(env, key: str) -> str:
    value = getattr(env, key, None)
    return "" if value is None else str(value).strip()


def _explicitly_disabled(env, key: str) -> bool:
    value = getattr(env, key, None)
    return value is not None and str(value).strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"E2E-{timestamp}-{uuid4().hex[:8]}"


def _event_payload(run_id: str, event_id: str) -> dict:
    start = (datetime.now(_JST) + timedelta(days=7)).replace(second=0, microsecond=0)
    end = start + timedelta(minutes=30)
    return {
        "id": event_id,
        "summary": f"[E2E] Google to Notion sync {run_id}",
        "description": f"IE Event Bot application E2E fixture for {run_id}.",
        "location": "E2E isolated location",
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        "extendedProperties": {
            "private": {"ie_event_bot_e2e_run": run_id},
        },
        "reminders": {"useDefault": False},
        "transparency": "transparent",
        "visibility": "private",
    }


def _target_fingerprints(calendar_id: str, database_id: str) -> dict[str, str]:
    return {
        "calendar_id_sha256": _calendar_fingerprint(calendar_id),
        "notion_database_id_sha256": _fingerprint(_canonical_id(database_id)),
    }


def _notion_page_is_owned(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    google_event_id: str,
    run_id: str,
) -> bool:
    return (
        _canonical_id(page.get("id")) == _canonical_id(page_id)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and _property_text(page, "GoogleイベントID", "rich_text")
        == google_event_id
        and _property_text(page, "イベント名", "title")
        == f"[E2E] Google to Notion sync {run_id}"
    )


async def _archive_owned_notion_page(
    env,
    database_id: str,
    page_id: str,
    google_event_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
) -> tuple[bool, int, int, str]:
    path = f"/pages/{quote(page_id, safe='')}"
    last_status = 0
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        status, page = await _request_stage(
            env,
            stages,
            retries,
            "notion_cleanup_verify",
            "GET",
            path,
        )
        last_status = status
        if status == 404:
            return True, attempt, status, ""
        if not (200 <= status < 300) or not isinstance(page, dict):
            continue
        if not _notion_page_is_owned(
            page,
            page_id=page_id,
            database_id=database_id,
            google_event_id=google_event_id,
            run_id=run_id,
        ):
            return False, attempt, status, "cleanup_target_mismatch"
        if _page_archived(page):
            return True, attempt, status, ""

        status, archived = await _request_stage(
            env,
            stages,
            retries,
            "notion_archive",
            "PATCH",
            path,
            {"archived": True},
        )
        last_status = status
        if (
            200 <= status < 300
            and isinstance(archived, dict)
            and _notion_page_is_owned(
                archived,
                page_id=page_id,
                database_id=database_id,
                google_event_id=google_event_id,
                run_id=run_id,
            )
            and _page_archived(archived)
        ):
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, "notion_page_archive_failed"


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    rate_limit_retries: dict[str, int],
    calendar_id: str,
    database_id: str,
    google_event_id: str,
    notion_page_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(calendar_id, database_id)
    fingerprints["google_event_id_sha256"] = sha256(
        google_event_id.encode("utf-8")
    ).hexdigest()
    if notion_page_id:
        fingerprints["notion_page_id_sha256"] = _fingerprint(notion_page_id)
    return {
        "version": 1,
        "kind": "google_notion_sync",
        "dirty": False,
        "last_run_id": run_id,
        "outcome": outcome,
        "cleanup_attempts": cleanup_attempts,
        "stages": dict(stages),
        "rate_limit_retries": dict(rate_limit_retries),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "resource_fingerprints": fingerprints,
    }


async def _cleanup_resources(
    env,
    manifest: dict,
    bearer_token: str | None,
) -> dict:
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    run_id = str(manifest.get("run_id") or "")
    google_event_id = str(manifest.get("google_event_id") or "")
    notion_page_id = str(manifest.get("notion_page_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}

    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    notion_error = ""
    if not notion_page_id and google_event_id:
        notion_page_id, notion_error = await _find_page_by_marker(
            env,
            database_id,
            "GoogleイベントID",
            google_event_id,
            stages,
            retries,
            "notion_cleanup_find",
            "notion_page",
        )
        if (
            not notion_error
            and not notion_page_id
            and create_attempted.get("notion_page") is True
        ):
            notion_error = "notion_page_ownership_unresolved"

    notion_ok = not notion_error
    notion_attempts = 1
    if notion_ok and notion_page_id:
        notion_ok, notion_attempts, _, notion_error = await _archive_owned_notion_page(
            env,
            database_id,
            notion_page_id,
            google_event_id,
            run_id,
            stages,
            retries,
        )

    google_ok = True
    google_attempts = 1
    google_error = ""
    if create_attempted.get("google_event") is True:
        if not bearer_token:
            google_ok = False
            google_error = "missing_google_access_token"
            stages["google_delete"] = 0
        else:
            google_ok, google_attempts, delete_status, google_error = await _delete_event(
                calendar_id,
                google_event_id,
                run_id,
                bearer_token,
            )
            stages["google_delete"] = delete_status

    return {
        "ok": notion_ok and google_ok,
        "attempts": max(notion_attempts, google_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": notion_error or google_error,
        "notion_page_id": notion_page_id,
    }


def _configuration_error(env) -> str:
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    if not calendar_id:
        return "missing_google_calendar_id"
    if calendar_id.lower() == "primary":
        return "primary_calendar_forbidden"
    if _env_text(env, "NOTION_EVENT_ID"):
        return "external_notion_database_must_be_disabled"
    if not _explicitly_disabled(env, "DISCORD_SYNC_ENABLED"):
        return "discord_sync_must_be_disabled"
    if not _env_text(env, "NOTION_TOKEN"):
        return "missing_notion_token"
    if not _canonical_id(_env_text(env, "NOTION_EVENT_INTERNAL_ID")):
        return "invalid_notion_event_database_id"
    if any(
        _env_text(env, key) and _env_text(env, key) != expected
        for key, expected in _NOTION_PROPERTY_DEFAULTS.items()
    ):
        return "notion_property_overrides_forbidden"
    return ""


async def run_google_notion_sync_probe(env, state, run_id: str | None = None) -> dict:
    """専用Calendar eventを既存適用処理でNotionへ反映し、両方を削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}

    current_manifest = await state.get_e2e_manifest(
        GOOGLE_NOTION_SYNC_MANIFEST_SERVICE
    )
    if isinstance(current_manifest, dict) and current_manifest.get("dirty") is True:
        return {
            "ok": False,
            "dirty": True,
            "error": "environment_dirty",
            "cleanup_required": True,
        }

    configuration_error = _configuration_error(env)
    if configuration_error:
        return {"ok": False, "dirty": False, "error": configuration_error}

    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    target_error = await _verify_database(
        env,
        database_id,
        _EVENT_SCHEMA,
        stages,
        retries,
        "target_notion_database",
        "notion_event",
    )
    if target_error:
        return {
            "ok": False,
            "dirty": False,
            "error": target_error,
            "stages": stages,
        }

    try:
        bearer_token = await get_google_access_token(env, state)
    except Exception:
        bearer_token = None
    if not bearer_token:
        return {
            "ok": False,
            "dirty": False,
            "error": "missing_google_access_token",
            "stages": stages,
        }

    run_id = str(run_id or "").strip() or _new_run_id()
    google_event_id = f"iee2e{uuid4().hex}"
    event = _event_payload(run_id, google_event_id)
    preexisting_page_id, precheck_error = await _find_page_by_marker(
        env,
        database_id,
        "GoogleイベントID",
        google_event_id,
        stages,
        retries,
        "notion_precheck",
        "notion_page",
    )
    if precheck_error:
        return {
            "ok": False,
            "dirty": False,
            "error": precheck_error,
            "stages": stages,
        }
    if preexisting_page_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "notion_event_id_collision",
            "stages": stages,
        }
    manifest = {
        "version": 1,
        "kind": "google_notion_sync",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(calendar_id, database_id),
        "google_event_id": google_event_id,
        "create_attempted": {"google_event": False, "notion_page": False},
        "stage": "planned",
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)

    error = ""
    notion_page_id = ""
    manifest["create_attempted"]["google_event"] = True
    manifest["stage"] = "google_create_started"
    await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)
    create_status, created = await _google_request(
        "POST",
        f"{_event_collection_url(calendar_id)}?sendUpdates=none",
        bearer_token,
        event,
    )
    stages["google_create"] = create_status
    if not (200 <= create_status < 300) or not _event_matches(
        created,
        event_id=google_event_id,
        run_id=run_id,
        summary=str(event["summary"]),
    ):
        error = "google_create_verification_failed"
    else:
        manifest["stage"] = "google_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)

    google_event = created
    if not error:
        read_status, google_event = await _google_request(
            "GET",
            _event_item_url(calendar_id, google_event_id),
            bearer_token,
        )
        stages["google_read"] = read_status
        if not (200 <= read_status < 300) or not _event_matches(
            google_event,
            event_id=google_event_id,
            run_id=run_id,
            summary=str(event["summary"]),
        ):
            error = "google_read_verification_failed"

    if not error:
        manifest["create_attempted"]["notion_page"] = True
        manifest["stage"] = "application_apply_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)
        try:
            apply_result = await apply_google_events(
                env,
                _EphemeralApplyState(),
                [google_event],
            )
        except Exception:
            apply_result = {"ok": False}
        apply_ok = (
            isinstance(apply_result, dict)
            and apply_result.get("ok") is True
            and apply_result.get("processed") == 1
            and apply_result.get("pending_events") == 0
            and apply_result.get("error_count") == 0
        )
        stages["application_apply"] = 200 if apply_ok else 500
        if not apply_ok:
            error = "google_notion_apply_failed"

    if not error:
        notion_page_id, find_error = await _find_page_by_marker(
            env,
            database_id,
            "GoogleイベントID",
            google_event_id,
            stages,
            retries,
            "notion_find",
            "notion_page",
        )
        if find_error:
            error = find_error
        elif not notion_page_id:
            error = "notion_page_ownership_unresolved"
        else:
            manifest["notion_page_id"] = notion_page_id
            manifest["stage"] = "notion_page_found"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)

    if not error:
        read_status, notion_page = await _request_stage(
            env,
            stages,
            retries,
            "notion_read",
            "GET",
            f"/pages/{notion_page_id}",
        )
        if not (
            200 <= read_status < 300
            and isinstance(notion_page, dict)
            and _event_page_matches(
                notion_page,
                page_id=notion_page_id,
                database_id=database_id,
                marker=google_event_id,
                title=str(event["summary"]),
                content=str(event["description"]),
                location=str(event["location"]),
                page_uuid=notion_page_id,
            )
            and _property_text(
                notion_page,
                "GoogleイベントID",
                "rich_text",
            )
            == google_event_id
        ):
            error = "notion_page_verification_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest, bearer_token)
    stages.update(cleanup["stages"])
    retries.update(cleanup["rate_limit_retries"])
    cleanup_ok = cleanup["ok"] is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    notion_page_id = str(cleanup.get("notion_page_id") or notion_page_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            GOOGLE_NOTION_SYNC_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                calendar_id=calendar_id,
                database_id=database_id,
                google_event_id=google_event_id,
                notion_page_id=notion_page_id,
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
    else:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or error or "cleanup_failed")
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["stages"] = dict(stages)
        manifest["rate_limit_retries"] = dict(retries)
        if notion_page_id:
            manifest["notion_page_id"] = notion_page_id
        await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)

    result = {
        "ok": operation_ok and cleanup_ok,
        "dirty": not cleanup_ok,
        "run_id": run_id,
        "stages": stages,
        "cleanup": {"ok": cleanup_ok, "attempts": cleanup_attempts},
    }
    if error:
        result["error"] = error
    if not cleanup_ok:
        result["error"] = str(cleanup.get("error") or error or "cleanup_failed")
    return result


async def cleanup_google_notion_sync_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """run IDと対象fingerprint確認後にdirtyな両資源のcleanupを再実行する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}

    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "google_notion_sync":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    google_event_id = str(manifest.get("google_event_id") or "")
    if (
        not run_id
        or not google_event_id
        or not calendar_id
        or calendar_id.lower() == "primary"
        or not _canonical_id(database_id)
        or manifest.get("target_fingerprints")
        != _target_fingerprints(calendar_id, database_id)
    ):
        return {
            "ok": False,
            "dirty": True,
            "error": "dirty_manifest_target_mismatch",
        }

    try:
        bearer_token = await get_google_access_token(env, state)
    except Exception:
        bearer_token = None
    cleanup = await _cleanup_resources(env, manifest, bearer_token)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    stages.update(cleanup["stages"])
    retries = manifest.get("rate_limit_retries")
    if not isinstance(retries, dict):
        retries = {}
    retries.update(cleanup["rate_limit_retries"])
    attempts = int(cleanup.get("attempts") or 1)
    notion_page_id = str(cleanup.get("notion_page_id") or "")

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if notion_page_id:
            manifest["notion_page_id"] = notion_page_id
        await state.put_e2e_manifest(GOOGLE_NOTION_SYNC_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        GOOGLE_NOTION_SYNC_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            calendar_id=calendar_id,
            database_id=database_id,
            google_event_id=google_event_id,
            notion_page_id=notion_page_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
