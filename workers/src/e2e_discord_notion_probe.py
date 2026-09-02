"""Discord→Notion適用処理の自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from discord_notion_sync import _sync_discord_event_upsert
from e2e_discord_probe import (
    _find_event_by_run as _find_discord_event_by_run,
    _fingerprint,
    _request_stage as _discord_request_stage,
    _run_marker,
)
from e2e_notion_probe import (
    _EVENT_SCHEMA,
    _canonical_id,
    _find_page_by_marker,
    _page_archived,
    _page_database_id,
    _property_text,
    _request_stage as _notion_request_stage,
    _verify_database,
)


DISCORD_NOTION_SYNC_MANIFEST_SERVICE = "discord_notion"

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
    return f"[E2E] Discord to Notion sync {run_id}"


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


def _target_fingerprints(guild_id: str, database_id: str) -> dict[str, str]:
    return {
        "guild_id_sha256": _fingerprint(guild_id),
        "notion_database_id_sha256": _fingerprint(_canonical_id(database_id)),
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


def _notion_page_is_owned(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    discord_event_id: str,
    run_id: str,
) -> bool:
    return (
        _canonical_id(page.get("id")) == _canonical_id(page_id)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and _property_text(page, "メッセージID", "rich_text")
        == discord_event_id
        and _property_text(page, "イベント名", "title") == _event_name(run_id)
        and _run_marker(run_id)
        in _property_text(page, "内容", "rich_text")
    )


def _notion_page_matches(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    discord_event: dict,
    run_id: str,
) -> bool:
    metadata = discord_event.get("entity_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return (
        _notion_page_is_owned(
            page,
            page_id=page_id,
            database_id=database_id,
            discord_event_id=str(discord_event.get("id") or ""),
            run_id=run_id,
        )
        and not _page_archived(page)
        and _property_text(page, "内容", "rich_text")
        == str(discord_event.get("description") or "")
        and _property_text(page, "場所", "rich_text")
        == str(metadata.get("location") or "")
        and _canonical_id(_property_text(page, "ページID", "rich_text"))
        == _canonical_id(page_id)
        and _property_text(page, "作成者ID", "rich_text")
        == str(discord_event.get("creator_id") or "不明")
        and not _property_text(page, "GoogleイベントID", "rich_text")
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


async def _archive_owned_notion_page(
    env,
    database_id: str,
    page_id: str,
    discord_event_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
) -> tuple[bool, int, int, str]:
    path = f"/pages/{quote(page_id, safe='')}"
    last_status = 0
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        status, page = await _notion_request_stage(
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
            discord_event_id=discord_event_id,
            run_id=run_id,
        ):
            return False, attempt, status, "cleanup_target_mismatch"
        if _page_archived(page):
            return True, attempt, status, ""

        status, archived = await _notion_request_stage(
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
                discord_event_id=discord_event_id,
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
    guild_id: str,
    database_id: str,
    discord_event_id: str,
    notion_page_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(guild_id, database_id)
    if discord_event_id:
        fingerprints["discord_event_id_sha256"] = _fingerprint(discord_event_id)
    if notion_page_id:
        fingerprints["notion_page_id_sha256"] = _fingerprint(notion_page_id)
    return {
        "version": 1,
        "kind": "discord_notion_sync",
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


async def _cleanup_resources(env, manifest: dict) -> dict:
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    run_id = str(manifest.get("run_id") or "")
    discord_event_id = str(manifest.get("discord_event_id") or "")
    notion_page_id = str(manifest.get("notion_page_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}

    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    notion_error = ""
    if not notion_page_id and discord_event_id:
        notion_page_id, notion_error = await _find_page_by_marker(
            env,
            database_id,
            "メッセージID",
            discord_event_id,
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
        notion_ok, notion_attempts, _, notion_error = (
            await _archive_owned_notion_page(
                env,
                database_id,
                notion_page_id,
                discord_event_id,
                run_id,
                stages,
                retries,
            )
        )

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
        "ok": notion_ok and discord_ok,
        "attempts": max(notion_attempts, discord_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": notion_error or discord_error,
        "discord_event_id": discord_event_id,
        "notion_page_id": notion_page_id,
    }


def _configuration_error(env) -> str:
    if not _env_text(env, "DISCORD_TOKEN"):
        return "missing_discord_token"
    if not _env_text(env, "DISCORD_GUILD_ID"):
        return "missing_discord_guild_id"
    if not _explicitly_disabled(env, "DISCORD_TO_GOOGLE_SYNC_ENABLED"):
        return "discord_to_google_sync_must_be_disabled"
    if _env_text(env, "NOTION_EVENT_ID"):
        return "external_notion_database_must_be_disabled"
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


async def run_discord_notion_sync_probe(
    env,
    state,
    run_id: str | None = None,
) -> dict:
    """専用Discord eventを既存適用処理でNotionへ反映し、両方を削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}

    current_manifest = await state.get_e2e_manifest(
        DISCORD_NOTION_SYNC_MANIFEST_SERVICE
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

    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    target_error = await _verify_guild(env, guild_id, stages, retries)
    if not target_error:
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
        result = {
            "ok": False,
            "dirty": False,
            "error": target_error,
            "stages": stages,
        }
        if retries:
            result["rate_limit_retries"] = retries
        return result

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
        "kind": "discord_notion_sync",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(guild_id, database_id),
        "create_attempted": {"discord_event": False, "notion_page": False},
        "stage": "planned",
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE, manifest)

    error = ""
    discord_event_id = ""
    notion_page_id = ""
    event_payload = _event_payload(run_id)
    manifest["create_attempted"]["discord_event"] = True
    manifest["stage"] = "discord_create_started"
    await state.put_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE, manifest)
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
        await state.put_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE, manifest)

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
        preexisting_page_id, notion_precheck_error = await _find_page_by_marker(
            env,
            database_id,
            "メッセージID",
            discord_event_id,
            stages,
            retries,
            "notion_precheck",
            "notion_page",
        )
        if notion_precheck_error:
            error = notion_precheck_error
        elif preexisting_page_id:
            error = "notion_event_id_collision"

    if not error:
        manifest["create_attempted"]["notion_page"] = True
        manifest["stage"] = "application_apply_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE, manifest)
        try:
            apply_ok = await _sync_discord_event_upsert(
                env,
                discord_event,
                None,
            )
        except Exception:
            apply_ok = False
        stages["application_apply"] = 200 if apply_ok else 500
        if not apply_ok:
            error = "discord_notion_apply_failed"

    if manifest["create_attempted"].get("notion_page") is True and discord_event_id:
        notion_page_id, find_error = await _find_page_by_marker(
            env,
            database_id,
            "メッセージID",
            discord_event_id,
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
            await state.put_e2e_manifest(
                DISCORD_NOTION_SYNC_MANIFEST_SERVICE,
                manifest,
            )

    if not error and notion_page_id:
        read_status, notion_page = await _notion_request_stage(
            env,
            stages,
            retries,
            "notion_read",
            "GET",
            f"/pages/{quote(notion_page_id, safe='')}",
        )
        if not (
            200 <= read_status < 300
            and isinstance(notion_page, dict)
            and _notion_page_matches(
                notion_page,
                page_id=notion_page_id,
                database_id=database_id,
                discord_event=discord_event,
                run_id=run_id,
            )
        ):
            error = "notion_page_verification_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest)
    stages.update(cleanup["stages"])
    retries.update(cleanup["rate_limit_retries"])
    cleanup_ok = cleanup["ok"] is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    discord_event_id = str(cleanup.get("discord_event_id") or discord_event_id)
    notion_page_id = str(cleanup.get("notion_page_id") or notion_page_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            DISCORD_NOTION_SYNC_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                guild_id=guild_id,
                database_id=database_id,
                discord_event_id=discord_event_id,
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
        if discord_event_id:
            manifest["discord_event_id"] = discord_event_id
        if notion_page_id:
            manifest["notion_page_id"] = notion_page_id
        await state.put_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE, manifest)

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


async def cleanup_discord_notion_sync_probe(
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
    manifest = await state.get_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "discord_notion_sync":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    if (
        not run_id
        or not guild_id
        or not _env_text(env, "DISCORD_TOKEN")
        or not _canonical_id(database_id)
        or not _env_text(env, "NOTION_TOKEN")
        or manifest.get("target_fingerprints")
        != _target_fingerprints(guild_id, database_id)
    ):
        return {
            "ok": False,
            "dirty": True,
            "error": "dirty_manifest_target_mismatch",
        }

    cleanup = await _cleanup_resources(env, manifest)
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
    notion_page_id = str(cleanup.get("notion_page_id") or "")

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if discord_event_id:
            manifest["discord_event_id"] = discord_event_id
        if notion_page_id:
            manifest["notion_page_id"] = notion_page_id
        await state.put_e2e_manifest(DISCORD_NOTION_SYNC_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        DISCORD_NOTION_SYNC_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            guild_id=guild_id,
            database_id=database_id,
            discord_event_id=discord_event_id,
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
