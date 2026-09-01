import asyncio
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from workers import fetch as _runtime_fetch


DISCORD_CRUD_MANIFEST_SERVICE = "discord"

_DISCORD_API_BASE = "https://discord.com/api/v10"
_CLEANUP_MAX_ATTEMPTS = 4
_RATE_LIMIT_MAX_ATTEMPTS = 4
_RATE_LIMIT_MAX_DELAY_SECONDS = 10.0
_USER_AGENT = "DiscordBot (https://github.com/lycanthr0pes/IE_Event_Bot_fork, 1.0)"


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


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"E2E-{timestamp}-{uuid4().hex[:8]}"


def _run_marker(run_id: str) -> str:
    return f"[ie-event-bot-e2e:{run_id}]"


def _discord_iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def _event_payload(run_id: str) -> dict:
    start = datetime.now(timezone.utc) + timedelta(days=7)
    end = start + timedelta(minutes=30)
    return {
        "channel_id": None,
        "name": f"[E2E] Discord CRUD {run_id}",
        "description": (
            "IE Event Bot E2E automated fixture; safe to delete.\n"
            f"{_run_marker(run_id)}"
        ),
        "privacy_level": 2,
        "entity_type": 3,
        "scheduled_start_time": _discord_iso(start),
        "scheduled_end_time": _discord_iso(end),
        "entity_metadata": {"location": "E2E initial location"},
    }


def _event_update_payload(run_id: str) -> dict:
    return {
        "name": f"[E2E] Discord CRUD updated {run_id}",
        "description": (
            "IE Event Bot E2E automated fixture updated; safe to delete.\n"
            f"{_run_marker(run_id)}"
        ),
        "entity_metadata": {"location": "E2E updated location"},
    }


def _allowed_mentions(role_id: str) -> dict:
    return {
        "parse": [],
        "roles": [role_id],
        "replied_user": False,
    }


def _message_payload(run_id: str, role_id: str, *, updated: bool) -> dict:
    state = "updated" if updated else "created"
    return {
        "content": (f"<@&{role_id}> [E2E] Discord CRUD {state}\n{_run_marker(run_id)}"),
        "allowed_mentions": _allowed_mentions(role_id),
    }


def _header_text(response, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        value = headers.get(name)
    except Exception:
        value = None
    return str(value or "").strip()


def _retry_after_seconds(response, data: Any) -> float | None:
    raw = _header_text(response, "Retry-After")
    if not raw and isinstance(data, dict):
        raw = str(data.get("retry_after") or "").strip()
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


async def _discord_request(
    env,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, Any, int]:
    """小さなDiscord JSON APIを呼び、429だけ公式待機値で再試行する。"""
    token = _env_text(env, "DISCORD_TOKEN")
    if not token:
        return 0, {}, 0

    headers = {
        "Authorization": f"Bot {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    options: dict[str, Any] = {
        "method": method.upper(),
        "headers": headers,
    }
    if payload is not None:
        options["body"] = json.dumps(payload, ensure_ascii=False)

    for request_number in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        try:
            response = await fetch(f"{_DISCORD_API_BASE}{path}", options)
            status = int(response.status)
            text = await response.text()
        except Exception:
            return 0, {}, request_number - 1

        data: Any = {}
        if text:
            try:
                data = json.loads(text)
            except Exception:
                data = {}

        retries = request_number - 1
        if status != 429 or request_number >= _RATE_LIMIT_MAX_ATTEMPTS:
            return status, data, retries

        retry_after = _retry_after_seconds(response, data)
        if retry_after is None or retry_after > _RATE_LIMIT_MAX_DELAY_SECONDS:
            return status, data, retries
        await asyncio.sleep(retry_after)

    return 429, {}, _RATE_LIMIT_MAX_ATTEMPTS - 1


async def _request_stage(
    env,
    stages: dict[str, int],
    retries: dict[str, int],
    key: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, Any]:
    status, data, retry_count = await _discord_request(
        env,
        method,
        path,
        payload,
    )
    stages[key] = status
    if retry_count:
        retries[key] = retries.get(key, 0) + retry_count
    return status, data


def _event_matches(
    event: dict,
    *,
    event_id: str,
    guild_id: str,
    run_id: str,
    name: str,
    location: str,
) -> bool:
    metadata = event.get("entity_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return (
        str(event.get("id") or "") == event_id
        and str(event.get("guild_id") or "") == guild_id
        and str(event.get("name") or "") == name
        and _run_marker(run_id) in str(event.get("description") or "")
        and _int_value(event.get("privacy_level")) == 2
        and _int_value(event.get("entity_type")) == 3
        and str(metadata.get("location") or "") == location
    )


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


def _message_matches(
    message: dict,
    *,
    message_id: str,
    channel_id: str,
    role_id: str,
    run_id: str,
    content: str,
) -> bool:
    mention_roles = message.get("mention_roles")
    if not isinstance(mention_roles, list):
        mention_roles = []
    return (
        str(message.get("id") or "") == message_id
        and str(message.get("channel_id") or "") == channel_id
        and str(message.get("content") or "") == content
        and _run_marker(run_id) in str(message.get("content") or "")
        and [str(value) for value in mention_roles] == [role_id]
        and message.get("mention_everyone") is False
    )


def _message_has_run(
    message: dict,
    *,
    message_id: str,
    channel_id: str,
    run_id: str,
) -> bool:
    return (
        str(message.get("id") or "") == message_id
        and str(message.get("channel_id") or "") == channel_id
        and _run_marker(run_id) in str(message.get("content") or "")
    )


def _has_own_check_reaction(message: dict) -> bool:
    reactions = message.get("reactions")
    if not isinstance(reactions, list):
        return False
    for reaction in reactions:
        if not isinstance(reaction, dict):
            continue
        emoji = reaction.get("emoji")
        if not isinstance(emoji, dict):
            continue
        if str(emoji.get("name") or "") == "✅" and reaction.get("me") is True:
            return True
    return False


async def _verify_targets(
    env,
    stages: dict[str, int],
    retries: dict[str, int],
) -> str:
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "EVENT_CREATE_CHANNEL_ID")
    role_id = _env_text(env, "EVENT_CREATE_ROLE_ID")

    status, guild = await _request_stage(
        env,
        stages,
        retries,
        "target_guild",
        "GET",
        f"/guilds/{quote(guild_id, safe='')}",
    )
    if not (200 <= status < 300) or not isinstance(guild, dict):
        return f"discord_target_guild_failed_{status}"
    if str(guild.get("id") or "") != guild_id:
        return "discord_target_guild_mismatch"

    status, channel = await _request_stage(
        env,
        stages,
        retries,
        "target_channel",
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

    status, roles = await _request_stage(
        env,
        stages,
        retries,
        "target_role",
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
    if (
        not isinstance(role, dict)
        or role.get("mentionable") is not True
    ):
        return "discord_target_role_mismatch"
    return ""


async def _find_event_by_run(
    env,
    guild_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
    stage_key: str,
) -> tuple[str, str]:
    status, events = await _request_stage(
        env,
        stages,
        retries,
        stage_key,
        "GET",
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events?with_user_count=false",
    )
    if not (200 <= status < 300) or not isinstance(events, list):
        return "", f"discord_event_reconcile_failed_{status}"
    matches = [
        event
        for event in events
        if isinstance(event, dict)
        and str(event.get("guild_id") or "") == guild_id
        and _run_marker(run_id) in str(event.get("description") or "")
    ]
    if len(matches) > 1:
        return "", "discord_event_reconcile_ambiguous"
    if not matches:
        return "", ""
    event_id = str(matches[0].get("id") or "")
    if not event_id:
        return "", "discord_event_reconcile_invalid"
    return event_id, ""


async def _find_message_by_run(
    env,
    channel_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
    stage_key: str,
) -> tuple[str, str]:
    status, messages = await _request_stage(
        env,
        stages,
        retries,
        stage_key,
        "GET",
        f"/channels/{quote(channel_id, safe='')}/messages?limit=50",
    )
    if not (200 <= status < 300) or not isinstance(messages, list):
        return "", f"discord_message_reconcile_failed_{status}"
    matches = [
        message
        for message in messages
        if isinstance(message, dict)
        and str(message.get("channel_id") or "") == channel_id
        and _run_marker(run_id) in str(message.get("content") or "")
    ]
    if len(matches) > 1:
        return "", "discord_message_reconcile_ambiguous"
    if not matches:
        return "", ""
    message_id = str(matches[0].get("id") or "")
    if not message_id:
        return "", "discord_message_reconcile_invalid"
    return message_id, ""


async def _delete_event(
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
        status, event = await _request_stage(
            env,
            stages,
            retries,
            "event_cleanup_verify",
            "GET",
            path,
        )
        last_status = status
        if status == 404:
            return True, attempt, status, ""
        if not (200 <= status < 300) or not isinstance(event, dict):
            continue
        if not _event_has_run(
            event,
            event_id=event_id,
            guild_id=guild_id,
            run_id=run_id,
        ):
            return False, attempt, status, "cleanup_target_mismatch"
        status, _ = await _request_stage(
            env,
            stages,
            retries,
            "event_delete",
            "DELETE",
            path,
        )
        last_status = status
        if 200 <= status < 300 or status == 404:
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, "discord_event_delete_failed"


async def _delete_message(
    env,
    channel_id: str,
    message_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
) -> tuple[bool, int, int, str]:
    path = (
        f"/channels/{quote(channel_id, safe='')}/messages/{quote(message_id, safe='')}"
    )
    last_status = 0
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        status, message = await _request_stage(
            env,
            stages,
            retries,
            "message_cleanup_verify",
            "GET",
            path,
        )
        last_status = status
        if status == 404:
            return True, attempt, status, ""
        if not (200 <= status < 300) or not isinstance(message, dict):
            continue
        if not _message_has_run(
            message,
            message_id=message_id,
            channel_id=channel_id,
            run_id=run_id,
        ):
            return False, attempt, status, "cleanup_target_mismatch"
        status, _ = await _request_stage(
            env,
            stages,
            retries,
            "message_delete",
            "DELETE",
            path,
        )
        last_status = status
        if 200 <= status < 300 or status == 404:
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, "discord_message_delete_failed"


async def _cleanup_resources(env, manifest: dict) -> dict:
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "EVENT_CREATE_CHANNEL_ID")
    run_id = str(manifest.get("run_id") or "")
    event_id = str(manifest.get("event_id") or "")
    message_id = str(manifest.get("message_id") or "")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}

    event_resolve_error = ""
    if not event_id:
        event_id, event_resolve_error = await _find_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "event_cleanup_search",
        )
        if not event_resolve_error and not event_id and create_attempted.get("event") is True:
            event_resolve_error = "discord_event_ownership_unresolved"

    message_resolve_error = ""
    if not message_id:
        message_id, message_resolve_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "message_cleanup_search",
        )
        if (
            not message_resolve_error
            and not message_id
            and create_attempted.get("message") is True
        ):
            message_resolve_error = "discord_message_ownership_unresolved"

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

    error = message_error or event_error
    return {
        "ok": message_ok and event_ok,
        "attempts": max(message_attempts, event_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": error,
        "event_id": event_id,
        "message_id": message_id,
    }


def _target_fingerprints(guild_id: str, channel_id: str) -> dict[str, str]:
    return {
        "guild_id_sha256": _fingerprint(guild_id),
        "channel_id_sha256": _fingerprint(channel_id),
    }


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    guild_id: str,
    channel_id: str,
    role_id: str,
    event_id: str,
    message_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = {
        **_target_fingerprints(guild_id, channel_id),
        "role_id_sha256": _fingerprint(role_id),
    }
    if event_id:
        fingerprints["discord_event_id_sha256"] = _fingerprint(event_id)
    if message_id:
        fingerprints["discord_message_id_sha256"] = _fingerprint(message_id)
    return {
        "version": 1,
        "kind": "discord_event_message",
        "dirty": False,
        "last_run_id": run_id,
        "outcome": outcome,
        "cleanup_attempts": cleanup_attempts,
        "stages": dict(stages),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "resource_fingerprints": fingerprints,
    }


async def run_discord_crud_probe(
    env,
    state,
    run_id: str | None = None,
) -> dict:
    """専用Guildでevent/message CRUDを確認し、直後に削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}
    if await state.get_legacy_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE) is not None:
        return {
            "ok": False,
            "dirty": True,
            "error": "legacy_e2e_manifest_review_required",
        }

    current_manifest = await state.get_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE)
    if isinstance(current_manifest, dict) and current_manifest.get("dirty") is True:
        return {
            "ok": False,
            "dirty": True,
            "error": "environment_dirty",
            "cleanup_required": True,
        }

    token = _env_text(env, "DISCORD_TOKEN")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "EVENT_CREATE_CHANNEL_ID")
    role_id = _env_text(env, "EVENT_CREATE_ROLE_ID")
    if not token:
        return {"ok": False, "dirty": False, "error": "missing_discord_token"}
    if not guild_id:
        return {"ok": False, "dirty": False, "error": "missing_discord_guild_id"}
    if not channel_id:
        return {"ok": False, "dirty": False, "error": "missing_discord_channel_id"}
    if not role_id:
        return {"ok": False, "dirty": False, "error": "missing_discord_role_id"}

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
    manifest = {
        "version": 1,
        "kind": "discord_event_message",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(guild_id, channel_id),
        "stage": "planned",
        "create_attempted": {"event": False, "message": False},
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)

    error = ""
    event_id = ""
    event = _event_payload(run_id)
    manifest["create_attempted"]["event"] = True
    manifest["stage"] = "event_create_started"
    await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)
    create_status, created = await _request_stage(
        env,
        stages,
        retries,
        "event_create",
        "POST",
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events",
        event,
    )
    if 200 <= create_status < 300 and isinstance(created, dict):
        candidate_id = str(created.get("id") or "")
        if candidate_id and _event_matches(
            created,
            event_id=candidate_id,
            guild_id=guild_id,
            run_id=run_id,
            name=str(event["name"]),
            location="E2E initial location",
        ):
            event_id = candidate_id
    if not event_id:
        event_id, reconcile_error = await _find_event_by_run(
            env,
            guild_id,
            run_id,
            stages,
            retries,
            "event_create_reconcile",
        )
        if reconcile_error:
            error = reconcile_error
        elif not event_id:
            error = f"discord_event_create_failed_{create_status}"
    if event_id:
        manifest["event_id"] = event_id
        manifest["stage"] = "event_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)

    event_path = (
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events/"
        f"{quote(event_id, safe='')}"
    )
    if not error:
        status, read_event = await _request_stage(
            env,
            stages,
            retries,
            "event_read",
            "GET",
            event_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(read_event, dict)
            or not _event_matches(
                read_event,
                event_id=event_id,
                guild_id=guild_id,
                run_id=run_id,
                name=str(event["name"]),
                location="E2E initial location",
            )
        ):
            error = f"discord_event_read_verification_failed_{status}"

    event_update = _event_update_payload(run_id)
    if not error:
        status, updated_event = await _request_stage(
            env,
            stages,
            retries,
            "event_update",
            "PATCH",
            event_path,
            event_update,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(updated_event, dict)
            or not _event_matches(
                updated_event,
                event_id=event_id,
                guild_id=guild_id,
                run_id=run_id,
                name=str(event_update["name"]),
                location="E2E updated location",
            )
        ):
            error = f"discord_event_update_verification_failed_{status}"

    if not error:
        status, updated_read = await _request_stage(
            env,
            stages,
            retries,
            "event_read_updated",
            "GET",
            event_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(updated_read, dict)
            or not _event_matches(
                updated_read,
                event_id=event_id,
                guild_id=guild_id,
                run_id=run_id,
                name=str(event_update["name"]),
                location="E2E updated location",
            )
        ):
            error = f"discord_event_updated_read_verification_failed_{status}"

    message_id = ""
    initial_message = _message_payload(run_id, role_id, updated=False)
    if not error:
        manifest["create_attempted"]["message"] = True
        manifest["stage"] = "message_create_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)
        message_payload = {
            **initial_message,
            "nonce": uuid4().hex[:24],
            "enforce_nonce": True,
        }
        status, created_message = await _request_stage(
            env,
            stages,
            retries,
            "message_create",
            "POST",
            f"/channels/{quote(channel_id, safe='')}/messages",
            message_payload,
        )
        if 200 <= status < 300 and isinstance(created_message, dict):
            candidate_id = str(created_message.get("id") or "")
            if candidate_id and _message_matches(
                created_message,
                message_id=candidate_id,
                channel_id=channel_id,
                role_id=role_id,
                run_id=run_id,
                content=str(initial_message["content"]),
            ):
                message_id = candidate_id
        if not message_id:
            message_id, reconcile_error = await _find_message_by_run(
                env,
                channel_id,
                run_id,
                stages,
                retries,
                "message_create_reconcile",
            )
            if reconcile_error:
                error = reconcile_error
            elif not message_id:
                error = f"discord_message_create_failed_{status}"
        if message_id:
            manifest["message_id"] = message_id
            manifest["stage"] = "message_created"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)

    message_path = (
        f"/channels/{quote(channel_id, safe='')}/messages/{quote(message_id, safe='')}"
    )
    if not error:
        status, read_message = await _request_stage(
            env,
            stages,
            retries,
            "message_read",
            "GET",
            message_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(read_message, dict)
            or not _message_matches(
                read_message,
                message_id=message_id,
                channel_id=channel_id,
                role_id=role_id,
                run_id=run_id,
                content=str(initial_message["content"]),
            )
        ):
            error = f"discord_message_read_verification_failed_{status}"

    updated_message = _message_payload(run_id, role_id, updated=True)
    if not error:
        status, edited_message = await _request_stage(
            env,
            stages,
            retries,
            "message_update",
            "PATCH",
            message_path,
            updated_message,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(edited_message, dict)
            or not _message_matches(
                edited_message,
                message_id=message_id,
                channel_id=channel_id,
                role_id=role_id,
                run_id=run_id,
                content=str(updated_message["content"]),
            )
        ):
            error = f"discord_message_update_verification_failed_{status}"

    if not error:
        reaction_path = f"{message_path}/reactions/{quote('✅', safe='')}/@me"
        status, _ = await _request_stage(
            env,
            stages,
            retries,
            "message_reaction",
            "PUT",
            reaction_path,
        )
        if not (200 <= status < 300):
            error = f"discord_message_reaction_failed_{status}"

    if not error:
        status, updated_read = await _request_stage(
            env,
            stages,
            retries,
            "message_read_updated",
            "GET",
            message_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(updated_read, dict)
            or not _message_matches(
                updated_read,
                message_id=message_id,
                channel_id=channel_id,
                role_id=role_id,
                run_id=run_id,
                content=str(updated_message["content"]),
            )
            or not _has_own_check_reaction(updated_read)
        ):
            error = f"discord_message_updated_read_verification_failed_{status}"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest)
    stages.update(cleanup["stages"])
    for key, value in cleanup["rate_limit_retries"].items():
        retries[key] = retries.get(key, 0) + value
    cleanup_ok = bool(cleanup["ok"])
    cleanup_attempts = int(cleanup["attempts"])
    event_id = str(cleanup.get("event_id") or event_id)
    message_id = str(cleanup.get("message_id") or message_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            DISCORD_CRUD_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
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
        manifest["failure"] = str(cleanup.get("error") or "") or error
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["stages"] = dict(stages)
        if event_id:
            manifest["event_id"] = event_id
        if message_id:
            manifest["message_id"] = message_id
        await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)

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
    if retries:
        result["rate_limit_retries"] = retries
    if not cleanup_ok:
        result["error"] = str(cleanup.get("error") or "discord_cleanup_failed")
    elif error:
        result["error"] = error
    return result


async def cleanup_discord_crud_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """本人確認後にdirty manifestのDiscord資源削除を再実行する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}
    if await state.get_legacy_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE) is not None:
        return {
            "ok": False,
            "dirty": True,
            "error": "legacy_e2e_manifest_review_required",
        }
    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "discord_event_message":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    token = _env_text(env, "DISCORD_TOKEN")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "EVENT_CREATE_CHANNEL_ID")
    role_id = _env_text(env, "EVENT_CREATE_ROLE_ID")
    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and run_id != expected_run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    fingerprints = manifest.get("target_fingerprints")
    if not token:
        return {"ok": False, "dirty": True, "error": "missing_discord_token"}
    if (
        not guild_id
        or not channel_id
        or not role_id
        or not run_id
        or not isinstance(fingerprints, dict)
        or fingerprints != _target_fingerprints(guild_id, channel_id)
    ):
        return {"ok": False, "dirty": True, "error": "dirty_manifest_target_mismatch"}

    cleanup = await _cleanup_resources(env, manifest)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    stages.update(cleanup["stages"])
    attempts = int(cleanup["attempts"])
    event_id = str(cleanup.get("event_id") or "")
    message_id = str(cleanup.get("message_id") or "")
    if not cleanup["ok"]:
        manifest["stage"] = "cleanup_failed"
        manifest["cleanup_attempts"] = attempts
        manifest["failure"] = str(cleanup.get("error") or "discord_cleanup_failed")
        manifest["stages"] = stages
        if event_id:
            manifest["event_id"] = event_id
        if message_id:
            manifest["message_id"] = message_id
        await state.put_e2e_manifest(DISCORD_CRUD_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "discord_cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        DISCORD_CRUD_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            guild_id=guild_id,
            channel_id=channel_id,
            role_id=role_id,
            event_id=event_id,
            message_id=message_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    result = {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
    if cleanup["rate_limit_retries"]:
        result["rate_limit_retries"] = cleanup["rate_limit_retries"]
    return result
