"""Notion自動cleanupジョブの自己cleanup型E2Eシナリオ。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from e2e_notion_probe import (
    _EVENT_SCHEMA,
    _archive_page,
    _canonical_id,
    _find_page_by_marker,
    _fingerprint,
    _page_archived,
    _page_database_id,
    _property_text,
    _request_stage as _notion_request_stage,
    _rich_text,
    _run_marker,
    _title,
    _verify_database,
)
from jobs import _notion_archive_page, _run_auto_clean_pages


NOTION_CLEANUP_MANIFEST_SERVICE = "notion_cleanup"

_MANIFEST_KIND = "notion_cleanup_job"
_MARKER_PROPERTY = "GoogleイベントID"


class _EphemeralCleanupState:
    """cleanupの実行時刻を1回のprobe内だけに閉じ込める状態アダプター。"""

    def __init__(self) -> None:
        self.last_epoch = ""
        self.write_count = 0

    @staticmethod
    def enabled() -> bool:
        return True

    async def get_text(self, key: str) -> str | None:
        if key != "cleanup:last_epoch":
            return None
        return self.last_epoch or None

    async def put_text(self, key: str, value: str) -> None:
        if key != "cleanup:last_epoch":
            return
        self.last_epoch = str(value)
        self.write_count += 1


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


def _page_marker(run_id: str, page_kind: str) -> str:
    return f"{_run_marker(run_id)}:{page_kind}"


def _target_fingerprints(database_id: str) -> dict[str, str]:
    return {
        "notion_event_database_id_sha256": _fingerprint(
            _canonical_id(database_id)
        )
    }


def _page_payload(
    database_id: str,
    run_id: str,
    page_kind: str,
    probe_now: datetime,
) -> tuple[dict, dict]:
    marker = _page_marker(run_id, page_kind)
    if page_kind == "due":
        start = probe_now - timedelta(hours=1)
        end = probe_now - timedelta(minutes=1)
    else:
        start = probe_now + timedelta(days=7)
        end = start + timedelta(minutes=30)
    expected = {
        "title": f"[E2E] Notion cleanup {page_kind} {run_id}",
        "content": f"IE Event Bot cleanup E2E fixture; safe to archive. {marker}",
        "marker": marker,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    return (
        {
            "parent": {"database_id": database_id},
            "properties": {
                "イベント名": _title(expected["title"]),
                "内容": _rich_text(expected["content"]),
                "日時": {
                    "date": {
                        "start": expected["start"],
                        "end": expected["end"],
                    }
                },
                "場所": _rich_text("E2E cleanup fixture"),
                "メッセージID": _rich_text(""),
                "作成者ID": _rich_text("ie-event-bot-e2e"),
                "ページID": _rich_text(""),
                "イベントURL": {
                    "url": "https://example.invalid/ie-event-bot-e2e"
                },
                _MARKER_PROPERTY: _rich_text(marker),
            },
        },
        expected,
    )


def _property_date(page: dict, property_name: str) -> dict:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return {}
    prop = properties.get(property_name)
    if not isinstance(prop, dict):
        return {}
    value = prop.get("date")
    return value if isinstance(value, dict) else {}


def _timestamp_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _same_timestamp(actual: object, expected: object) -> bool:
    actual_utc = _timestamp_utc(actual)
    expected_utc = _timestamp_utc(expected)
    return actual_utc is not None and actual_utc == expected_utc


def _page_matches(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    expected: dict,
    archived: bool,
) -> bool:
    date_value = _property_date(page, "日時")
    return (
        _canonical_id(page.get("id")) == _canonical_id(page_id)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and _property_text(page, "イベント名", "title") == expected["title"]
        and _property_text(page, "内容", "rich_text") == expected["content"]
        and _property_text(page, _MARKER_PROPERTY, "rich_text")
        == expected["marker"]
        and _same_timestamp(date_value.get("start"), expected["start"])
        and _same_timestamp(date_value.get("end"), expected["end"])
        and _page_archived(page) is archived
    )


def _configuration_error(env) -> str:
    if not _env_text(env, "NOTION_TOKEN"):
        return "missing_notion_token"
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    if not database_id:
        return "missing_notion_event_database_id"
    if not _canonical_id(database_id):
        return "invalid_notion_event_database_id"
    if not _explicitly_disabled(env, "CRON_ENABLE_AUTO_CLEAN"):
        return "cron_auto_clean_must_be_disabled"
    return ""


async def _create_owned_page(
    env,
    state,
    manifest: dict,
    *,
    database_id: str,
    run_id: str,
    page_kind: str,
    payload: dict,
    expected: dict,
    stages: dict[str, int],
    retries: dict[str, int],
) -> tuple[str, dict, str]:
    attempted_key = f"{page_kind}_page"
    manifest["create_attempted"][attempted_key] = True
    manifest["stage"] = f"{page_kind}_create_started"
    manifest["stages"] = dict(stages)
    await state.put_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE, manifest)

    status, created = await _notion_request_stage(
        env,
        stages,
        retries,
        f"{page_kind}_create",
        "POST",
        "/pages",
        payload,
    )
    page_id = ""
    if 200 <= status < 300 and isinstance(created, dict):
        candidate_id = str(created.get("id") or "")
        if candidate_id and _page_matches(
            created,
            page_id=candidate_id,
            database_id=database_id,
            expected=expected,
            archived=False,
        ):
            page_id = candidate_id

    if not page_id:
        page_id, reconcile_error = await _find_page_by_marker(
            env,
            database_id,
            _MARKER_PROPERTY,
            str(expected["marker"]),
            stages,
            retries,
            f"{page_kind}_create_reconcile",
            f"notion_{page_kind}",
        )
        if reconcile_error:
            return "", {}, reconcile_error
        if not page_id:
            return "", {}, f"notion_{page_kind}_ownership_unresolved"

    manifest[f"{page_kind}_page_id"] = page_id
    manifest["stage"] = f"{page_kind}_created"
    manifest["stages"] = dict(stages)
    await state.put_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE, manifest)

    read_status, page = await _notion_request_stage(
        env,
        stages,
        retries,
        f"{page_kind}_read",
        "GET",
        f"/pages/{quote(page_id, safe='')}",
    )
    if not (
        200 <= read_status < 300
        and isinstance(page, dict)
        and _page_matches(
            page,
            page_id=page_id,
            database_id=database_id,
            expected=expected,
            archived=False,
        )
    ):
        return (
            page_id,
            page if isinstance(page, dict) else {},
            f"notion_{page_kind}_read_verification_failed_{read_status}",
        )
    return page_id, page, ""


async def _cleanup_resources(env, manifest: dict) -> dict:
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    run_id = str(manifest.get("run_id") or "")
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}

    page_ids = {
        "due": str(manifest.get("due_page_id") or ""),
        "future": str(manifest.get("future_page_id") or ""),
    }
    resolve_errors = {"due": "", "future": ""}
    for page_kind in ("due", "future"):
        if page_ids[page_kind] or create_attempted.get(f"{page_kind}_page") is not True:
            continue
        page_ids[page_kind], resolve_errors[page_kind] = await _find_page_by_marker(
            env,
            database_id,
            _MARKER_PROPERTY,
            _page_marker(run_id, page_kind),
            stages,
            retries,
            f"{page_kind}_cleanup_search",
            f"notion_{page_kind}",
        )
        if not resolve_errors[page_kind] and not page_ids[page_kind]:
            resolve_errors[page_kind] = f"notion_{page_kind}_ownership_unresolved"

    cleanup_results: dict[str, tuple[bool, int, str]] = {}
    for page_kind in ("due", "future"):
        error = resolve_errors[page_kind]
        page_id = page_ids[page_kind]
        if error:
            cleanup_results[page_kind] = (False, 1, error)
            continue
        if not page_id:
            cleanup_results[page_kind] = (True, 1, "")
            continue
        ok, attempts, _, cleanup_error = await _archive_page(
            env,
            database_id,
            page_id,
            _MARKER_PROPERTY,
            _page_marker(run_id, page_kind),
            stages,
            retries,
            page_kind,
        )
        cleanup_results[page_kind] = (ok, attempts, cleanup_error)

    due_ok, due_attempts, due_error = cleanup_results["due"]
    future_ok, future_attempts, future_error = cleanup_results["future"]
    return {
        "ok": due_ok and future_ok,
        "attempts": max(due_attempts, future_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": due_error or future_error,
        "due_page_id": page_ids["due"],
        "future_page_id": page_ids["future"],
    }


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    rate_limit_retries: dict[str, int],
    database_id: str,
    due_page_id: str,
    future_page_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(database_id)
    if due_page_id:
        fingerprints["due_page_id_sha256"] = _fingerprint(due_page_id)
    if future_page_id:
        fingerprints["future_page_id_sha256"] = _fingerprint(future_page_id)
    return {
        "version": 1,
        "kind": _MANIFEST_KIND,
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


async def run_notion_cleanup_probe(env, state, run_id: str | None = None) -> dict:
    """所有ページだけへcleanupジョブを適用し、残存ページをarchiveする。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {
            "ok": False,
            "dirty": False,
            "error": "sync_coordinator_required",
        }

    current_manifest = await state.get_e2e_manifest(
        NOTION_CLEANUP_MANIFEST_SERVICE
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

    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    target_error = await _verify_database(
        env,
        database_id,
        _EVENT_SCHEMA,
        stages,
        retries,
        "target_notion_event_database",
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
    for page_kind in ("due", "future"):
        existing_page_id, precheck_error = await _find_page_by_marker(
            env,
            database_id,
            _MARKER_PROPERTY,
            _page_marker(run_id, page_kind),
            stages,
            retries,
            f"{page_kind}_precheck",
            f"notion_{page_kind}",
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
                "error": f"notion_{page_kind}_run_collision",
                "stages": stages,
            }

    probe_now = datetime.now(timezone.utc).replace(microsecond=0)
    manifest = {
        "version": 1,
        "kind": _MANIFEST_KIND,
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(database_id),
        "create_attempted": {"due_page": False, "future_page": False},
        "stage": "planned",
        "stages": dict(stages),
        "created_at": probe_now.isoformat(),
    }
    await state.put_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE, manifest)

    error = ""
    due_page_id = ""
    future_page_id = ""
    due_page: dict = {}
    future_page: dict = {}
    due_payload, due_expected = _page_payload(
        database_id,
        run_id,
        "due",
        probe_now,
    )
    future_payload, future_expected = _page_payload(
        database_id,
        run_id,
        "future",
        probe_now,
    )

    due_page_id, due_page, error = await _create_owned_page(
        env,
        state,
        manifest,
        database_id=database_id,
        run_id=run_id,
        page_kind="due",
        payload=due_payload,
        expected=due_expected,
        stages=stages,
        retries=retries,
    )
    if not error:
        future_page_id, future_page, error = await _create_owned_page(
            env,
            state,
            manifest,
            database_id=database_id,
            run_id=run_id,
            page_kind="future",
            payload=future_payload,
            expected=future_expected,
            stages=stages,
            retries=retries,
        )

    cleanup_state = _EphemeralCleanupState()
    archive_calls: list[str] = []

    async def tracked_archive(archive_env, page_id: str) -> bool:
        if page_id not in {due_page_id, future_page_id}:
            return False
        manifest["job_archive_attempted"] = True
        manifest["stage"] = "job_archive_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE, manifest)
        archive_calls.append(page_id)
        return await _notion_archive_page(archive_env, page_id)

    if not error:
        try:
            detail = await _run_auto_clean_pages(
                env,
                cleanup_state,
                [due_page, future_page],
                now_utc=probe_now,
                return_detail=True,
                archive_page=tracked_archive,
            )
        except Exception:
            detail = {}
        job_ok = (
            detail == {"ok": True, "scanned": 2, "archived": 1}
            and archive_calls == [due_page_id]
            and cleanup_state.last_epoch == str(probe_now.timestamp())
            and cleanup_state.write_count == 1
        )
        stages["job_cleanup"] = 200 if job_ok else 500
        if not job_ok:
            error = "notion_cleanup_job_failed"

    if not error:
        due_status, due_after = await _notion_request_stage(
            env,
            stages,
            retries,
            "due_job_verify",
            "GET",
            f"/pages/{quote(due_page_id, safe='')}",
        )
        if not (
            200 <= due_status < 300
            and isinstance(due_after, dict)
            and _page_matches(
                due_after,
                page_id=due_page_id,
                database_id=database_id,
                expected=due_expected,
                archived=True,
            )
        ):
            error = "notion_due_job_verification_failed"

    if not error:
        future_status, future_after = await _notion_request_stage(
            env,
            stages,
            retries,
            "future_job_verify",
            "GET",
            f"/pages/{quote(future_page_id, safe='')}",
        )
        if not (
            200 <= future_status < 300
            and isinstance(future_after, dict)
            and _page_matches(
                future_after,
                page_id=future_page_id,
                database_id=database_id,
                expected=future_expected,
                archived=False,
            )
        ):
            error = "notion_future_job_verification_failed"

    if not error:
        try:
            duplicate_detail = await _run_auto_clean_pages(
                env,
                cleanup_state,
                [due_page, future_page],
                now_utc=probe_now,
                return_detail=True,
                archive_page=tracked_archive,
            )
        except Exception:
            duplicate_detail = {}
        guard_ok = (
            duplicate_detail
            == {"ok": True, "skipped": True, "reason": "interval_guard"}
            and archive_calls == [due_page_id]
            and cleanup_state.last_epoch == str(probe_now.timestamp())
            and cleanup_state.write_count == 1
        )
        stages["job_interval_guard"] = 200 if guard_ok else 500
        if not guard_ok:
            error = "notion_cleanup_interval_guard_failed"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest)
    stages.update(cleanup["stages"])
    for key, value in cleanup["rate_limit_retries"].items():
        retries[key] = retries.get(key, 0) + value
    cleanup_ok = cleanup.get("ok") is True
    cleanup_attempts = int(cleanup.get("attempts") or 1)
    due_page_id = str(cleanup.get("due_page_id") or due_page_id)
    future_page_id = str(cleanup.get("future_page_id") or future_page_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            NOTION_CLEANUP_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                rate_limit_retries=retries,
                database_id=database_id,
                due_page_id=due_page_id,
                future_page_id=future_page_id,
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
    else:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(
            cleanup.get("error") or error or "notion_cleanup_failed"
        )
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["stages"] = dict(stages)
        manifest["rate_limit_retries"] = dict(retries)
        if due_page_id:
            manifest["due_page_id"] = due_page_id
        if future_page_id:
            manifest["future_page_id"] = future_page_id
        await state.put_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE, manifest)

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
        result["error"] = str(
            cleanup.get("error") or error or "notion_cleanup_failed"
        )
    if retries:
        result["rate_limit_retries"] = retries
    return result


async def cleanup_notion_cleanup_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """run IDと対象fingerprint確認後にdirtyなNotionページを回収する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}

    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != _MANIFEST_KIND:
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    run_id = str(manifest.get("run_id") or "")
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    if expected_run_id and expected_run_id != run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    if not _env_text(env, "NOTION_TOKEN"):
        return {"ok": False, "dirty": True, "error": "missing_notion_token"}
    if (
        not run_id
        or not _canonical_id(database_id)
        or manifest.get("target_fingerprints")
        != _target_fingerprints(database_id)
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
    due_page_id = str(cleanup.get("due_page_id") or "")
    future_page_id = str(cleanup.get("future_page_id") or "")

    if cleanup.get("ok") is not True:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "notion_cleanup_failed")
        manifest["cleanup_attempts"] = attempts
        manifest["stages"] = stages
        manifest["rate_limit_retries"] = retries
        if due_page_id:
            manifest["due_page_id"] = due_page_id
        if future_page_id:
            manifest["future_page_id"] = future_page_id
        await state.put_e2e_manifest(NOTION_CLEANUP_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "notion_cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        NOTION_CLEANUP_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            rate_limit_retries=retries,
            database_id=database_id,
            due_page_id=due_page_id,
            future_page_id=future_page_id,
            started_at=str(manifest.get("created_at") or "") or None,
        ),
    )
    result = {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": attempts},
    }
    if retries:
        result["rate_limit_retries"] = retries
    return result
