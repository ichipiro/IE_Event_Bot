"""前日リマインドの自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from e2e_discord_probe import (
    _delete_event,
    _delete_message,
    _discord_iso,
    _find_event_by_run,
    _find_message_by_run,
    _fingerprint,
    _int_value,
    _request_stage as _discord_request_stage,
    _run_marker,
)
from jobs import (
    _discord_send_message,
    _format_japanese_datetime,
    _parse_rfc3339,
    _reminder_window_minutes,
    _run_reminder_events,
)


REMINDER_MANIFEST_SERVICE = "reminder"


class _EphemeralReminderState:
    """reminder cacheを1回のprobe内だけに閉じ込める状態アダプター。"""

    def __init__(self) -> None:
        self.cache: dict = {}
        self.write_count = 0

    @staticmethod
    def enabled() -> bool:
        return True

    async def get_json(self, key: str, default=None):
        if key != "reminder_cache" or not self.cache:
            return default
        return dict(self.cache)

    async def put_json_if_changed(self, key: str, payload) -> bool:
        if key != "reminder_cache" or not isinstance(payload, dict):
            return False
        next_cache = dict(payload)
        if self.cache == next_cache:
            return False
        self.cache = next_cache
        self.write_count += 1
        return True


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


def _target_fingerprints(guild_id: str, channel_id: str, role_id: str) -> dict[str, str]:
    return {
        "guild_id_sha256": _fingerprint(guild_id),
        "channel_id_sha256": _fingerprint(channel_id),
        "role_id_sha256": _fingerprint(role_id),
    }


def _event_name(run_id: str) -> str:
    return f"[E2E] Reminder {_run_marker(run_id)}"


def _event_payload(run_id: str, now_utc: datetime, window_minutes: int) -> dict:
    offset_seconds = max(1, min(300, (window_minutes * 60) // 2))
    start = now_utc + timedelta(hours=24, seconds=offset_seconds)
    end = start + timedelta(minutes=30)
    return {
        "channel_id": None,
        "name": _event_name(run_id),
        "description": (
            "IE Event Bot reminder E2E fixture; safe to delete.\n"
            f"{_run_marker(run_id)}"
        ),
        "privacy_level": 2,
        "entity_type": 3,
        "scheduled_start_time": _discord_iso(start),
        "scheduled_end_time": _discord_iso(end),
        "entity_metadata": {"location": "E2E reminder fixture"},
    }


def _event_has_run(
    event: dict,
    *,
    event_id: str,
    guild_id: str,
    run_id: str,
) -> bool:
    return (
        str(event.get("id") or "") == event_id
        and str(event.get("guild_id") or "") == guild_id
        and _run_marker(run_id) in str(event.get("description") or "")
    )


def _event_matches(
    event: dict,
    *,
    event_id: str,
    guild_id: str,
    run_id: str,
    expected_start: datetime,
) -> bool:
    metadata = event.get("entity_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    actual_start = _parse_rfc3339(str(event.get("scheduled_start_time") or ""))
    if actual_start is not None:
        if actual_start.tzinfo is None:
            actual_start = actual_start.replace(tzinfo=timezone.utc)
        else:
            actual_start = actual_start.astimezone(timezone.utc)
    return (
        _event_has_run(
            event,
            event_id=event_id,
            guild_id=guild_id,
            run_id=run_id,
        )
        and str(event.get("name") or "") == _event_name(run_id)
        and _int_value(event.get("privacy_level")) == 2
        and _int_value(event.get("entity_type")) == 3
        and str(metadata.get("location") or "") == "E2E reminder fixture"
        and actual_start == expected_start
    )


def _message_content(
    env,
    *,
    run_id: str,
    event_id: str,
    role_id: str,
    start: datetime,
) -> str:
    start_text = _format_japanese_datetime(start) or _discord_iso(start)
    return (
        f"<@&{role_id}>\n"
        "🔔 明日開催のイベントがあります\n"
        f"イベント名: {_event_name(run_id)}\n"
        f"開始日時: {start_text}\n"
        "場所: E2E reminder fixture\n"
        f"https://discord.com/events/{_env_text(env, 'DISCORD_GUILD_ID')}/{event_id}"
    )


def _message_matches(
    message: dict,
    *,
    env,
    message_id: str,
    channel_id: str,
    role_id: str,
    event_id: str,
    run_id: str,
    start: datetime,
) -> bool:
    mention_roles = message.get("mention_roles")
    if not isinstance(mention_roles, list):
        mention_roles = []
    return (
        str(message.get("id") or "") == message_id
        and str(message.get("channel_id") or "") == channel_id
        and str(message.get("content") or "")
        == _message_content(
            env,
            run_id=run_id,
            event_id=event_id,
            role_id=role_id,
            start=start,
        )
        and [str(value) for value in mention_roles] == [role_id]
        and message.get("mention_everyone") is False
    )


async def _verify_targets(env, stages: dict[str, int], retries: dict[str, int]) -> str:
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "REMINDER_CHANNEL_ID")
    role_id = _env_text(env, "REMINDER_ROLE_ID")

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

    status, channel = await _discord_request_stage(
        env,
        stages,
        retries,
        "target_discord_reminder_channel",
        "GET",
        f"/channels/{quote(channel_id, safe='')}",
    )
    if not (200 <= status < 300) or not isinstance(channel, dict):
        return f"discord_target_channel_failed_{status}"
    if (
        str(channel.get("id") or "") != channel_id
        or str(channel.get("guild_id") or "") != guild_id
        or _int_value(channel.get("type"), -1) != 0
    ):
        return "discord_target_channel_mismatch"

    status, roles = await _discord_request_stage(
        env,
        stages,
        retries,
        "target_discord_reminder_role",
        "GET",
        f"/guilds/{quote(guild_id, safe='')}/roles",
    )
    if not (200 <= status < 300) or not isinstance(roles, list):
        return f"discord_target_role_failed_{status}"
    role = next(
        (
            value
            for value in roles
            if isinstance(value, dict) and str(value.get("id") or "") == role_id
        ),
        None,
    )
    if not isinstance(role, dict) or role.get("mentionable") is not True:
        return "discord_target_role_mismatch"
    return ""


def _configuration_error(env) -> str:
    if not _env_text(env, "DISCORD_TOKEN"):
        return "missing_discord_token"
    if not _env_text(env, "DISCORD_GUILD_ID"):
        return "missing_discord_guild_id"
    if not _env_text(env, "REMINDER_CHANNEL_ID"):
        return "missing_reminder_channel_id"
    if not _env_text(env, "REMINDER_ROLE_ID"):
        return "missing_reminder_role_id"
    if not _explicitly_disabled(env, "CRON_ENABLE_REMINDER"):
        return "cron_reminder_must_be_disabled"
    return ""


async def _cleanup_resources(env, manifest: dict) -> dict:
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "REMINDER_CHANNEL_ID")
    run_id = str(manifest.get("run_id") or "")
    event_id = str(manifest.get("event_id") or "")
    message_id = str(manifest.get("message_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}

    message_resolve_error = ""
    if not message_id:
        message_id, message_resolve_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "discord_message_cleanup_search",
        )
        if (
            not message_resolve_error
            and not message_id
            and create_attempted.get("message") is True
        ):
            message_resolve_error = "discord_message_ownership_unresolved"

    event_resolve_error = ""
    if not event_id:
        event_id, event_resolve_error = await _find_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "discord_event_cleanup_search",
        )
        if (
            not event_resolve_error
            and not event_id
            and create_attempted.get("event") is True
        ):
            event_resolve_error = "discord_event_ownership_unresolved"

    message_ok = not message_resolve_error
    message_attempts = 1
    message_error = message_resolve_error
    if message_ok and message_id:
        message_ok, message_attempts, _, message_error = await _delete_message(
            env,
            channel_id,
            message_id,
            run_id,
            stages,
            retries,
        )

    event_ok = not event_resolve_error
    event_attempts = 1
    event_error = event_resolve_error
    if event_ok and event_id:
        event_ok, event_attempts, _, event_error = await _delete_event(
            env,
            guild_id,
            event_id,
            run_id,
            stages,
            retries,
        )

    return {
        "ok": message_ok and event_ok,
        "attempts": max(message_attempts, event_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": message_error or event_error,
        "event_id": event_id,
        "message_id": message_id,
    }


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    rate_limit_retries: dict[str, int],
    guild_id: str,
    channel_id: str,
    role_id: str,
    event_id: str,
    message_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(guild_id, channel_id, role_id)
    if event_id:
        fingerprints["discord_event_id_sha256"] = _fingerprint(event_id)
    if message_id:
        fingerprints["discord_message_id_sha256"] = _fingerprint(message_id)
    return {
        "version": 1,
        "kind": "day_before_reminder",
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


async def run_reminder_probe(env, state, run_id: str | None = None) -> dict:
    """所有イベントだけへ前日通知を適用し、イベントとメッセージを削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}

    current_manifest = await state.get_e2e_manifest(REMINDER_MANIFEST_SERVICE)
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
    channel_id = _env_text(env, "REMINDER_CHANNEL_ID")
    role_id = _env_text(env, "REMINDER_ROLE_ID")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    target_error = await _verify_targets(env, stages, retries)
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
    existing_event_id, precheck_error = await _find_event_by_run(
        env,
        guild_id,
        run_id,
        stages,
        retries,
        "discord_event_precheck",
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
            "error": "discord_event_run_collision",
            "stages": stages,
        }
    existing_message_id, precheck_error = await _find_message_by_run(
        env,
        channel_id,
        run_id,
        stages,
        retries,
        "discord_message_precheck",
    )
    if precheck_error:
        return {
            "ok": False,
            "dirty": False,
            "error": precheck_error,
            "stages": stages,
        }
    if existing_message_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "discord_message_run_collision",
            "stages": stages,
        }

    probe_now = datetime.now(timezone.utc).replace(microsecond=0)
    window_minutes = _reminder_window_minutes(env)
    event_payload = _event_payload(run_id, probe_now, window_minutes)
    event_start = _parse_rfc3339(event_payload["scheduled_start_time"])
    if event_start is None:
        return {"ok": False, "dirty": False, "error": "reminder_time_invalid"}
    event_start = event_start.astimezone(timezone.utc)
    manifest = {
        "version": 1,
        "kind": "day_before_reminder",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(guild_id, channel_id, role_id),
        "create_attempted": {"event": False, "message": False},
        "stage": "planned",
        "stages": dict(stages),
        "created_at": probe_now.isoformat(),
    }
    await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)

    error = ""
    event_id = ""
    message_id = ""
    manifest["create_attempted"]["event"] = True
    manifest["stage"] = "discord_event_create_started"
    await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)
    create_status, created_event = await _discord_request_stage(
        env,
        stages,
        retries,
        "discord_event_create",
        "POST",
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events",
        event_payload,
    )
    if 200 <= create_status < 300 and isinstance(created_event, dict):
        candidate_id = str(created_event.get("id") or "")
        if candidate_id and _event_matches(
            created_event,
            event_id=candidate_id,
            guild_id=guild_id,
            run_id=run_id,
            expected_start=event_start,
        ):
            event_id = candidate_id
    if not event_id:
        event_id, reconcile_error = await _find_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "discord_event_create_reconcile",
        )
        if reconcile_error:
            error = reconcile_error
        elif not event_id:
            error = "discord_event_ownership_unresolved"
    if event_id:
        manifest["event_id"] = event_id
        manifest["stage"] = "discord_event_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)

    event = created_event
    if not error:
        event_status, event = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_event_read",
            "GET",
            (
                f"/guilds/{quote(guild_id, safe='')}/scheduled-events/"
                f"{quote(event_id, safe='')}"
            ),
        )
        if not (
            200 <= event_status < 300
            and isinstance(event, dict)
            and _event_matches(
                event,
                event_id=event_id,
                guild_id=guild_id,
                run_id=run_id,
                expected_start=event_start,
            )
        ):
            error = "discord_event_verification_failed"

    reminder_state = _EphemeralReminderState()

    async def tracked_send_message(
        send_env,
        send_channel_id,
        content,
        allowed_mentions=None,
    ):
        manifest["create_attempted"]["message"] = True
        manifest["stage"] = "discord_message_create_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)
        return await _discord_send_message(
            send_env,
            send_channel_id,
            content,
            allowed_mentions=allowed_mentions,
        )

    if not error:
        try:
            detail = await _run_reminder_events(
                env,
                reminder_state,
                [event],
                now_utc=probe_now,
                return_detail=True,
                send_message=tracked_send_message,
            )
        except Exception:
            detail = {}
        notify_ok = (
            isinstance(detail, dict)
            and detail.get("ok") is True
            and detail.get("failed_count") == 0
            and reminder_state.cache.get(event_id) == probe_now.isoformat()
            and reminder_state.write_count == 1
        )
        stages["job_notify"] = 200 if notify_ok else 500
        if not notify_ok:
            error = "reminder_job_failed"

    if manifest["create_attempted"].get("message") is True:
        message_id, find_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "discord_message_find",
        )
        if find_error:
            error = find_error
        elif not message_id:
            error = "discord_message_ownership_unresolved"
        else:
            manifest["message_id"] = message_id
            manifest["stage"] = "discord_message_found"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)
    elif not error:
        error = "discord_message_not_created"

    if not error and message_id:
        message_status, message = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_message_read",
            "GET",
            (
                f"/channels/{quote(channel_id, safe='')}/messages/"
                f"{quote(message_id, safe='')}"
            ),
        )
        if not (
            200 <= message_status < 300
            and isinstance(message, dict)
            and _message_matches(
                message,
                env=env,
                message_id=message_id,
                channel_id=channel_id,
                role_id=role_id,
                event_id=event_id,
                run_id=run_id,
                start=event_start,
            )
        ):
            error = "discord_message_verification_failed"

    if not error:
        try:
            duplicate_detail = await _run_reminder_events(
                env,
                reminder_state,
                [event],
                now_utc=probe_now,
                return_detail=True,
                send_message=tracked_send_message,
            )
        except Exception:
            duplicate_detail = {}
        duplicate_message_id, find_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "discord_duplicate_verify",
        )
        duplicate_ok = (
            isinstance(duplicate_detail, dict)
            and duplicate_detail.get("ok") is True
            and duplicate_detail.get("failed_count") == 0
            and not find_error
            and duplicate_message_id == message_id
            and reminder_state.cache.get(event_id) == probe_now.isoformat()
            and reminder_state.write_count == 1
        )
        stages["job_duplicate_suppression"] = 200 if duplicate_ok else 500
        if not duplicate_ok:
            error = find_error or "reminder_duplicate_suppression_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest)
    stages.update(cleanup["stages"])
    for key, value in cleanup["rate_limit_retries"].items():
        retries[key] = retries.get(key, 0) + value
    cleanup_ok = cleanup.get("ok") is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    event_id = str(cleanup.get("event_id") or event_id)
    message_id = str(cleanup.get("message_id") or message_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            REMINDER_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                guild_id=guild_id,
                channel_id=channel_id,
                role_id=role_id,
                event_id=event_id,
                message_id=message_id,
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
    else:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or error or "cleanup_failed")
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["stages"] = dict(stages)
        manifest["rate_limit_retries"] = dict(retries)
        if event_id:
            manifest["event_id"] = event_id
        if message_id:
            manifest["message_id"] = message_id
        await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)

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


async def cleanup_reminder_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """run IDと対象fingerprint確認後にdirtyなリマインド資源を回収する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}

    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(REMINDER_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "day_before_reminder":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "REMINDER_CHANNEL_ID")
    role_id = _env_text(env, "REMINDER_ROLE_ID")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    if (
        not run_id
        or not _env_text(env, "DISCORD_TOKEN")
        or not guild_id
        or not channel_id
        or not role_id
        or manifest.get("target_fingerprints")
        != _target_fingerprints(guild_id, channel_id, role_id)
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
    for key, value in cleanup["rate_limit_retries"].items():
        retries[key] = retries.get(key, 0) + value
    attempts = int(cleanup.get("attempts") or 1)
    event_id = str(cleanup.get("event_id") or "")
    message_id = str(cleanup.get("message_id") or "")

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if event_id:
            manifest["event_id"] = event_id
        if message_id:
            manifest["message_id"] = message_id
        await state.put_e2e_manifest(REMINDER_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        REMINDER_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            guild_id=guild_id,
            channel_id=channel_id,
            role_id=role_id,
            event_id=event_id,
            message_id=message_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
