"""Google Calendar→Discord適用処理の自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import quote
from uuid import uuid4

from e2e_discord_probe import (
    _find_event_by_run as _find_discord_event_by_run,
    _fingerprint,
    _request_stage as _discord_request_stage,
    _run_marker,
)
from e2e_google_probe import (
    _calendar_fingerprint,
    _delete_event as _delete_google_event,
    _event_collection_url,
    _event_item_url,
    _event_matches as _google_event_matches,
    _google_request,
)
from google_apply_sync import _build_discord_payload, _sync_to_discord
from google_auth import get_google_access_token


GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE = "google_discord"

_JST = timezone(timedelta(hours=9))
_CLEANUP_MAX_ATTEMPTS = 4


class _DiscordSyncEnv:
    """この適用呼び出しだけDiscord同期を有効化する読み取り専用env view。"""

    def __init__(self, env) -> None:
        self._env = env

    def __getattr__(self, key: str):
        if key == "DISCORD_SYNC_ENABLED":
            return "true"
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


def _event_name(run_id: str) -> str:
    return f"[E2E] Google to Discord sync {run_id}"


def _event_payload(run_id: str, event_id: str) -> dict:
    start = (datetime.now(_JST) + timedelta(days=7)).replace(second=0, microsecond=0)
    end = start + timedelta(minutes=30)
    return {
        "id": event_id,
        "summary": _event_name(run_id),
        "description": (
            "IE Event Bot application E2E fixture; safe to delete.\n"
            f"{_run_marker(run_id)}"
        ),
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
    google_event_id: str,
    discord_event_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(calendar_id, guild_id)
    fingerprints["google_event_id_sha256"] = sha256(
        google_event_id.encode("utf-8")
    ).hexdigest()
    if discord_event_id:
        fingerprints["discord_event_id_sha256"] = _fingerprint(discord_event_id)
    return {
        "version": 1,
        "kind": "google_discord_sync",
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
    google_event_id = str(manifest.get("google_event_id") or "")
    discord_event_id = str(manifest.get("discord_event_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}

    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    discord_error = ""
    if not discord_event_id:
        discord_event_id, discord_error = await _find_discord_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "discord_cleanup_search",
        )
        if (
            not discord_error
            and not discord_event_id
            and create_attempted.get("discord_event") is True
        ):
            discord_error = "discord_event_ownership_unresolved"

    discord_ok = not discord_error
    discord_attempts = 1
    if discord_ok and discord_event_id:
        (
            discord_ok,
            discord_attempts,
            _,
            discord_error,
        ) = await _delete_owned_discord_event(
            env,
            guild_id,
            discord_event_id,
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
            (
                google_ok,
                google_attempts,
                delete_status,
                google_error,
            ) = await _delete_google_event(
                calendar_id,
                google_event_id,
                run_id,
                bearer_token,
            )
            stages["google_delete"] = delete_status

    return {
        "ok": discord_ok and google_ok,
        "attempts": max(discord_attempts, google_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": discord_error or google_error,
        "discord_event_id": discord_event_id,
    }


def _configuration_error(env) -> str:
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    if not calendar_id:
        return "missing_google_calendar_id"
    if calendar_id.lower() == "primary":
        return "primary_calendar_forbidden"
    if not _explicitly_disabled(env, "DISCORD_SYNC_ENABLED"):
        return "discord_sync_must_be_disabled"
    if not _env_text(env, "DISCORD_TOKEN"):
        return "missing_discord_token"
    if not _env_text(env, "DISCORD_GUILD_ID"):
        return "missing_discord_guild_id"
    return ""


async def run_google_discord_sync_probe(env, state, run_id: str | None = None) -> dict:
    """専用Calendar eventを既存適用処理でDiscordへ反映し、両方を削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}

    current_manifest = await state.get_e2e_manifest(
        GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE
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

    google_event_id = f"iee2e{uuid4().hex}"
    event = _event_payload(run_id, google_event_id)
    manifest = {
        "version": 1,
        "kind": "google_discord_sync",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(calendar_id, guild_id),
        "google_event_id": google_event_id,
        "create_attempted": {"google_event": False, "discord_event": False},
        "stage": "planned",
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)

    error = ""
    discord_event_id = ""
    manifest["create_attempted"]["google_event"] = True
    manifest["stage"] = "google_create_started"
    await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)
    create_status, created = await _google_request(
        "POST",
        f"{_event_collection_url(calendar_id)}?sendUpdates=none",
        bearer_token,
        event,
    )
    stages["google_create"] = create_status
    if not (200 <= create_status < 300) or not _google_event_matches(
        created,
        event_id=google_event_id,
        run_id=run_id,
        summary=str(event["summary"]),
    ):
        error = "google_create_verification_failed"
    else:
        manifest["stage"] = "google_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)

    google_event = created
    if not error:
        read_status, google_event = await _google_request(
            "GET",
            _event_item_url(calendar_id, google_event_id),
            bearer_token,
        )
        stages["google_read"] = read_status
        if not (200 <= read_status < 300) or not _google_event_matches(
            google_event,
            event_id=google_event_id,
            run_id=run_id,
            summary=str(event["summary"]),
        ):
            error = "google_read_verification_failed"

    sync_env = _DiscordSyncEnv(env)
    expected_discord = _build_discord_payload(sync_env, google_event)
    if not error and not isinstance(expected_discord, dict):
        error = "discord_payload_invalid"

    if not error:
        manifest["create_attempted"]["discord_event"] = True
        manifest["stage"] = "application_apply_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)
        try:
            resolved_id = await _sync_to_discord(
                sync_env,
                google_event,
                None,
                None,
                {},
            )
        except Exception:
            resolved_id = None
        discord_event_id = str(resolved_id or "")
        stages["application_apply"] = 200 if discord_event_id else 0
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
        manifest["stage"] = "discord_event_found"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)

    if not error and isinstance(expected_discord, dict):
        path = (
            f"/guilds/{quote(guild_id, safe='')}/scheduled-events/"
            f"{quote(discord_event_id, safe='')}"
        )
        read_status, discord_event = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_read",
            "GET",
            path,
        )
        metadata = expected_discord.get("entity_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if not (
            200 <= read_status < 300
            and isinstance(discord_event, dict)
            and _discord_event_is_owned(
                discord_event,
                event_id=discord_event_id,
                guild_id=guild_id,
                run_id=run_id,
            )
            and str(discord_event.get("description") or "")
            == str(expected_discord.get("description") or "")
            and str((discord_event.get("entity_metadata") or {}).get("location") or "")
            == str(metadata.get("location") or "")
        ):
            error = "discord_event_verification_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest, bearer_token)
    stages.update(cleanup["stages"])
    retries.update(cleanup["rate_limit_retries"])
    cleanup_ok = cleanup["ok"] is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    discord_event_id = str(cleanup.get("discord_event_id") or discord_event_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                calendar_id=calendar_id,
                guild_id=guild_id,
                google_event_id=google_event_id,
                discord_event_id=discord_event_id,
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
        await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)

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


async def cleanup_google_discord_sync_probe(
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
    manifest = await state.get_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "google_discord_sync":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    google_event_id = str(manifest.get("google_event_id") or "")
    if (
        not run_id
        or not google_event_id
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

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if discord_event_id:
            manifest["discord_event_id"] = discord_event_id
        await state.put_e2e_manifest(GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        GOOGLE_DISCORD_SYNC_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            calendar_id=calendar_id,
            guild_id=guild_id,
            google_event_id=google_event_id,
            discord_event_id=discord_event_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
