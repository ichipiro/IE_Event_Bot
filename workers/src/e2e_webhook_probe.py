"""Google webhook dispatchの自己cleanup型E2E simulation。"""

import json
from datetime import datetime, timedelta, timezone

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

    def __init__(self, updated_min: str):
        self._text = {"sync:updated_min": updated_min}
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


def _owned_event(event: dict, event_id: str, run_id: str) -> bool:
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return (
        str(event.get("id") or "") == event_id
        and str(private.get("ie_event_bot_e2e_run") or "") == run_id
    )


async def run_webhook_dispatch_probe(
    env,
    state,
    dispatch,
    run_id: str | None = None,
) -> dict:
    """所有Google eventだけを実差分取得からNotionへ適用して回収する。"""

    async def apply_via_webhook(events: list[dict], stages: dict[str, int]) -> dict:
        if len(events) != 1 or not isinstance(events[0], dict):
            stages["webhook_fixture"] = 500
            return {"ok": False}

        fixture = events[0]
        event_id = str(fixture.get("id") or "")
        private = ((fixture.get("extendedProperties") or {}).get("private") or {})
        expected_run_id = str(private.get("ie_event_bot_e2e_run") or "")
        if not _owned_event(fixture, event_id, expected_run_id):
            stages["webhook_fixture"] = 500
            return {"ok": False}
        stages["webhook_fixture"] = 200

        cursor = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        sync_state = _RunScopedSyncState(cursor)
        selected_count = 0
        notion_write_started = False

        async def apply_owned(apply_env, _sync_state, fetched_events):
            nonlocal notion_write_started, selected_count
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
            return await apply_google_events(
                apply_env,
                _EphemeralApplyState(),
                owned,
            )

        try:
            response = await dispatch(sync_state, apply_owned)
            status = int(response.status)
            payload = json.loads(await response.text() or "{}")
        except Exception:
            status = 500
            payload = {}

        safe_payload = payload if isinstance(payload, dict) else {}
        google = safe_payload.get("google")
        apply_result = safe_payload.get("google_apply")
        dispatch_ok = (
            200 <= status < 300
            and safe_payload.get("ok") is True
            and safe_payload.get("source") == "e2e-webhook"
            and isinstance(google, dict)
            and google.get("ok") is True
            and selected_count == 1
            and isinstance(apply_result, dict)
            and apply_result.get("ok") is True
        )
        stages["webhook_delta_fetch"] = 200 if selected_count == 1 else 500
        stages["webhook_dispatch"] = status
        stages["webhook_cursor_isolated"] = 200 if sync_state.cursor_written else 500
        stages["webhook_last_epoch_isolated"] = (
            200 if sync_state.last_epoch_written else 500
        )
        stages["webhook_last_result_isolated"] = (
            200 if sync_state.last_result_written else 500
        )
        if not dispatch_ok or not isinstance(apply_result, dict):
            return {
                "ok": False,
                "processed": 0,
                "pending_events": 0,
                "error_count": 1,
                "_notion_write_started": notion_write_started,
            }
        return {**apply_result, "_notion_write_started": notion_write_started}

    manifest_state = _EphemeralManifestState(state)
    return await run_google_notion_scenario(
        env,
        manifest_state,
        run_id,
        manifest_service=WEBHOOK_DISPATCH_MANIFEST_SERVICE,
        manifest_kind=WEBHOOK_DISPATCH_MANIFEST_KIND,
        apply_runner=apply_via_webhook,
        apply_error="webhook_dispatch_failed",
    )


async def cleanup_webhook_dispatch_probe(
    env,
    state,
    expected_run_id: str | None = None,
) -> dict:
    """run IDと対象fingerprintが一致するsimulation資源だけを回収する。"""
    manifest_state = _EphemeralManifestState(state)
    return await cleanup_google_notion_scenario(
        env,
        manifest_state,
        expected_run_id,
        manifest_service=WEBHOOK_DISPATCH_MANIFEST_SERVICE,
        manifest_kind=WEBHOOK_DISPATCH_MANIFEST_KIND,
    )
