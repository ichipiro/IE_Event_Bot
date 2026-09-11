"""Google webhook ingressの自己cleanup型E2E simulation。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from e2e_google_notion_probe import (
    _EphemeralApplyState,
    cleanup_google_notion_scenario,
    run_google_notion_scenario,
)
from google_apply_sync import apply_google_events


WEBHOOK_DISPATCH_MANIFEST_SERVICE = "webhook_dispatch"
WEBHOOK_DISPATCH_MANIFEST_KIND = "google_webhook_simulation"


class _EphemeralManifestState:
    """manifestだけを永続化し、Google認証cacheは実行内に閉じ込める。"""

    def __init__(self, state):
        self._state = state
        self._text: dict[str, str] = {}

    def enabled(self) -> bool:
        return self._state.enabled()

    def e2e_manifest_enabled(self) -> bool:
        return self._state.e2e_manifest_enabled()

    async def get_text(self, key: str) -> str | None:
        return self._text.get(key)

    async def put_text_if_changed(self, key: str, value: str) -> bool:
        next_value = str(value)
        changed = self._text.get(key) != next_value
        self._text[key] = next_value
        return changed

    async def get_e2e_manifest(self, service: str) -> dict | None:
        return await self._state.get_e2e_manifest(service)

    async def put_e2e_manifest(self, service: str, manifest: dict) -> None:
        await self._state.put_e2e_manifest(service, manifest)


class _RunScopedSyncState:
    """通常KVへ書かず、1回のdispatch内だけで同期状態を保持する。"""

    def __init__(
        self,
        updated_min: str,
        dedupe_state,
        run_id: str,
        channel_id: str,
        message_number: str,
    ):
        self._text = {"sync:updated_min": updated_min}
        self._dedupe_state = dedupe_state
        self._run_id = run_id
        self._channel_id = channel_id
        self._message_number = message_number
        self.dedupe_calls = 0
        self.cursor_written = False
        self.last_epoch_written = False
        self.last_result_written = False

    @staticmethod
    def enabled() -> bool:
        return True

    async def get_text(self, key: str) -> str | None:
        return self._text.get(key)

    async def put_text_if_changed(self, key: str, value: str) -> bool:
        next_value = str(value)
        changed = self._text.get(key) != next_value
        self._text[key] = next_value
        return changed

    async def get_sync_updated_min(self) -> str | None:
        return self._text.get("sync:updated_min")

    async def set_sync_updated_min(self, updated_min: str) -> None:
        if updated_min:
            self._text["sync:updated_min"] = str(updated_min)
            self.cursor_written = True

    async def set_sync_last_epoch_now(self) -> None:
        self.last_epoch_written = True

    async def set_last_result(self, op_name: str, payload: dict) -> None:
        if op_name == "sync_all" and isinstance(payload, dict):
            self.last_result_written = True

    async def mark_google_message_seen(
        self,
        channel_id: str,
        message_number: str,
    ) -> bool:
        if channel_id != self._channel_id or message_number != self._message_number:
            raise RuntimeError("webhook_dedupe_target_mismatch")
        self.dedupe_calls += 1
        return await self._dedupe_state.mark_e2e_google_message_seen(
            channel_id,
            message_number,
            self._run_id,
        )


class _WebhookHeaders:
    """内部simulation request向けの大文字小文字非依存header。"""

    def __init__(self, values: dict[str, str]):
        self._values = {str(key).lower(): str(value) for key, value in values.items()}

    def get(self, name: str):
        return self._values.get(str(name).lower())


class _WebhookRequest:
    """外部公開せず共通Webhook handlerへ渡す最小request。"""

    def __init__(
        self,
        channel_token: str,
        channel_id: str,
        message_number: str,
    ):
        self.url = "https://e2e.invalid/gcal/webhook"
        self.method = "POST"
        self.headers = _WebhookHeaders(
            {
                "X-Goog-Channel-Token": channel_token,
                "X-Goog-Channel-ID": channel_id,
                "X-Goog-Message-Number": message_number,
            }
        )


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"E2E-{timestamp}-{uuid4().hex[:8]}"


def _message_identity(run_id: str) -> tuple[str, str]:
    digest = sha256(run_id.encode("utf-8")).hexdigest()
    channel_id = f"e2e-webhook-{digest[:32]}"
    message_number = str(int(digest[32:48], 16) + 1)
    return channel_id, message_number


def _dedupe_fingerprints(channel_id: str, message_number: str) -> dict[str, str]:
    return {
        "webhook_channel_id_sha256": sha256(channel_id.encode("utf-8")).hexdigest(),
        "webhook_message_number_sha256": sha256(
            message_number.encode("utf-8")
        ).hexdigest(),
    }


def _manifest_dedupe_fingerprints(manifest: dict | None) -> dict[str, str]:
    if not isinstance(manifest, dict):
        return {}
    marker = manifest.get("webhook_dedupe")
    if not isinstance(marker, dict):
        return {}
    channel_id = str(marker.get("channel_id") or "")
    message_number = str(marker.get("message_number") or "")
    if not channel_id or not message_number:
        return {}
    return _dedupe_fingerprints(channel_id, message_number)


async def _cleanup_dedupe_marker(state, manifest: dict) -> dict:
    attempted = manifest.get("create_attempted")
    if not isinstance(attempted, dict) or attempted.get("webhook_dedupe") is not True:
        return {"ok": True, "attempts": 1, "stages": {}, "error": ""}

    run_id = str(manifest.get("run_id") or "")
    marker = manifest.get("webhook_dedupe")
    fingerprints = manifest.get("webhook_dedupe_fingerprints")
    if not isinstance(marker, dict) or not isinstance(fingerprints, dict):
        return {
            "ok": False,
            "attempts": 1,
            "stages": {"webhook_dedupe_delete": 409},
            "error": "webhook_dedupe_target_mismatch",
        }

    channel_id = str(marker.get("channel_id") or "")
    message_number = str(marker.get("message_number") or "")
    owner_run_id = str(marker.get("owner_run_id") or "")
    if (
        not channel_id
        or not message_number
        or owner_run_id != run_id
        or fingerprints != _dedupe_fingerprints(channel_id, message_number)
    ):
        return {
            "ok": False,
            "attempts": 1,
            "stages": {"webhook_dedupe_delete": 409},
            "error": "webhook_dedupe_target_mismatch",
        }

    try:
        await state.clear_e2e_google_message_seen(
            channel_id,
            message_number,
            owner_run_id,
        )
    except Exception:
        return {
            "ok": False,
            "attempts": 1,
            "stages": {"webhook_dedupe_delete": 500},
            "error": "webhook_dedupe_cleanup_failed",
        }
    return {
        "ok": True,
        "attempts": 1,
        "stages": {"webhook_dedupe_delete": 200},
        "error": "",
    }


def _owned_event(event: dict, event_id: str, run_id: str) -> bool:
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return (
        str(event.get("id") or "") == event_id
        and str(private.get("ie_event_bot_e2e_run") or "") == run_id
    )


async def run_webhook_dispatch_probe(
    env,
    state,
    deliver,
    run_id: str | None = None,
) -> dict:
    """共通Webhook ingressから所有Google eventだけを適用して回収する。"""

    if not str(getattr(env, "GCAL_WEBHOOK_TOKEN", "") or "").strip():
        return {"ok": False, "dirty": False, "error": "missing_gcal_webhook_token"}
    if not state.is_gcal_dedupe_enabled(env):
        return {
            "ok": False,
            "dirty": False,
            "error": "google_message_dedupe_must_be_enabled",
        }

    run_id = str(run_id or "").strip() or _new_run_id()
    channel_id, message_number = _message_identity(run_id)
    dedupe_fingerprints = _dedupe_fingerprints(channel_id, message_number)
    manifest_state = _EphemeralManifestState(state)

    async def apply_via_webhook(events: list[dict], stages: dict[str, int]) -> dict:
        if len(events) != 1 or not isinstance(events[0], dict):
            stages["webhook_fixture"] = 500
            return {"ok": False}

        fixture = events[0]
        event_id = str(fixture.get("id") or "")
        private = ((fixture.get("extendedProperties") or {}).get("private") or {})
        expected_run_id = str(private.get("ie_event_bot_e2e_run") or "")
        if expected_run_id != run_id or not _owned_event(
            fixture,
            event_id,
            expected_run_id,
        ):
            stages["webhook_fixture"] = 500
            return {"ok": False}
        stages["webhook_fixture"] = 200

        cursor = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        sync_state = _RunScopedSyncState(
            cursor,
            state,
            expected_run_id,
            channel_id,
            message_number,
        )
        selected_count = 0
        dispatch_count = 0
        notion_write_started = False
        apply_result: dict = {}

        async def apply_owned(apply_env, _sync_state, fetched_events):
            nonlocal apply_result, dispatch_count, notion_write_started, selected_count
            dispatch_count += 1
            owned = [
                event
                for event in list(fetched_events or [])
                if isinstance(event, dict)
                and _owned_event(event, event_id, expected_run_id)
            ]
            selected_count = len(owned)
            if selected_count != 1:
                return {
                    "ok": False,
                    "processed": 0,
                    "pending_events": 0,
                    "error_count": 1,
                }
            notion_write_started = True
            apply_result = await apply_google_events(
                apply_env,
                _EphemeralApplyState(),
                owned,
            )
            return apply_result

        manifest = await manifest_state.get_e2e_manifest(
            WEBHOOK_DISPATCH_MANIFEST_SERVICE
        )
        if (
            not isinstance(manifest, dict)
            or manifest.get("dirty") is not True
            or str(manifest.get("run_id") or "") != expected_run_id
        ):
            stages["webhook_message_dedupe"] = 500
            return {
                "ok": False,
                "processed": 0,
                "pending_events": 0,
                "error_count": 1,
                "_notion_write_started": False,
            }
        attempted = manifest.get("create_attempted")
        if not isinstance(attempted, dict):
            attempted = {}
            manifest["create_attempted"] = attempted
        attempted["webhook_dedupe"] = True
        manifest["webhook_dedupe"] = {
            "channel_id": channel_id,
            "message_number": message_number,
            "owner_run_id": expected_run_id,
        }
        manifest["webhook_dedupe_fingerprints"] = dedupe_fingerprints
        manifest["stage"] = "webhook_ingress_started"
        await manifest_state.put_e2e_manifest(
            WEBHOOK_DISPATCH_MANIFEST_SERVICE,
            manifest,
        )

        try:
            required_token = str(getattr(env, "GCAL_WEBHOOK_TOKEN", "") or "").strip()
            wrong_token = f"{required_token}-invalid"
            rejected = await deliver(
                _WebhookRequest(wrong_token, channel_id, message_number),
                sync_state,
                apply_owned,
            )
            rejected_status = int(rejected.status)
            reject_isolated = sync_state.dedupe_calls == 0 and dispatch_count == 0

            first = await deliver(
                _WebhookRequest(required_token, channel_id, message_number),
                sync_state,
                apply_owned,
            )
            first_status = int(first.status)
            first_dispatch_count = dispatch_count
            duplicate = await deliver(
                _WebhookRequest(required_token, channel_id, message_number),
                sync_state,
                apply_owned,
            )
            duplicate_status = int(duplicate.status)
        except Exception:
            rejected_status = 500
            reject_isolated = False
            first_status = 500
            first_dispatch_count = dispatch_count
            duplicate_status = 500

        stages["webhook_token_reject"] = rejected_status
        stages["webhook_token_reject_isolated"] = 200 if reject_isolated else 500
        stages["webhook_first_delivery"] = first_status
        stages["webhook_duplicate_delivery"] = duplicate_status
        dedupe_ok = (
            first_dispatch_count == 1
            and dispatch_count == 1
            and sync_state.dedupe_calls == 2
        )
        stages["webhook_message_dedupe"] = 200 if dedupe_ok else 500
        dispatch_ok = (
            rejected_status == 401
            and reject_isolated
            and first_status == 204
            and duplicate_status == 204
            and dedupe_ok
            and selected_count == 1
            and apply_result.get("ok") is True
        )
        stages["webhook_delta_fetch"] = 200 if selected_count == 1 else 500
        stages["webhook_dispatch"] = first_status
        stages["webhook_cursor_isolated"] = 200 if sync_state.cursor_written else 500
        stages["webhook_last_epoch_isolated"] = (
            200 if sync_state.last_epoch_written else 500
        )
        stages["webhook_last_result_isolated"] = (
            200 if sync_state.last_result_written else 500
        )
        if not dispatch_ok:
            return {
                "ok": False,
                "processed": 0,
                "pending_events": 0,
                "error_count": 1,
                "_notion_write_started": notion_write_started,
            }
        return {**apply_result, "_notion_write_started": notion_write_started}

    return await run_google_notion_scenario(
        env,
        manifest_state,
        run_id,
        manifest_service=WEBHOOK_DISPATCH_MANIFEST_SERVICE,
        manifest_kind=WEBHOOK_DISPATCH_MANIFEST_KIND,
        apply_runner=apply_via_webhook,
        apply_error="webhook_dispatch_failed",
        cleanup_runner=lambda manifest: _cleanup_dedupe_marker(state, manifest),
        extra_resource_fingerprints=dedupe_fingerprints,
    )


async def cleanup_webhook_dispatch_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """run IDと対象fingerprintが一致する資源・重複状態だけを回収する。"""
    manifest_state = _EphemeralManifestState(state)
    manifest = await manifest_state.get_e2e_manifest(
        WEBHOOK_DISPATCH_MANIFEST_SERVICE
    )
    return await cleanup_google_notion_scenario(
        env,
        manifest_state,
        expected_run_id,
        manifest_service=WEBHOOK_DISPATCH_MANIFEST_SERVICE,
        manifest_kind=WEBHOOK_DISPATCH_MANIFEST_KIND,
        cleanup_runner=lambda dirty_manifest: _cleanup_dedupe_marker(
            state,
            dirty_manifest,
        ),
        extra_resource_fingerprints=_manifest_dedupe_fingerprints(manifest),
    )
