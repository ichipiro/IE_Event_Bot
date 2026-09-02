"""Discord→Google適用処理の自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import quote, urlencode
from uuid import uuid4

from discord_notion_sync import _sync_discord_event_upsert
from e2e_discord_probe import (
    _find_event_by_run as _find_discord_event_by_run,
    _fingerprint,
    _request_stage as _discord_request_stage,
    _run_marker,
)
from e2e_google_probe import (
    _calendar_fingerprint,
    _event_collection_url,
    _event_item_url,
    _google_request,
)
from google_auth import get_google_access_token


DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE = "discord_google"

_CLEANUP_MAX_ATTEMPTS = 4


class _GoogleOnlySyncEnv:
    """この適用呼び出しだけGoogleを有効化し、Notionを隠すenv view。"""

    def __init__(self, env) -> None:
        self._env = env

    def __getattr__(self, key: str):
        if key == "DISCORD_TO_GOOGLE_SYNC_ENABLED":
            return "true"
        if key in ("NOTION_EVENT_INTERNAL_ID", "NOTION_EVENT_ID"):
            return ""
        return getattr(self._env, key)


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


def _discord_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _event_name(run_id: str) -> str:
    return f"[E2E] Discord to Google sync {run_id}"


def _event_description(run_id: str) -> str:
    return (
        "IE Event Bot application E2E fixture; safe to delete.\n"
        f"{_run_marker(run_id)}"
    )


def _event_payload(run_id: str) -> dict:
    start = datetime.now(timezone.utc) + timedelta(days=7)
    end = start + timedelta(minutes=30)
    return {
        "channel_id": None,
        "name": _event_name(run_id),
        "description": _event_description(run_id),
        "privacy_level": 2,
        "entity_type": 3,
        "scheduled_start_time": _discord_iso(start),
        "scheduled_end_time": _discord_iso(end),
        "entity_metadata": {"location": "E2E isolated location"},
    }


def _target_fingerprints(calendar_id: str, guild_id: str) -> dict[str, str]:
    return {
        "calendar_id_sha256": _calendar_fingerprint(calendar_id),
        "guild_id_sha256": _fingerprint(guild_id),
    }


async def _verify_guild(
    env,
    guild_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
) -> str:
    status, guild = await _discord_request_stage(
        env,
        stages,
        retries,
        "target_discord_guild",
        "GET",
        f"/guilds/{quote(guild_id, safe='')}",
    )
    if not (200 <= status < 300) or not isinstance(guild, dict):
        return f"discord_target_guild_failed_{status}"
    if str(guild.get("id") or "") != guild_id:
        return "discord_target_guild_mismatch"
    return ""


async def _verify_calendar(
    calendar_id: str,
    bearer_token: str,
    stages: dict[str, int],
) -> str:
    calendar_url = _event_collection_url(calendar_id).removesuffix("/events")
    status, calendar = await _google_request("GET", calendar_url, bearer_token)
    stages["target_google_calendar"] = status
    if not (200 <= status < 300) or not isinstance(calendar, dict):
        return f"google_target_calendar_failed_{status}"
    if str(calendar.get("id") or "") != calendar_id:
        return "google_target_calendar_mismatch"
    return ""


def _discord_event_is_owned(
    event: dict,
    *,
    event_id: str,
    guild_id: str,
    run_id: str,
) -> bool:
    return (
        str(event.get("id") or "") == event_id
        and str(event.get("guild_id") or "") == guild_id
        and str(event.get("name") or "") == _event_name(run_id)
        and _run_marker(run_id) in str(event.get("description") or "")
    )


def _google_event_is_owned(
    event: dict,
    *,
    event_id: str,
    discord_event_id: str,
    run_id: str,
) -> bool:
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return (
        str(event.get("id") or "") == event_id
        and str(event.get("status") or "") != "cancelled"
        and str(private.get("ie_origin") or "") == "discord"
        and str(private.get("ie_discord_event_id") or "") == discord_event_id
        and str(event.get("summary") or "") == _event_name(run_id)
        and _run_marker(run_id) in str(event.get("description") or "")
    )


def _parse_instant(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _google_event_matches(
    event: dict,
    *,
    event_id: str,
    discord_event: dict,
    run_id: str,
) -> bool:
    metadata = discord_event.get("entity_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    start = event.get("start")
    if not isinstance(start, dict):
        start = {}
    end = event.get("end")
    if not isinstance(end, dict):
        end = {}
    return (
        _google_event_is_owned(
            event,
            event_id=event_id,
            discord_event_id=str(discord_event.get("id") or ""),
            run_id=run_id,
        )
        and str(event.get("description") or "")
        == str(discord_event.get("description") or "")
        and str(event.get("location") or "") == str(metadata.get("location") or "")
        and _parse_instant(start.get("dateTime"))
        == _parse_instant(discord_event.get("scheduled_start_time"))
        and _parse_instant(end.get("dateTime"))
        == _parse_instant(discord_event.get("scheduled_end_time"))
    )


def _google_query_url(calendar_id: str, discord_event_id: str) -> str:
    query = urlencode(
        {
            "privateExtendedProperty": (
                f"ie_discord_event_id={discord_event_id}"
            ),
            "showDeleted": "false",
            "singleEvents": "false",
            "maxResults": "10",
        }
    )
    return f"{_event_collection_url(calendar_id)}?{query}"


async def _find_google_event_by_source(
    calendar_id: str,
    discord_event_id: str,
    run_id: str,
    bearer_token: str,
    stages: dict[str, int],
    stage_key: str,
) -> tuple[str, str]:
    status, data = await _google_request(
        "GET",
        _google_query_url(calendar_id, discord_event_id),
        bearer_token,
    )
    stages[stage_key] = status
    if not (200 <= status < 300) or not isinstance(data, dict):
        return "", f"google_event_reconcile_failed_{status}"
    items = data.get("items")
    if not isinstance(items, list):
        return "", "google_event_reconcile_invalid"
    matches = [
        event
        for event in items
        if isinstance(event, dict)
        and str(
            (((event.get("extendedProperties") or {}).get("private") or {}).get(
                "ie_discord_event_id"
            ))
            or ""
        )
        == discord_event_id
        and str(event.get("status") or "") != "cancelled"
    ]
    if len(matches) > 1:
        return "", "google_event_reconcile_ambiguous"
    if not matches:
        return "", ""
    event_id = str(matches[0].get("id") or "")
    if not event_id:
        return "", "google_event_reconcile_invalid"
    if not _google_event_is_owned(
        matches[0],
        event_id=event_id,
        discord_event_id=discord_event_id,
        run_id=run_id,
    ):
        return "", "google_event_reconcile_mismatch"
    return event_id, ""


async def _delete_owned_google_event(
    calendar_id: str,
    event_id: str,
    discord_event_id: str,
    run_id: str,
    bearer_token: str,
    stages: dict[str, int],
) -> tuple[bool, int, int, str]:
    item_url = _event_item_url(calendar_id, event_id)
    last_status = 0
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        status, event = await _google_request("GET", item_url, bearer_token)
        stages["google_cleanup_verify"] = status
        last_status = status
        if status in (404, 410):
            return True, attempt, status, ""
        if not (200 <= status < 300) or not isinstance(event, dict):
            continue
        if not _google_event_is_owned(
            event,
            event_id=event_id,
            discord_event_id=discord_event_id,
            run_id=run_id,
        ):
            return False, attempt, status, "cleanup_target_mismatch"

        status, _ = await _google_request(
            "DELETE",
            f"{item_url}?sendUpdates=none",
            bearer_token,
        )
        stages["google_delete"] = status
        last_status = status
        if 200 <= status < 300 or status in (404, 410):
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, "google_event_delete_failed"


async def _delete_owned_discord_event(
    env,
    guild_id: str,
    event_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
) -> tuple[bool, int, int, str]:
    path = (
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events/"
        f"{quote(event_id, safe='')}"
    )
    last_status = 0
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        status, event = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_cleanup_verify",
            "GET",
            path,
        )
        last_status = status
        if status == 404:
            return True, attempt, status, ""
        if not (200 <= status < 300) or not isinstance(event, dict):
            continue
        if not _discord_event_is_owned(
            event,
            event_id=event_id,
            guild_id=guild_id,
            run_id=run_id,
        ):
            return False, attempt, status, "cleanup_target_mismatch"

        status, _ = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_delete",
            "DELETE",
            path,
        )
        last_status = status
        if 200 <= status < 300 or status == 404:
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, "discord_event_delete_failed"


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    rate_limit_retries: dict[str, int],
    calendar_id: str,
    guild_id: str,
    discord_event_id: str,
    google_event_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(calendar_id, guild_id)
    if discord_event_id:
        fingerprints["discord_event_id_sha256"] = _fingerprint(discord_event_id)
    if google_event_id:
        fingerprints["google_event_id_sha256"] = sha256(
            google_event_id.encode("utf-8")
        ).hexdigest()
    return {
        "version": 1,
        "kind": "discord_google_sync",
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
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    run_id = str(manifest.get("run_id") or "")
    discord_event_id = str(manifest.get("discord_event_id") or "")
    google_event_id = str(manifest.get("google_event_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}

    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    discord_search_error = ""
    if not discord_event_id:
        discord_event_id, discord_search_error = await _find_discord_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "discord_cleanup_search",
        )
        if (
            not discord_search_error
            and not discord_event_id
            and create_attempted.get("discord_event") is True
        ):
            discord_search_error = "discord_event_ownership_unresolved"

    google_error = ""
    if not google_event_id and discord_event_id and bearer_token:
        google_event_id, google_error = await _find_google_event_by_source(
            calendar_id,
            discord_event_id,
            run_id,
            bearer_token,
            stages,
            "google_cleanup_find",
        )
    if (
        not google_error
        and not google_event_id
        and create_attempted.get("google_event") is True
    ):
        google_error = "google_event_ownership_unresolved"

    google_ok = not google_error
    google_attempts = 1
    if create_attempted.get("google_event") is True and not bearer_token:
        google_ok = False
        google_error = "missing_google_access_token"
        stages["google_delete"] = 0
    elif google_ok and google_event_id and bearer_token:
        google_ok, google_attempts, _, google_error = (
            await _delete_owned_google_event(
                calendar_id,
                google_event_id,
                discord_event_id,
                run_id,
                bearer_token,
                stages,
            )
        )

    discord_ok = not discord_search_error
    discord_attempts = 1
    discord_error = discord_search_error
    if discord_ok and discord_event_id:
        discord_ok, discord_attempts, _, discord_error = (
            await _delete_owned_discord_event(
                env,
                guild_id,
                discord_event_id,
                run_id,
                stages,
                retries,
            )
        )

    return {
        "ok": google_ok and discord_ok,
        "attempts": max(google_attempts, discord_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": google_error or discord_error,
        "discord_event_id": discord_event_id,
        "google_event_id": google_event_id,
    }


def _configuration_error(env) -> str:
    if not _env_text(env, "GOOGLE_CALENDAR_ID"):
        return "missing_google_calendar_id"
    if _env_text(env, "GOOGLE_CALENDAR_ID").lower() == "primary":
        return "primary_calendar_forbidden"
    if not _explicitly_disabled(env, "DISCORD_TO_GOOGLE_SYNC_ENABLED"):
        return "discord_to_google_sync_must_be_disabled_before_probe"
    if not _env_text(env, "DISCORD_TOKEN"):
        return "missing_discord_token"
    if not _env_text(env, "DISCORD_GUILD_ID"):
        return "missing_discord_guild_id"
    return ""


async def run_discord_google_sync_probe(
    env,
    state,
    run_id: str | None = None,
) -> dict:
    """専用Discord eventを既存適用処理でGoogleへ反映し、両方を削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}

    current_manifest = await state.get_e2e_manifest(
        DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE
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
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    target_error = await _verify_guild(env, guild_id, stages, retries)
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
    target_error = await _verify_calendar(calendar_id, bearer_token, stages)
    if target_error:
        return {
            "ok": False,
            "dirty": False,
            "error": target_error,
            "stages": stages,
        }

    run_id = str(run_id or "").strip() or _new_run_id()
    existing_event_id, precheck_error = await _find_discord_event_by_run(
        env,
        guild_id,
        run_id,
        stages,
        retries,
        "discord_precheck",
    )
    if precheck_error:
        return {
            "ok": False,
            "dirty": False,
            "error": precheck_error,
            "stages": stages,
        }
    if existing_event_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "discord_run_collision",
            "stages": stages,
        }

    manifest = {
        "version": 1,
        "kind": "discord_google_sync",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(calendar_id, guild_id),
        "create_attempted": {"discord_event": False, "google_event": False},
        "stage": "planned",
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE, manifest)

    error = ""
    discord_event_id = ""
    google_event_id = ""
    event_payload = _event_payload(run_id)
    manifest["create_attempted"]["discord_event"] = True
    manifest["stage"] = "discord_create_started"
    await state.put_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE, manifest)
    create_status, created = await _discord_request_stage(
        env,
        stages,
        retries,
        "discord_create",
        "POST",
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events",
        event_payload,
    )
    if 200 <= create_status < 300 and isinstance(created, dict):
        candidate_id = str(created.get("id") or "")
        if candidate_id and _discord_event_is_owned(
            created,
            event_id=candidate_id,
            guild_id=guild_id,
            run_id=run_id,
        ):
            discord_event_id = candidate_id
    if not discord_event_id:
        discord_event_id, reconcile_error = await _find_discord_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "discord_create_reconcile",
        )
        if reconcile_error:
            error = reconcile_error
        elif not discord_event_id:
            error = "discord_event_ownership_unresolved"
    if discord_event_id:
        manifest["discord_event_id"] = discord_event_id
        manifest["stage"] = "discord_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE, manifest)

    discord_event = created
    if not error:
        event_path = (
            f"/guilds/{quote(guild_id, safe='')}/scheduled-events/"
            f"{quote(discord_event_id, safe='')}"
        )
        read_status, discord_event = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_read",
            "GET",
            event_path,
        )
        if not (
            200 <= read_status < 300
            and isinstance(discord_event, dict)
            and _discord_event_is_owned(
                discord_event,
                event_id=discord_event_id,
                guild_id=guild_id,
                run_id=run_id,
            )
        ):
            error = "discord_event_verification_failed"

    if not error:
        existing_google_id, google_precheck_error = (
            await _find_google_event_by_source(
                calendar_id,
                discord_event_id,
                run_id,
                bearer_token,
                stages,
                "google_precheck",
            )
        )
        if google_precheck_error:
            error = google_precheck_error
        elif existing_google_id:
            error = "google_event_id_collision"

    if not error:
        manifest["create_attempted"]["google_event"] = True
        manifest["stage"] = "application_apply_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE, manifest)
        try:
            apply_ok = await _sync_discord_event_upsert(
                _GoogleOnlySyncEnv(env),
                discord_event,
                bearer_token,
            )
        except Exception:
            apply_ok = False
        stages["application_apply"] = 200 if apply_ok else 500
        if not apply_ok:
            error = "discord_google_apply_failed"

    if manifest["create_attempted"].get("google_event") is True and discord_event_id:
        google_event_id, find_error = await _find_google_event_by_source(
            calendar_id,
            discord_event_id,
            run_id,
            bearer_token,
            stages,
            "google_find",
        )
        if find_error:
            error = find_error
        elif not google_event_id:
            error = "google_event_ownership_unresolved"
        else:
            manifest["google_event_id"] = google_event_id
            manifest["stage"] = "google_event_found"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(
                DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE,
                manifest,
            )

    if not error and google_event_id:
        read_status, google_event = await _google_request(
            "GET",
            _event_item_url(calendar_id, google_event_id),
            bearer_token,
        )
        stages["google_read"] = read_status
        if not (
            200 <= read_status < 300
            and isinstance(google_event, dict)
            and _google_event_matches(
                google_event,
                event_id=google_event_id,
                discord_event=discord_event,
                run_id=run_id,
            )
        ):
            error = "google_event_verification_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest, bearer_token)
    stages.update(cleanup["stages"])
    retries.update(cleanup["rate_limit_retries"])
    cleanup_ok = cleanup["ok"] is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    discord_event_id = str(cleanup.get("discord_event_id") or discord_event_id)
    google_event_id = str(cleanup.get("google_event_id") or google_event_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                calendar_id=calendar_id,
                guild_id=guild_id,
                discord_event_id=discord_event_id,
                google_event_id=google_event_id,
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
    else:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or error or "cleanup_failed")
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["stages"] = dict(stages)
        manifest["rate_limit_retries"] = dict(retries)
        if discord_event_id:
            manifest["discord_event_id"] = discord_event_id
        if google_event_id:
            manifest["google_event_id"] = google_event_id
        await state.put_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE, manifest)

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
    if retries:
        result["rate_limit_retries"] = retries
    return result


async def cleanup_discord_google_sync_probe(
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
    manifest = await state.get_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "discord_google_sync":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    if (
        not run_id
        or not calendar_id
        or calendar_id.lower() == "primary"
        or not guild_id
        or not _env_text(env, "DISCORD_TOKEN")
        or manifest.get("target_fingerprints")
        != _target_fingerprints(calendar_id, guild_id)
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
    discord_event_id = str(cleanup.get("discord_event_id") or "")
    google_event_id = str(cleanup.get("google_event_id") or "")

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if discord_event_id:
            manifest["discord_event_id"] = discord_event_id
        if google_event_id:
            manifest["google_event_id"] = google_event_id
        await state.put_e2e_manifest(DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        DISCORD_GOOGLE_SYNC_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            calendar_id=calendar_id,
            guild_id=guild_id,
            discord_event_id=discord_event_id,
            google_event_id=google_event_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
