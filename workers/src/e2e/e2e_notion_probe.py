import asyncio
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from workers import fetch as _runtime_fetch


NOTION_CRUD_MANIFEST_SERVICE = "notion"

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_CLEANUP_MAX_ATTEMPTS = 4
_RATE_LIMIT_MAX_ATTEMPTS = 4
_RATE_LIMIT_MAX_DELAY_SECONDS = 10.0
_JST = timezone(timedelta(hours=9))

_EVENT_SCHEMA = {
    "イベント名": "title",
    "内容": "rich_text",
    "日時": "date",
    "場所": "rich_text",
    "メッセージID": "rich_text",
    "作成者ID": "rich_text",
    "ページID": "rich_text",
    "イベントURL": "url",
    "GoogleイベントID": "rich_text",
}
_QA_SCHEMA = {
    "質問": "title",
    "回答": "rich_text",
    "質問番号": "number",
}


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


def _canonical_id(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "")
    if len(text) != 32 or any(char not in "0123456789abcdef" for char in text):
        return ""
    return text


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"E2E-{timestamp}-{uuid4().hex[:8]}"


def _run_marker(run_id: str) -> str:
    return f"[ie-event-bot-e2e:{run_id}]"


def _rich_text(content: str) -> dict:
    return {"rich_text": [{"text": {"content": content}}]}


def _title(content: str) -> dict:
    return {"title": [{"text": {"content": content}}]}


def _header_text(response, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        value = headers.get(name)
    except Exception:
        value = None
    return str(value or "").strip()


def _retry_after_seconds(response) -> float | None:
    raw = _header_text(response, "Retry-After")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


async def _notion_request(
    env,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, Any, int]:
    """小さなNotion JSON APIを呼び、429だけ公式待機値で再試行する。"""
    token = _env_text(env, "NOTION_TOKEN")
    if not token:
        return 0, {}, 0

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Notion-Version": _NOTION_VERSION,
    }
    options: dict[str, Any] = {
        "method": method.upper(),
        "headers": headers,
    }
    if payload is not None:
        options["body"] = json.dumps(payload, ensure_ascii=False)

    for request_number in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        try:
            response = await fetch(f"{_NOTION_API_BASE}{path}", options)
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

        retry_after = _retry_after_seconds(response)
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
    status, data, retry_count = await _notion_request(env, method, path, payload)
    stages[key] = status
    if retry_count:
        retries[key] = retries.get(key, 0) + retry_count
    return status, data


def _schema_matches(database: dict, expected: dict[str, str]) -> bool:
    properties = database.get("properties")
    if not isinstance(properties, dict):
        return False
    for name, expected_type in expected.items():
        prop = properties.get(name)
        if not isinstance(prop, dict) or str(prop.get("type") or "") != expected_type:
            return False
    return True


async def _verify_database(
    env,
    database_id: str,
    expected_schema: dict[str, str],
    stages: dict[str, int],
    retries: dict[str, int],
    stage_key: str,
    error_prefix: str,
) -> str:
    status, database = await _request_stage(
        env,
        stages,
        retries,
        stage_key,
        "GET",
        f"/databases/{quote(database_id, safe='')}",
    )
    if not (200 <= status < 300) or not isinstance(database, dict):
        return f"{error_prefix}_database_failed_{status}"
    if (
        str(database.get("object") or "") != "database"
        or _canonical_id(database.get("id")) != _canonical_id(database_id)
    ):
        return f"{error_prefix}_database_mismatch"
    if not _schema_matches(database, expected_schema):
        return f"{error_prefix}_schema_mismatch"
    return ""


async def _verify_targets(
    env,
    stages: dict[str, int],
    retries: dict[str, int],
) -> str:
    event_database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    qa_database_id = _env_text(env, "NOTION_QA_ID")
    event_canonical = _canonical_id(event_database_id)
    qa_canonical = _canonical_id(qa_database_id)
    if not event_canonical:
        return "invalid_notion_event_database_id"
    if not qa_canonical:
        return "invalid_notion_qa_database_id"
    if event_canonical == qa_canonical:
        return "notion_database_ids_must_differ"

    error = await _verify_database(
        env,
        event_database_id,
        _EVENT_SCHEMA,
        stages,
        retries,
        "target_event_database",
        "notion_event",
    )
    if error:
        return error
    return await _verify_database(
        env,
        qa_database_id,
        _QA_SCHEMA,
        stages,
        retries,
        "target_qa_database",
        "notion_qa",
    )


def _property_text(page: dict, property_name: str, property_type: str) -> str:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return ""
    prop = properties.get(property_name)
    if not isinstance(prop, dict):
        return ""
    nodes = prop.get(property_type)
    if not isinstance(nodes, list) or not nodes:
        return ""
    node = nodes[0]
    if not isinstance(node, dict):
        return ""
    plain_text = str(node.get("plain_text") or "").strip()
    if plain_text:
        return plain_text
    text = node.get("text")
    if not isinstance(text, dict):
        return ""
    return str(text.get("content") or "").strip()


def _property_number(page: dict, property_name: str) -> int | None:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return None
    prop = properties.get(property_name)
    if not isinstance(prop, dict):
        return None
    value = prop.get("number")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _page_database_id(page: dict) -> str:
    parent = page.get("parent")
    if not isinstance(parent, dict):
        return ""
    return str(parent.get("database_id") or "")


def _page_archived(page: dict) -> bool:
    return page.get("archived") is True or page.get("in_trash") is True


def _page_has_marker(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    marker_property: str,
    marker: str,
) -> bool:
    return (
        _canonical_id(page.get("id")) == _canonical_id(page_id)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and marker in _property_text(page, marker_property, "rich_text")
    )


def _event_page_matches(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    marker: str,
    title: str,
    content: str,
    location: str,
    page_uuid: str | None,
) -> bool:
    if not _page_has_marker(
        page,
        page_id=page_id,
        database_id=database_id,
        marker_property="GoogleイベントID",
        marker=marker,
    ):
        return False
    if _page_archived(page):
        return False
    if _property_text(page, "イベント名", "title") != title:
        return False
    if _property_text(page, "内容", "rich_text") != content:
        return False
    if _property_text(page, "場所", "rich_text") != location:
        return False
    if page_uuid is not None:
        return _canonical_id(_property_text(page, "ページID", "rich_text")) == (
            _canonical_id(page_uuid)
        )
    return True


def _qa_page_matches(
    page: dict,
    *,
    page_id: str,
    database_id: str,
    marker: str,
    question: str,
    answer: str,
    number: int,
) -> bool:
    return (
        _page_has_marker(
            page,
            page_id=page_id,
            database_id=database_id,
            marker_property="回答",
            marker=marker,
        )
        and not _page_archived(page)
        and _property_text(page, "質問", "title") == question
        and _property_text(page, "回答", "rich_text") == answer
        and _property_number(page, "質問番号") == number
    )


def _event_create_payload(database_id: str, run_id: str) -> tuple[dict, dict]:
    marker = _run_marker(run_id)
    start = (datetime.now(_JST) + timedelta(days=7)).replace(
        second=0,
        microsecond=0,
    )
    end = start + timedelta(minutes=30)
    expected = {
        "title": f"[E2E] Notion event CRUD {run_id}",
        "content": f"IE Event Bot E2E fixture; safe to archive. {marker}",
        "location": "E2E initial location",
    }
    payload = {
        "parent": {"database_id": database_id},
        "properties": {
            "イベント名": _title(expected["title"]),
            "内容": _rich_text(expected["content"]),
            "日時": {
                "date": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                }
            },
            "場所": _rich_text(expected["location"]),
            "メッセージID": _rich_text(""),
            "作成者ID": _rich_text("ie-event-bot-e2e"),
            "ページID": _rich_text(""),
            "イベントURL": {"url": "https://example.invalid/ie-event-bot-e2e"},
            "GoogleイベントID": _rich_text(marker),
        },
    }
    return payload, expected


def _event_update_payload(run_id: str, page_id: str) -> tuple[dict, dict]:
    marker = _run_marker(run_id)
    expected = {
        "title": f"[E2E] Notion event CRUD updated {run_id}",
        "content": f"IE Event Bot E2E fixture updated; safe to archive. {marker}",
        "location": "E2E updated location",
    }
    return (
        {
            "properties": {
                "イベント名": _title(expected["title"]),
                "内容": _rich_text(expected["content"]),
                "場所": _rich_text(expected["location"]),
                "ページID": _rich_text(page_id),
            }
        },
        expected,
    )


def _qa_create_payload(database_id: str, run_id: str) -> tuple[dict, dict]:
    marker = _run_marker(run_id)
    expected = {
        "question": f"[E2E] Notion Q&A CRUD {run_id}",
        "answer": f"E2E initial answer. {marker}",
        "number": 1,
    }
    return (
        {
            "parent": {"database_id": database_id},
            "properties": {
                "質問": _title(expected["question"]),
                "回答": _rich_text(expected["answer"]),
                "質問番号": {"number": expected["number"]},
            },
        },
        expected,
    )


def _qa_update_payload(run_id: str) -> tuple[dict, dict]:
    marker = _run_marker(run_id)
    expected = {
        "question": f"[E2E] Notion Q&A CRUD updated {run_id}",
        "answer": f"E2E updated answer. {marker}",
        "number": 2,
    }
    return (
        {
            "properties": {
                "質問": _title(expected["question"]),
                "回答": _rich_text(expected["answer"]),
                "質問番号": {"number": expected["number"]},
            }
        },
        expected,
    )


async def _find_page_by_marker(
    env,
    database_id: str,
    marker_property: str,
    marker: str,
    stages: dict[str, int],
    retries: dict[str, int],
    stage_key: str,
    error_prefix: str,
) -> tuple[str, str]:
    status, data = await _request_stage(
        env,
        stages,
        retries,
        stage_key,
        "POST",
        f"/databases/{quote(database_id, safe='')}/query",
        {
            "filter": {
                "property": marker_property,
                "rich_text": {"contains": marker},
            },
            "page_size": 10,
        },
    )
    if not (200 <= status < 300) or not isinstance(data, dict):
        return "", f"{error_prefix}_reconcile_failed_{status}"
    results = data.get("results")
    if not isinstance(results, list):
        return "", f"{error_prefix}_reconcile_invalid"
    matches = [
        page
        for page in results
        if isinstance(page, dict)
        and _canonical_id(_page_database_id(page)) == _canonical_id(database_id)
        and marker in _property_text(page, marker_property, "rich_text")
        and not _page_archived(page)
    ]
    if len(matches) > 1:
        return "", f"{error_prefix}_reconcile_ambiguous"
    if not matches:
        return "", ""
    page_id = str(matches[0].get("id") or "")
    if not _canonical_id(page_id):
        return "", f"{error_prefix}_reconcile_invalid"
    return page_id, ""


async def _archive_page(
    env,
    database_id: str,
    page_id: str,
    marker_property: str,
    marker: str,
    stages: dict[str, int],
    retries: dict[str, int],
    stage_prefix: str,
) -> tuple[bool, int, int, str]:
    path = f"/pages/{quote(page_id, safe='')}"
    last_status = 0
    for attempt in range(1, _CLEANUP_MAX_ATTEMPTS + 1):
        status, page = await _request_stage(
            env,
            stages,
            retries,
            f"{stage_prefix}_cleanup_verify",
            "GET",
            path,
        )
        last_status = status
        if status == 404:
            return True, attempt, status, ""
        if not (200 <= status < 300) or not isinstance(page, dict):
            continue
        if not _page_has_marker(
            page,
            page_id=page_id,
            database_id=database_id,
            marker_property=marker_property,
            marker=marker,
        ):
            return False, attempt, status, "cleanup_target_mismatch"
        if _page_archived(page):
            return True, attempt, status, ""

        status, archived = await _request_stage(
            env,
            stages,
            retries,
            f"{stage_prefix}_archive",
            "PATCH",
            path,
            {"archived": True},
        )
        last_status = status
        if (
            200 <= status < 300
            and isinstance(archived, dict)
            and _page_has_marker(
                archived,
                page_id=page_id,
                database_id=database_id,
                marker_property=marker_property,
                marker=marker,
            )
            and _page_archived(archived)
        ):
            return True, attempt, status, ""
    return False, _CLEANUP_MAX_ATTEMPTS, last_status, f"{stage_prefix}_archive_failed"


async def _cleanup_resources(env, manifest: dict) -> dict:
    event_database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    qa_database_id = _env_text(env, "NOTION_QA_ID")
    run_id = str(manifest.get("run_id") or "")
    marker = _run_marker(run_id)
    event_page_id = str(manifest.get("event_page_id") or "")
    qa_page_id = str(manifest.get("qa_page_id") or "")
    stages: dict[str, int] = {}
    retries: dict[str, int] = {}
    create_attempted = manifest.get("create_attempted")
    if not isinstance(create_attempted, dict):
        create_attempted = {}

    event_resolve_error = ""
    if not event_page_id:
        event_page_id, event_resolve_error = await _find_page_by_marker(
            env,
            event_database_id,
            "GoogleイベントID",
            marker,
            stages,
            retries,
            "event_cleanup_search",
            "notion_event",
        )
        if (
            not event_resolve_error
            and not event_page_id
            and create_attempted.get("event") is True
        ):
            event_resolve_error = "notion_event_ownership_unresolved"

    qa_resolve_error = ""
    if not qa_page_id:
        qa_page_id, qa_resolve_error = await _find_page_by_marker(
            env,
            qa_database_id,
            "回答",
            marker,
            stages,
            retries,
            "qa_cleanup_search",
            "notion_qa",
        )
        if (
            not qa_resolve_error
            and not qa_page_id
            and create_attempted.get("qa") is True
        ):
            qa_resolve_error = "notion_qa_ownership_unresolved"

    qa_ok = not qa_resolve_error
    qa_attempts = 1
    qa_error = qa_resolve_error
    if qa_ok and qa_page_id:
        qa_ok, qa_attempts, _, qa_error = await _archive_page(
            env,
            qa_database_id,
            qa_page_id,
            "回答",
            marker,
            stages,
            retries,
            "qa",
        )

    event_ok = not event_resolve_error
    event_attempts = 1
    event_error = event_resolve_error
    if event_ok and event_page_id:
        event_ok, event_attempts, _, event_error = await _archive_page(
            env,
            event_database_id,
            event_page_id,
            "GoogleイベントID",
            marker,
            stages,
            retries,
            "event",
        )

    return {
        "ok": event_ok and qa_ok,
        "attempts": max(event_attempts, qa_attempts),
        "stages": stages,
        "rate_limit_retries": retries,
        "error": qa_error or event_error,
        "event_page_id": event_page_id,
        "qa_page_id": qa_page_id,
    }


def _target_fingerprints(event_database_id: str, qa_database_id: str) -> dict[str, str]:
    return {
        "event_database_id_sha256": _fingerprint(_canonical_id(event_database_id)),
        "qa_database_id_sha256": _fingerprint(_canonical_id(qa_database_id)),
    }


def _clean_manifest(
    run_id: str,
    *,
    outcome: str,
    cleanup_attempts: int,
    stages: dict[str, int],
    event_database_id: str,
    qa_database_id: str,
    event_page_id: str,
    qa_page_id: str,
    started_at: str | None,
) -> dict:
    fingerprints = _target_fingerprints(event_database_id, qa_database_id)
    if event_page_id:
        fingerprints["event_page_id_sha256"] = _fingerprint(event_page_id)
    if qa_page_id:
        fingerprints["qa_page_id_sha256"] = _fingerprint(qa_page_id)
    return {
        "version": 1,
        "kind": "notion_pages",
        "dirty": False,
        "last_run_id": run_id,
        "outcome": outcome,
        "cleanup_attempts": cleanup_attempts,
        "stages": dict(stages),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "resource_fingerprints": fingerprints,
    }


async def run_notion_crud_probe(
    env,
    state,
    run_id: str | None = None,
) -> dict:
    """専用Notion DBでpage CRUDを確認し、直後にarchiveする。"""
    if not state.enabled():
        return {"ok": False, "dirty": False, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": False, "error": "sync_coordinator_required"}
    if await state.get_legacy_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE) is not None:
        return {
            "ok": False,
            "dirty": True,
            "error": "legacy_e2e_manifest_review_required",
        }

    current_manifest = await state.get_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE)
    if isinstance(current_manifest, dict) and current_manifest.get("dirty") is True:
        return {
            "ok": False,
            "dirty": True,
            "error": "environment_dirty",
            "cleanup_required": True,
        }

    token = _env_text(env, "NOTION_TOKEN")
    event_database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    qa_database_id = _env_text(env, "NOTION_QA_ID")
    if not token:
        return {"ok": False, "dirty": False, "error": "missing_notion_token"}
    if not event_database_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "missing_notion_event_database_id",
        }
    if not qa_database_id:
        return {
            "ok": False,
            "dirty": False,
            "error": "missing_notion_qa_database_id",
        }

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
    marker = _run_marker(run_id)
    manifest = {
        "version": 1,
        "kind": "notion_pages",
        "dirty": True,
        "run_id": run_id,
        "target_fingerprints": _target_fingerprints(
            event_database_id,
            qa_database_id,
        ),
        "stage": "planned",
        "create_attempted": {"event": False, "qa": False},
        "stages": dict(stages),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)

    error = ""
    event_page_id = ""
    event_payload, event_expected = _event_create_payload(event_database_id, run_id)
    manifest["create_attempted"]["event"] = True
    manifest["stage"] = "event_create_started"
    await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)
    status, created_event = await _request_stage(
        env,
        stages,
        retries,
        "event_create",
        "POST",
        "/pages",
        event_payload,
    )
    if 200 <= status < 300 and isinstance(created_event, dict):
        candidate_id = str(created_event.get("id") or "")
        if candidate_id and _event_page_matches(
            created_event,
            page_id=candidate_id,
            database_id=event_database_id,
            marker=marker,
            title=str(event_expected["title"]),
            content=str(event_expected["content"]),
            location=str(event_expected["location"]),
            page_uuid=None,
        ):
            event_page_id = candidate_id
    if not event_page_id:
        event_page_id, reconcile_error = await _find_page_by_marker(
            env,
            event_database_id,
            "GoogleイベントID",
            marker,
            stages,
            retries,
            "event_create_reconcile",
            "notion_event",
        )
        if reconcile_error:
            error = reconcile_error
        elif not event_page_id:
            error = f"notion_event_create_failed_{status}"
    if event_page_id:
        manifest["event_page_id"] = event_page_id
        manifest["stage"] = "event_created"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)

    event_path = f"/pages/{quote(event_page_id, safe='')}"
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
            or not _event_page_matches(
                read_event,
                page_id=event_page_id,
                database_id=event_database_id,
                marker=marker,
                title=str(event_expected["title"]),
                content=str(event_expected["content"]),
                location=str(event_expected["location"]),
                page_uuid=None,
            )
        ):
            error = f"notion_event_read_verification_failed_{status}"

    event_update, event_updated_expected = _event_update_payload(
        run_id,
        event_page_id,
    )
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
            or not _event_page_matches(
                updated_event,
                page_id=event_page_id,
                database_id=event_database_id,
                marker=marker,
                title=str(event_updated_expected["title"]),
                content=str(event_updated_expected["content"]),
                location=str(event_updated_expected["location"]),
                page_uuid=event_page_id,
            )
        ):
            error = f"notion_event_update_verification_failed_{status}"

    if not error:
        status, updated_event_read = await _request_stage(
            env,
            stages,
            retries,
            "event_read_updated",
            "GET",
            event_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(updated_event_read, dict)
            or not _event_page_matches(
                updated_event_read,
                page_id=event_page_id,
                database_id=event_database_id,
                marker=marker,
                title=str(event_updated_expected["title"]),
                content=str(event_updated_expected["content"]),
                location=str(event_updated_expected["location"]),
                page_uuid=event_page_id,
            )
        ):
            error = f"notion_event_updated_read_verification_failed_{status}"

    qa_page_id = ""
    qa_payload, qa_expected = _qa_create_payload(qa_database_id, run_id)
    if not error:
        manifest["create_attempted"]["qa"] = True
        manifest["stage"] = "qa_create_started"
        manifest["stages"] = dict(stages)
        await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)
        status, created_qa = await _request_stage(
            env,
            stages,
            retries,
            "qa_create",
            "POST",
            "/pages",
            qa_payload,
        )
        if 200 <= status < 300 and isinstance(created_qa, dict):
            candidate_id = str(created_qa.get("id") or "")
            if candidate_id and _qa_page_matches(
                created_qa,
                page_id=candidate_id,
                database_id=qa_database_id,
                marker=marker,
                question=str(qa_expected["question"]),
                answer=str(qa_expected["answer"]),
                number=int(qa_expected["number"]),
            ):
                qa_page_id = candidate_id
        if not qa_page_id:
            qa_page_id, reconcile_error = await _find_page_by_marker(
                env,
                qa_database_id,
                "回答",
                marker,
                stages,
                retries,
                "qa_create_reconcile",
                "notion_qa",
            )
            if reconcile_error:
                error = reconcile_error
            elif not qa_page_id:
                error = f"notion_qa_create_failed_{status}"
        if qa_page_id:
            manifest["qa_page_id"] = qa_page_id
            manifest["stage"] = "qa_created"
            manifest["stages"] = dict(stages)
            await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)

    qa_path = f"/pages/{quote(qa_page_id, safe='')}"
    if not error:
        status, read_qa = await _request_stage(
            env,
            stages,
            retries,
            "qa_read",
            "GET",
            qa_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(read_qa, dict)
            or not _qa_page_matches(
                read_qa,
                page_id=qa_page_id,
                database_id=qa_database_id,
                marker=marker,
                question=str(qa_expected["question"]),
                answer=str(qa_expected["answer"]),
                number=int(qa_expected["number"]),
            )
        ):
            error = f"notion_qa_read_verification_failed_{status}"

    qa_update, qa_updated_expected = _qa_update_payload(run_id)
    if not error:
        status, updated_qa = await _request_stage(
            env,
            stages,
            retries,
            "qa_update",
            "PATCH",
            qa_path,
            qa_update,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(updated_qa, dict)
            or not _qa_page_matches(
                updated_qa,
                page_id=qa_page_id,
                database_id=qa_database_id,
                marker=marker,
                question=str(qa_updated_expected["question"]),
                answer=str(qa_updated_expected["answer"]),
                number=int(qa_updated_expected["number"]),
            )
        ):
            error = f"notion_qa_update_verification_failed_{status}"

    if not error:
        status, updated_qa_read = await _request_stage(
            env,
            stages,
            retries,
            "qa_read_updated",
            "GET",
            qa_path,
        )
        if (
            not (200 <= status < 300)
            or not isinstance(updated_qa_read, dict)
            or not _qa_page_matches(
                updated_qa_read,
                page_id=qa_page_id,
                database_id=qa_database_id,
                marker=marker,
                question=str(qa_updated_expected["question"]),
                answer=str(qa_updated_expected["answer"]),
                number=int(qa_updated_expected["number"]),
            )
        ):
            error = f"notion_qa_updated_read_verification_failed_{status}"

    operation_ok = not error
    manifest["stages"] = dict(stages)
    cleanup = await _cleanup_resources(env, manifest)
    stages.update(cleanup["stages"])
    for key, value in cleanup["rate_limit_retries"].items():
        retries[key] = retries.get(key, 0) + value
    cleanup_ok = bool(cleanup["ok"])
    cleanup_attempts = int(cleanup["attempts"])
    event_page_id = str(cleanup.get("event_page_id") or event_page_id)
    qa_page_id = str(cleanup.get("qa_page_id") or qa_page_id)

    if cleanup_ok:
        await state.put_e2e_manifest(
            NOTION_CRUD_MANIFEST_SERVICE,
            _clean_manifest(
                run_id,
                outcome="passed" if operation_ok else "failed_clean",
                cleanup_attempts=cleanup_attempts,
                stages=stages,
                event_database_id=event_database_id,
                qa_database_id=qa_database_id,
                event_page_id=event_page_id,
                qa_page_id=qa_page_id,
                started_at=str(manifest.get("created_at") or "") or None,
            ),
        )
    else:
        manifest["stage"] = "cleanup_failed"
        manifest["failure"] = str(cleanup.get("error") or "") or error
        manifest["cleanup_attempts"] = cleanup_attempts
        manifest["stages"] = dict(stages)
        if event_page_id:
            manifest["event_page_id"] = event_page_id
        if qa_page_id:
            manifest["qa_page_id"] = qa_page_id
        await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)

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
        result["error"] = str(cleanup.get("error") or "notion_cleanup_failed")
    elif error:
        result["error"] = error
    return result


async def cleanup_notion_crud_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """本人確認後にdirty manifestのNotion page archiveを再実行する。"""
    if not state.enabled():
        return {"ok": False, "dirty": True, "error": "state_kv_required"}
    if not state.e2e_manifest_enabled():
        return {"ok": False, "dirty": True, "error": "sync_coordinator_required"}
    if await state.get_legacy_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE) is not None:
        return {
            "ok": False,
            "dirty": True,
            "error": "legacy_e2e_manifest_review_required",
        }
    expected_run_id = str(expected_run_id or "").strip()
    manifest = await state.get_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE)
    if not isinstance(manifest, dict) or manifest.get("dirty") is not True:
        last_run_id = str((manifest or {}).get("last_run_id") or "")
        if expected_run_id and last_run_id and last_run_id != expected_run_id:
            return {"ok": False, "dirty": False, "error": "cleanup_run_id_mismatch"}
        return {"ok": True, "dirty": False, "action": "noop_clean"}
    if manifest.get("kind") != "notion_pages":
        return {"ok": False, "dirty": True, "error": "invalid_dirty_manifest"}

    token = _env_text(env, "NOTION_TOKEN")
    event_database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    qa_database_id = _env_text(env, "NOTION_QA_ID")
    run_id = str(manifest.get("run_id") or "")
    if expected_run_id and run_id != expected_run_id:
        return {"ok": False, "dirty": True, "error": "cleanup_run_id_mismatch"}
    fingerprints = manifest.get("target_fingerprints")
    if not token:
        return {"ok": False, "dirty": True, "error": "missing_notion_token"}
    if (
        not _canonical_id(event_database_id)
        or not _canonical_id(qa_database_id)
        or not run_id
        or not isinstance(fingerprints, dict)
        or fingerprints
        != _target_fingerprints(event_database_id, qa_database_id)
    ):
        return {"ok": False, "dirty": True, "error": "dirty_manifest_target_mismatch"}

    cleanup = await _cleanup_resources(env, manifest)
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    stages.update(cleanup["stages"])
    attempts = int(cleanup["attempts"])
    event_page_id = str(cleanup.get("event_page_id") or "")
    qa_page_id = str(cleanup.get("qa_page_id") or "")
    if not cleanup["ok"]:
        manifest["stage"] = "cleanup_failed"
        manifest["cleanup_attempts"] = attempts
        manifest["failure"] = str(cleanup.get("error") or "notion_cleanup_failed")
        manifest["stages"] = stages
        if event_page_id:
            manifest["event_page_id"] = event_page_id
        if qa_page_id:
            manifest["qa_page_id"] = qa_page_id
        await state.put_e2e_manifest(NOTION_CRUD_MANIFEST_SERVICE, manifest)
        return {
            "ok": False,
            "dirty": True,
            "action": "cleanup",
            "error": str(cleanup.get("error") or "notion_cleanup_failed"),
            "cleanup": {"ok": False, "attempts": attempts},
        }

    await state.put_e2e_manifest(
        NOTION_CRUD_MANIFEST_SERVICE,
        _clean_manifest(
            run_id,
            outcome="recovered",
            cleanup_attempts=attempts,
            stages=stages,
            event_database_id=event_database_id,
            qa_database_id=qa_database_id,
            event_page_id=event_page_id,
            qa_page_id=qa_page_id,
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
