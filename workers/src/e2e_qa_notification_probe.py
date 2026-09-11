"""Q&A更新通知の自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from e2e_discord_probe import (
    _delete_message,
    _find_message_by_run,
    _fingerprint,
    _int_value,
    _request_stage as _discord_request_stage,
    _run_marker,
)
from e2e_notion_probe import (
    _QA_SCHEMA,
    _canonical_id,
    _page_archived,
    _page_database_id,
    _property_number,
    _property_text,
    _request_stage as _notion_request_stage,
    _title,
    _verify_database,
)
from jobs import _run_qa_notification_pages


QA_NOTIFICATION_MANIFEST_SERVICE = "qa_notification"

_CLEANUP_MAX_ATTEMPTS = 4
_QUESTION_NUMBER = 700001


class _EphemeralQaState:
    """QA cacheを1回のprobe内だけに閉じ込める状態アダプター。"""

    def __init__(self) -> None:
        self.cache: dict = {}
        self.write_count = 0

    @staticmethod
    def enabled() -> bool:
        return True

    async def get_json(self, key: str, default=None):
        if key != "qa_cache" or not self.cache:
            return default
        return dict(self.cache)

    async def put_json_if_changed(self, key: str, payload) -> bool:
        if key != "qa_cache" or not isinstance(payload, dict):
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


def _question(run_id: str, *, updated: bool) -> str:
    state = "updated" if updated else "initial"
    return f"[E2E] QA notification {state} {_run_marker(run_id)}"


def _message_content(run_id: str) -> str:
    return (
        f"❓ 質問番号 #{_QUESTION_NUMBER} に更新があります\n"
        f"質問: {_question(run_id, updated=True)}\n"
        "回答: (回答なし)"
    )


def _create_payload(database_id: str, run_id: str) -> dict:
    return {
        "parent": {"database_id": database_id},
        "properties": {
            "質問": _title(_question(run_id, updated=False)),
            "回答": {"rich_text": []},
            "質問番号": {"number": _QUESTION_NUMBER},
        },
    }


def _update_payload(run_id: str) -> dict:
    return {
        "properties": {
            "質問": _title(_question(run_id, updated=True)),
        }
    }


def _qa_page_has_run(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    run_id: str,
) -> bool:
    return (
        _canonical_id(page.get("id")) == _canonical_id(page_id)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and _run_marker(run_id) in _property_text(page, "質問", "title")
    )


def _qa_page_matches(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    run_id: str,
    updated: bool,
) -> bool:
    return (
        _qa_page_has_run(
            page,
            page_id=page_id,
            database_id=database_id,
            run_id=run_id,
        )
        and not _page_archived(page)
        and _property_text(page, "質問", "title")
        == _question(run_id, updated=updated)
        and _property_text(page, "回答", "rich_text") == ""
        and _property_number(page, "質問番号") == _QUESTION_NUMBER
        and bool(str(page.get("last_edited_time") or ""))
    )


def _message_matches(
    message: dict,
    *,
    message_id: str,
    channel_id: str,
    run_id: str,
) -> bool:
    mention_roles = message.get("mention_roles")
    if not isinstance(mention_roles, list):
        mention_roles = []
    return (
        str(message.get("id") or "") == message_id
        and str(message.get("channel_id") or "") == channel_id
        and str(message.get("content") or "") == _message_content(run_id)
        and _run_marker(run_id) in str(message.get("content") or "")
        and mention_roles == []
        and message.get("mention_everyone") is False
    )


async def _verify_targets(env, stages: dict[str, int], retries: dict[str, int]) -> str:
    database_id = _env_text(env, "NOTION_QA_ID")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "QA_CHANNEL_ID")
    error = await _verify_database(
        env,
        database_id,
        _QA_SCHEMA,
        stages,
        retries,
        "target_notion_qa_database",
        "notion_qa",
    )
    if error:
        return error

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
        "target_discord_qa_channel",
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
    return ""


async def _find_qa_page_by_run(
    env,
    database_id: str,
    run_id: str,
    stages: dict[str, int],
    retries: dict[str, int],
    stage_key: str,
) -> tuple[str, str]:
    marker = _run_marker(run_id)
    status, data = await _notion_request_stage(
        env,
        stages,
        retries,
        stage_key,
        "POST",
        f"/databases/{quote(database_id, safe='')}/query",
        {
            "filter": {
                "property": "質問",
                "title": {"contains": marker},
            },
            "page_size": 10,
        },
    )
    if not (200 <= status < 300) or not isinstance(data, dict):
        return "", f"notion_qa_reconcile_failed_{status}"
    results = data.get("results")
    if not isinstance(results, list):
        return "", "notion_qa_reconcile_invalid"
    matches = [
        page
        for page in results
        if isinstance(page, dict)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and marker in _property_text(page, "質問", "title")
        and not _page_archived(page)
    ]
    if len(matches) > 1:
        return "", "notion_qa_reconcile_ambiguous"
    if not matches:
        return "", ""
    page_id = str(matches[0].get("id") or "")
    if not _canonical_id(page_id):
        return "", "notion_qa_reconcile_invalid"
    return page_id, ""


async def _archive_qa_page(
    env,
    database_id: str,
    page_id: str,
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
        if not _qa_page_has_run(
            page,
            page_id=page_id,
            database_id=database_id,
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
            and _qa_page_has_run(
                archived,
                page_id=page_id,
                database_id=database_id,
                run_id=run_id,
            )
            and _page_archived(archived)
        ):
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, "notion_qa_archive_failed"


async def _cleanup_resources(env, manifest: dict) -> dict:
    database_id = _env_text(env, "NOTION_QA_ID")
    channel_id = _env_text(env, "QA_CHANNEL_ID")
    run_id = str(manifest.get("run_id") or "")
    notion_page_id = str(manifest.get("notion_page_id") or "")
    discord_message_id = str(manifest.get("discord_message_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}

    message_error = ""
    if not discord_message_id:
        discord_message_id, message_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "discord_cleanup_search",
        )
        if (
            not message_error
            and not discord_message_id
            and create_attempted.get("discord_message") is True
        ):
            message_error = "discord_message_ownership_unresolved"

    message_ok = not message_error
    message_attempts = 1
    if message_ok and discord_message_id:
        message_ok, message_attempts, _, message_error = await _delete_message(
            env,
            channel_id,
            discord_message_id,
            run_id,
            stages,
            retries,
        )

    page_error = ""
    if not notion_page_id:
        notion_page_id, page_error = await _find_qa_page_by_run(
            env,
            database_id,
            run_id,
            stages,
            retries,
            "notion_cleanup_search",
        )
        if (
            not page_error
            and not notion_page_id
            and create_attempted.get("notion_page") is True
        ):
            page_error = "notion_qa_ownership_unresolved"

    page_ok = not page_error
    page_attempts = 1
    if page_ok and notion_page_id:
        page_ok, page_attempts, _, page_error = await _archive_qa_page(
            env,
            database_id,
            notion_page_id,
            run_id,
            stages,
            retries,
        )

    return {
        "ok": message_ok and page_ok,
        "attempts": max(message_attempts, page_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": message_error or page_error,
        "discord_message_id": discord_message_id,
        "notion_page_id": notion_page_id,
    }


def _target_fingerprints(
    database_id: str,
    guild_id: str,
    channel_id: str,
) -> dict[str, str]:
    return {
        "notion_qa_database_id_sha256": _fingerprint(_canonical_id(database_id)),
        "discord_guild_id_sha256": _fingerprint(guild_id),
        "discord_channel_id_sha256": _fingerprint(channel_id),
    }


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    rate_limit_retries: dict[str, int],
    database_id: str,
    guild_id: str,
    channel_id: str,
    notion_page_id: str,
    discord_message_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(database_id, guild_id, channel_id)
    if notion_page_id:
        fingerprints["notion_page_id_sha256"] = _fingerprint(notion_page_id)
    if discord_message_id:
        fingerprints["discord_message_id_sha256"] = _fingerprint(discord_message_id)
    return {
        "version": 1,
        "kind": "qa_notification_job",
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


def _configuration_error(env) -> str:
    if not _env_text(env, "NOTION_TOKEN"):
        return "missing_notion_token"
    if not _canonical_id(_env_text(env, "NOTION_QA_ID")):
        return "invalid_notion_qa_database_id"
    if not _env_text(env, "DISCORD_TOKEN"):
        return "missing_discord_token"
    if not _env_text(env, "DISCORD_GUILD_ID"):
        return "missing_discord_guild_id"
    if not _env_text(env, "QA_CHANNEL_ID"):
        return "missing_qa_channel_id"
    if not _explicitly_disabled(env, "CRON_ENABLE_QA"):
        return "cron_qa_must_be_disabled"
    return ""


async def run_qa_notification_probe(
    env,
    state,
    run_id: str | None = None,
) -> dict:
    """所有QAページだけへ通知処理を適用し、ページとメッセージを削除する。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}

    current_manifest = await state.get_e2e_manifest(
        QA_NOTIFICATION_MANIFEST_SERVICE
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

    database_id = _env_text(env, "NOTION_QA_ID")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "QA_CHANNEL_ID")
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
    existing_page_id, precheck_error = await _find_qa_page_by_run(
        env,
        database_id,
        run_id,
        stages,
        retries,
        "notion_precheck",
    )
    if precheck_error:
        return {
            "ok": False,
            "dirty": False,
            "error": precheck_error,
            "stages": stages,
        }
    if existing_page_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "notion_qa_run_collision",
            "stages": stages,
        }
    existing_message_id, precheck_error = await _find_message_by_run(
        env,
        channel_id,
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
    if existing_message_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "discord_message_run_collision",
            "stages": stages,
        }

    manifest = {
        "version": 1,
        "kind": "qa_notification_job",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(
            database_id,
            guild_id,
            channel_id,
        ),
        "create_attempted": {
            "notion_page": False,
            "discord_message": False,
        },
        "stage": "planned",
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)

    error = ""
    notion_page_id = ""
    discord_message_id = ""
    manifest["create_attempted"]["notion_page"] = True
    manifest["stage"] = "notion_create_started"
    await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)
    create_status, created_page = await _notion_request_stage(
        env,
        stages,
        retries,
        "notion_create",
        "POST",
        "/pages",
        _create_payload(database_id, run_id),
    )
    if 200 <= create_status < 300 and isinstance(created_page, dict):
        candidate_id = str(created_page.get("id") or "")
        if candidate_id and _qa_page_matches(
            created_page,
            page_id=candidate_id,
            database_id=database_id,
            run_id=run_id,
            updated=False,
        ):
            notion_page_id = candidate_id
    if not notion_page_id:
        notion_page_id, reconcile_error = await _find_qa_page_by_run(
            env,
            database_id,
            run_id,
            stages,
            retries,
            "notion_create_reconcile",
        )
        if reconcile_error:
            error = reconcile_error
        elif not notion_page_id:
            error = "notion_qa_ownership_unresolved"
    if notion_page_id:
        manifest["notion_page_id"] = notion_page_id
        manifest["stage"] = "notion_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)

    page_path = f"/pages/{quote(notion_page_id, safe='')}"
    initial_page = created_page
    initial_edited_at = ""
    if not error:
        read_status, initial_page = await _notion_request_stage(
            env,
            stages,
            retries,
            "notion_read_initial",
            "GET",
            page_path,
        )
        if not (
            200 <= read_status < 300
            and isinstance(initial_page, dict)
            and _qa_page_matches(
                initial_page,
                page_id=notion_page_id,
                database_id=database_id,
                run_id=run_id,
                updated=False,
            )
        ):
            error = "notion_qa_initial_verification_failed"
        else:
            initial_edited_at = str(initial_page.get("last_edited_time") or "")

    qa_state = _EphemeralQaState()
    if not error:
        manifest["create_attempted"]["discord_message"] = True
        manifest["stage"] = "qa_first_run_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)
        try:
            first_detail = await _run_qa_notification_pages(
                env,
                qa_state,
                [initial_page],
                return_detail=True,
            )
        except Exception:
            first_detail = {}
        first_ok = (
            isinstance(first_detail, dict)
            and first_detail.get("ok") is True
            and first_detail.get("first_run") is True
            and first_detail.get("failed_count") == 0
            and qa_state.cache.get("_first_qa_run") is False
            and qa_state.cache.get(notion_page_id) == initial_edited_at
            and qa_state.write_count == 1
        )
        stages["job_first_run"] = 200 if first_ok else 500
        if not first_ok:
            error = "qa_first_run_failed"

    if not error:
        first_message_id, find_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "discord_first_run_verify",
        )
        if find_error:
            error = find_error
        elif first_message_id:
            discord_message_id = first_message_id
            manifest["discord_message_id"] = discord_message_id
            error = "qa_first_run_not_suppressed"
        else:
            manifest["create_attempted"]["discord_message"] = False
            manifest["stage"] = "qa_first_run_verified"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)

    updated_page = initial_page
    updated_edited_at = ""
    if not error:
        update_status, updated_page = await _notion_request_stage(
            env,
            stages,
            retries,
            "notion_update",
            "PATCH",
            page_path,
            _update_payload(run_id),
        )
        if not (
            200 <= update_status < 300
            and isinstance(updated_page, dict)
            and _qa_page_matches(
                updated_page,
                page_id=notion_page_id,
                database_id=database_id,
                run_id=run_id,
                updated=True,
            )
        ):
            error = "notion_qa_update_failed"

    if not error:
        read_status, updated_page = await _notion_request_stage(
            env,
            stages,
            retries,
            "notion_read_updated",
            "GET",
            page_path,
        )
        updated_edited_at = str((updated_page or {}).get("last_edited_time") or "")
        if not (
            200 <= read_status < 300
            and isinstance(updated_page, dict)
            and _qa_page_matches(
                updated_page,
                page_id=notion_page_id,
                database_id=database_id,
                run_id=run_id,
                updated=True,
            )
        ):
            error = "notion_qa_updated_verification_failed"

    if not error:
        if qa_state.cache.get(notion_page_id) == updated_edited_at:
            qa_state.cache[notion_page_id] = f"{initial_edited_at}#e2e-before-update"
        stages["qa_cache_miss_prepare"] = 200
        manifest["create_attempted"]["discord_message"] = True
        manifest["stage"] = "qa_notification_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)
        try:
            notify_detail = await _run_qa_notification_pages(
                env,
                qa_state,
                [updated_page],
                return_detail=True,
            )
        except Exception:
            notify_detail = {}
        notify_ok = (
            isinstance(notify_detail, dict)
            and notify_detail.get("ok") is True
            and notify_detail.get("first_run") is False
            and notify_detail.get("failed_count") == 0
        )
        stages["job_notify"] = 200 if notify_ok else 500
        if not notify_ok:
            error = "qa_notification_job_failed"

    if manifest["create_attempted"].get("discord_message") is True:
        discord_message_id, find_error = await _find_message_by_run(
            env,
            channel_id,
            run_id,
            stages,
            retries,
            "discord_message_find",
        )
        if find_error:
            error = find_error
        elif not discord_message_id:
            error = "discord_message_ownership_unresolved"
        else:
            manifest["discord_message_id"] = discord_message_id
            manifest["stage"] = "discord_message_found"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)

    if not error and discord_message_id:
        message_status, message = await _discord_request_stage(
            env,
            stages,
            retries,
            "discord_message_read",
            "GET",
            (
                f"/channels/{quote(channel_id, safe='')}/messages/"
                f"{quote(discord_message_id, safe='')}"
            ),
        )
        if not (
            200 <= message_status < 300
            and isinstance(message, dict)
            and _message_matches(
                message,
                message_id=discord_message_id,
                channel_id=channel_id,
                run_id=run_id,
            )
        ):
            error = "discord_message_verification_failed"

    if not error:
        cache_ok = (
            qa_state.cache.get("_first_qa_run") is False
            and qa_state.cache.get(notion_page_id) == updated_edited_at
            and qa_state.write_count == 2
        )
        stages["qa_cache_verify"] = 200 if cache_ok else 500
        if not cache_ok:
            error = "qa_cache_verification_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest)
    stages.update(cleanup["stages"])
    retries.update(cleanup["rate_limit_retries"])
    cleanup_ok = cleanup["ok"] is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    notion_page_id = str(cleanup.get("notion_page_id") or notion_page_id)
    discord_message_id = str(
        cleanup.get("discord_message_id") or discord_message_id
    )

    if cleanup_ok:
        await state.put_e2e_manifest(
            QA_NOTIFICATION_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                database_id=database_id,
                guild_id=guild_id,
                channel_id=channel_id,
                notion_page_id=notion_page_id,
                discord_message_id=discord_message_id,
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
        if discord_message_id:
            manifest["discord_message_id"] = discord_message_id
        await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)

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


async def cleanup_qa_notification_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """run IDと対象fingerprint確認後にdirtyなQA通知資源を回収する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}

    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "qa_notification_job":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    database_id = _env_text(env, "NOTION_QA_ID")
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    channel_id = _env_text(env, "QA_CHANNEL_ID")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    if (
        not run_id
        or not _canonical_id(database_id)
        or not _env_text(env, "NOTION_TOKEN")
        or not guild_id
        or not channel_id
        or not _env_text(env, "DISCORD_TOKEN")
        or manifest.get("target_fingerprints")
        != _target_fingerprints(database_id, guild_id, channel_id)
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
    notion_page_id = str(cleanup.get("notion_page_id") or "")
    discord_message_id = str(cleanup.get("discord_message_id") or "")

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if notion_page_id:
            manifest["notion_page_id"] = notion_page_id
        if discord_message_id:
            manifest["discord_message_id"] = discord_message_id
        await state.put_e2e_manifest(QA_NOTIFICATION_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        QA_NOTIFICATION_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            database_id=database_id,
            guild_id=guild_id,
            channel_id=channel_id,
            notion_page_id=notion_page_id,
            discord_message_id=discord_message_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    return {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
