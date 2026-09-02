"""Googleの実exists通知から所有eventだけをNotionへ反映するE2E。"""

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from workers import Response

from e2e_google_notion_probe import (
    _EphemeralApplyState,
    cleanup_google_notion_scenario,
    run_google_notion_scenario,
)
from e2e_google_probe import _event_item_url, _event_matches, _google_request
from e2e_google_webhook_delivery_probe import (
    _google_request as _google_watch_request,
)
from e2e_google_webhook_delivery_probe import (
    _stop_watch,
    _valid_webhook_url,
    _watch_url,
)
from e2e_webhook_probe import (
    _EphemeralManifestState,
    _RunScopedSyncState,
    _owned_event,
)
from google_apply_sync import apply_google_events
from google_auth import get_google_access_token


GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE = "webhook_change"
GOOGLE_WEBHOOK_CHANGE_MANIFEST_KIND = "google_webhook_change_dispatch"

_WATCH_TTL_SECONDS = 600
_SYNC_POLL_ATTEMPTS = 20
_DISPATCH_POLL_ATTEMPTS = 20
_POLL_DELAY_SECONDS = 1


def _env_text(env, key: str) -> str:
    value = getattr(env, key, None)
    return str(value or "").strip()


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _channel_id(run_id: str) -> str:
    return f"e2e-change-{_fingerprint(run_id)[:40]}"


def _header(request, name: str) -> str:
    value = request.headers.get(name)
    return str(value or "").strip()


def _watch_fingerprints(
    channel_id: str,
    resource_id: str,
    message_number: str,
) -> dict[str, str]:
    fingerprints = {
        "watch_channel_id_sha256": _fingerprint(channel_id),
    }
    if resource_id:
        fingerprints["watch_resource_id_sha256"] = _fingerprint(resource_id)
    if message_number:
        fingerprints["webhook_message_number_sha256"] = _fingerprint(message_number)
    return fingerprints


def _owned_watch(
    manifest: dict | None,
    *,
    env,
    expected_run_id: str,
) -> tuple[bool, str, str, str]:
    if not isinstance(manifest, dict):
        return False, "", "", ""
    watch = manifest.get("watch")
    if not isinstance(watch, dict):
        return False, "", "", ""
    calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
    webhook_url = _env_text(env, "GCAL_WEBHOOK_URL")
    channel_id = str(watch.get("channel_id") or "").strip()
    resource_id = str(watch.get("resource_id") or "").strip()
    if not resource_id:
        for key in ("sync_notification", "change_notification"):
            notification = manifest.get(key)
            if isinstance(notification, dict):
                resource_id = str(notification.get("resource_id") or "").strip()
                if resource_id:
                    break
    notification = manifest.get("change_notification")
    message_number = (
        str(notification.get("message_number") or "").strip()
        if isinstance(notification, dict)
        else ""
    )
    valid = bool(
        manifest.get("kind") == GOOGLE_WEBHOOK_CHANGE_MANIFEST_KIND
        and manifest.get("dirty") is True
        and str(manifest.get("run_id") or "") == expected_run_id
        and bool(calendar_id)
        and _valid_webhook_url(webhook_url)
        and channel_id
        and len(channel_id) <= 64
        and str(watch.get("calendar_id_sha256") or "")
        == _fingerprint(calendar_id)
        and str(watch.get("webhook_url_sha256") or "")
        == _fingerprint(webhook_url)
        and str(watch.get("channel_id_sha256") or "")
        == _fingerprint(channel_id)
    )
    return valid, channel_id, resource_id, message_number


def _sync_received(manifest: dict | None, resource_id: str) -> bool:
    notification = (
        manifest.get("sync_notification") if isinstance(manifest, dict) else None
    )
    return bool(
        isinstance(notification, dict)
        and str(notification.get("resource_id") or "") == resource_id
        and str(notification.get("message_number") or "") == "1"
    )


async def _wait_for_sync(state, resource_id: str) -> dict | None:
    for attempt in range(_SYNC_POLL_ATTEMPTS):
        manifest = await state.get_e2e_manifest(
            GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE
        )
        if _sync_received(manifest, resource_id):
            return manifest
        if attempt + 1 < _SYNC_POLL_ATTEMPTS:
            await asyncio.sleep(_POLL_DELAY_SECONDS)
    return None


async def _wait_for_dispatch(state) -> dict | None:
    for attempt in range(_DISPATCH_POLL_ATTEMPTS):
        manifest = await state.get_e2e_manifest(
            GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE
        )
        if isinstance(manifest, dict) and isinstance(manifest.get("dispatch"), dict):
            return manifest
        if attempt + 1 < _DISPATCH_POLL_ATTEMPTS:
            await asyncio.sleep(_POLL_DELAY_SECONDS)
    return None


def _event_update_payload(event: dict, run_id: str) -> dict:
    """PUT用に書込み可能な既知fieldだけを抽出し、変更markerを付ける。"""
    private = dict(((event.get("extendedProperties") or {}).get("private") or {}))
    private["ie_event_bot_e2e_run"] = run_id
    private["ie_event_bot_e2e_change"] = "exists"
    payload = {
        "summary": str(event.get("summary") or ""),
        "description": str(event.get("description") or ""),
        "location": str(event.get("location") or ""),
        "start": dict(event.get("start") or {}),
        "end": dict(event.get("end") or {}),
        "extendedProperties": {"private": private},
        "reminders": dict(event.get("reminders") or {"useDefault": False}),
        "transparency": str(event.get("transparency") or "transparent"),
        "visibility": str(event.get("visibility") or "private"),
    }
    return payload


def _dispatch_apply_result(manifest: dict | None) -> dict:
    dispatch = manifest.get("dispatch") if isinstance(manifest, dict) else None
    if not isinstance(dispatch, dict):
        return {
            "ok": False,
            "processed": 0,
            "pending_events": 0,
            "error_count": 1,
            "_notion_write_started": False,
        }
    isolated = all(
        dispatch.get(key) is True
        for key in (
            "cursor_written",
            "last_epoch_written",
            "last_result_written",
        )
    )
    ok = bool(
        dispatch.get("status") == 204
        and dispatch.get("selected_count") == 1
        and dispatch.get("dedupe_calls") == 1
        and dispatch.get("apply_ok") is True
        and dispatch.get("processed") == 1
        and dispatch.get("pending_events") == 0
        and dispatch.get("error_count") == 0
        and isolated
    )
    return {
        "ok": ok,
        "processed": int(dispatch.get("processed") or 0),
        "pending_events": int(dispatch.get("pending_events") or 0),
        "error_count": int(dispatch.get("error_count") or (0 if ok else 1)),
        "_notion_write_started": dispatch.get("notion_write_started") is True,
    }


async def _cleanup_watch_and_dedupe(env, state, manifest: dict) -> dict:
    run_id = str(manifest.get("run_id") or "")
    owned, channel_id, resource_id, message_number = _owned_watch(
        manifest,
        env=env,
        expected_run_id=run_id,
    )
    if not owned:
        return {
            "ok": False,
            "attempts": 1,
            "stages": {"watch_stop": 409},
            "error": "webhook_change_target_mismatch",
        }

    attempted = manifest.get("create_attempted")
    attempted = attempted if isinstance(attempted, dict) else {}
    stages: dict[str, int] = {}
    if attempted.get("watch_channel") is True:
        if not resource_id:
            return {
                "ok": False,
                "attempts": 1,
                "stages": {"watch_stop": 409},
                "error": "google_watch_ownership_unresolved",
            }
        try:
            bearer_token = await get_google_access_token(env, _EphemeralManifestState(state))
        except Exception:
            bearer_token = None
        if not bearer_token:
            return {
                "ok": False,
                "attempts": 1,
                "stages": {"watch_stop": 0},
                "error": "missing_google_access_token",
            }
        stopped, stop_status = await _stop_watch(
            str(bearer_token),
            channel_id,
            resource_id,
        )
        stages["watch_stop"] = stop_status
        if not stopped:
            return {
                "ok": False,
                "attempts": 1,
                "stages": stages,
                "error": "google_watch_stop_failed",
            }

    if attempted.get("webhook_dedupe") is True:
        if not message_number:
            return {
                "ok": False,
                "attempts": 1,
                "stages": {**stages, "webhook_dedupe_delete": 409},
                "error": "webhook_dedupe_target_mismatch",
            }
        try:
            await state.clear_e2e_google_message_seen(
                channel_id,
                message_number,
                run_id,
            )
        except Exception:
            return {
                "ok": False,
                "attempts": 1,
                "stages": {**stages, "webhook_dedupe_delete": 500},
                "error": "webhook_dedupe_cleanup_failed",
            }
        stages["webhook_dedupe_delete"] = 200

    return {
        "ok": True,
        "attempts": 1,
        "stages": stages,
        "error": "",
        "resource_fingerprints": _watch_fingerprints(
            channel_id,
            resource_id,
            message_number,
        ),
    }


async def run_google_webhook_change_probe(
    env,
    state,
    run_id: str,
) -> dict:
    """実watchのexists通知で共通dispatchを起動し、所有eventだけを回収する。"""
    webhook_url = _env_text(env, "GCAL_WEBHOOK_URL")
    channel_token = _env_text(env, "GCAL_WEBHOOK_TOKEN")
    if not _valid_webhook_url(webhook_url):
        return {"ok": False, "dirty": False, "error": "invalid_gcal_webhook_url"}
    if not channel_token:
        return {"ok": False, "dirty": False, "error": "missing_gcal_webhook_token"}
    if len(channel_token) > 256:
        return {"ok": False, "dirty": False, "error": "gcal_webhook_token_too_long"}
    if not state.is_gcal_dedupe_enabled(env):
        return {
            "ok": False,
            "dirty": False,
            "error": "google_message_dedupe_must_be_enabled",
        }

    manifest_state = _EphemeralManifestState(state)

    async def apply_via_exists(events: list[dict], stages: dict[str, int]) -> dict:
        if len(events) != 1 or not isinstance(events[0], dict):
            stages["webhook_fixture"] = 500
            return {"ok": False, "_notion_write_started": False}
        event = events[0]
        event_id = str(event.get("id") or "")
        if not _owned_event(event, event_id, run_id):
            stages["webhook_fixture"] = 500
            return {"ok": False, "_notion_write_started": False}
        stages["webhook_fixture"] = 200

        calendar_id = _env_text(env, "GOOGLE_CALENDAR_ID")
        channel_id = _channel_id(run_id)
        manifest = await manifest_state.get_e2e_manifest(
            GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE
        )
        if not (
            isinstance(manifest, dict)
            and manifest.get("dirty") is True
            and str(manifest.get("run_id") or "") == run_id
            and str(manifest.get("google_event_id") or "") == event_id
        ):
            return {"ok": False, "_notion_write_started": False}
        attempted = manifest.get("create_attempted")
        if not isinstance(attempted, dict):
            attempted = {}
            manifest["create_attempted"] = attempted
        attempted["watch_channel"] = True
        manifest["watch"] = {
            "channel_id": channel_id,
            "calendar_id_sha256": _fingerprint(calendar_id),
            "webhook_url_sha256": _fingerprint(webhook_url),
            "channel_id_sha256": _fingerprint(channel_id),
        }
        manifest["stage"] = "watch_create_started"
        manifest["stages"] = dict(stages)
        await manifest_state.put_e2e_manifest(
            GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE,
            manifest,
        )

        try:
            bearer_token = await get_google_access_token(env, manifest_state)
        except Exception:
            bearer_token = None
        if not bearer_token:
            return {"ok": False, "_notion_write_started": False}
        watch_status, watch_data = await _google_watch_request(
            "POST",
            _watch_url(calendar_id),
            str(bearer_token),
            {
                "id": channel_id,
                "type": "web_hook",
                "address": webhook_url,
                "token": channel_token,
                "params": {"ttl": str(_WATCH_TTL_SECONDS)},
            },
        )
        response_channel_id = str(watch_data.get("id") or "").strip()
        resource_id = str(watch_data.get("resourceId") or "").strip()
        expiration = str(watch_data.get("expiration") or "").strip()
        stages["watch_create"] = watch_status

        if 400 <= watch_status < 500:
            latest = await manifest_state.get_e2e_manifest(
                GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE
            )
            if isinstance(latest, dict):
                latest_attempted = latest.get("create_attempted")
                if isinstance(latest_attempted, dict):
                    latest_attempted["watch_channel"] = False
                latest["stages"] = dict(stages)
                latest["stage"] = "watch_create_rejected"
                await manifest_state.put_e2e_manifest(
                    GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE,
                    latest,
                )
            return {"ok": False, "_notion_write_started": False}

        if (
            200 <= watch_status < 300
            and response_channel_id == channel_id
            and resource_id
        ):
            try:
                await state.attach_e2e_webhook_change_watch(
                    run_id=run_id,
                    channel_id=channel_id,
                    resource_id=resource_id,
                    expiration=expiration,
                    watch_status=watch_status,
                )
            except Exception:
                pass
        latest = await manifest_state.get_e2e_manifest(
            GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE
        )
        owned, _, owned_resource_id, _ = _owned_watch(
            latest,
            env=env,
            expected_run_id=run_id,
        )
        if owned and owned_resource_id:
            resource_id = owned_resource_id
        if not (
            200 <= watch_status < 300
            and response_channel_id == channel_id
            and resource_id
            and await _wait_for_sync(manifest_state, resource_id) is not None
        ):
            return {"ok": False, "_notion_write_started": False}

        updated_min = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        await state.prepare_e2e_webhook_change(
            run_id=run_id,
            updated_min=updated_min,
        )
        update_status, updated = await _google_request(
            "PUT",
            f"{_event_item_url(calendar_id, event_id)}?sendUpdates=none",
            str(bearer_token),
            _event_update_payload(event, run_id),
        )
        await state.record_e2e_webhook_change_update(
            run_id=run_id,
            update_status=update_status,
        )
        if not (200 <= update_status < 300) or not _event_matches(
            updated,
            event_id=event_id,
            run_id=run_id,
            summary=str(event.get("summary") or ""),
        ):
            return {"ok": False, "_notion_write_started": False}

        completed = await _wait_for_dispatch(manifest_state)
        return _dispatch_apply_result(completed)

    return await run_google_notion_scenario(
        env,
        manifest_state,
        run_id,
        manifest_service=GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE,
        manifest_kind=GOOGLE_WEBHOOK_CHANGE_MANIFEST_KIND,
        apply_runner=apply_via_exists,
        apply_error="webhook_change_dispatch_failed",
        pre_cleanup_runner=lambda manifest: _cleanup_watch_and_dedupe(
            env,
            state,
            manifest,
        ),
    )


async def cleanup_google_webhook_change_probe(
    env,
    state,
    expected_run_id: str,
) -> dict:
    """run所有watchを先に停止し、dedupeとGoogle/Notion資源を回収する。"""
    manifest_state = _EphemeralManifestState(state)
    return await cleanup_google_notion_scenario(
        env,
        manifest_state,
        expected_run_id,
        manifest_service=GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE,
        manifest_kind=GOOGLE_WEBHOOK_CHANGE_MANIFEST_KIND,
        pre_cleanup_runner=lambda manifest: _cleanup_watch_and_dedupe(
            env,
            state,
            manifest,
        ),
    )


def _safe_count(value) -> int:
    return value if type(value) is int and value >= 0 else 0


async def handle_google_webhook_change_callback(
    env,
    state,
    request,
    deliver,
):
    """E2E Workerに届いた所有watchの通知だけを処理する。"""
    channel_id = _header(request, "X-Goog-Channel-ID")
    resource_id = _header(request, "X-Goog-Resource-ID")
    resource_state = _header(request, "X-Goog-Resource-State")
    message_number = _header(request, "X-Goog-Message-Number")

    if resource_state == "sync":
        accepted = await state.record_e2e_webhook_change_sync(
            channel_id=channel_id,
            resource_id=resource_id,
            resource_state=resource_state,
            message_number=message_number,
        )
        return Response("", status=204) if accepted else None
    if resource_state != "exists":
        return None

    claim = await state.claim_e2e_webhook_change(
        channel_id=channel_id,
        resource_id=resource_id,
        resource_state=resource_state,
        message_number=message_number,
    )
    if not claim["accepted"]:
        return None
    if claim["duplicate"]:
        return Response("", status=204)

    manifest = await state.get_e2e_manifest(GOOGLE_WEBHOOK_CHANGE_MANIFEST_SERVICE)
    run_id = str((manifest or {}).get("run_id") or "")
    owned, owned_channel_id, owned_resource_id, owned_message_number = _owned_watch(
        manifest,
        env=env,
        expected_run_id=run_id,
    )
    event_id = str((manifest or {}).get("google_event_id") or "")
    updated_min = str((manifest or {}).get("updated_min") or "")
    valid = bool(
        owned
        and owned_channel_id == channel_id
        and owned_resource_id == resource_id
        and owned_message_number == message_number
        and event_id
        and updated_min
    )

    sync_state = _RunScopedSyncState(
        updated_min,
        state,
        run_id,
        channel_id,
        message_number,
    )
    selected_count = 0
    notion_write_started = False
    apply_result: dict = {}

    async def apply_owned(apply_env, _sync_state, fetched_events):
        nonlocal apply_result, notion_write_started, selected_count
        owned_events = [
            event
            for event in list(fetched_events or [])
            if isinstance(event, dict) and _owned_event(event, event_id, run_id)
        ]
        selected_count = len(owned_events)
        if selected_count != 1:
            apply_result = {
                "ok": False,
                "processed": 0,
                "pending_events": 0,
                "error_count": 1,
            }
            return apply_result
        notion_write_started = True
        apply_result = await apply_google_events(
            apply_env,
            _EphemeralApplyState(),
            owned_events,
        )
        return apply_result

    response = Response("sync failed", status=500)
    if valid:
        try:
            response = await deliver(request, sync_state, apply_owned)
        except Exception:
            response = Response("sync failed", status=500)
    dispatch = {
        "dispatch_status": int(response.status),
        "selected_count": selected_count,
        "dedupe_calls": sync_state.dedupe_calls,
        "apply_ok": apply_result.get("ok") is True,
        "processed": _safe_count(apply_result.get("processed")),
        "pending_events": _safe_count(apply_result.get("pending_events")),
        "error_count": _safe_count(apply_result.get("error_count")),
        "notion_write_started": notion_write_started,
        "cursor_written": sync_state.cursor_written,
        "last_epoch_written": sync_state.last_epoch_written,
        "last_result_written": sync_state.last_result_written,
    }
    try:
        await state.complete_e2e_webhook_change(
            run_id=run_id,
            channel_id=channel_id,
            resource_id=resource_id,
            message_number=message_number,
            dispatch=dispatch,
        )
    except Exception:
        return Response("webhook unavailable", status=503)
    return response
