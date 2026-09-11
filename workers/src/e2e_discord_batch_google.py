"""通常ポーリングで作るGoogleイベントの固定ID・読戻し・回収。"""

from e2e_discord_delta_probe import _DeltaEnv
from e2e_discord_google_probe import _parse_instant, _verify_calendar
from e2e_discord_notion_probe import _env_text, _event_name
from e2e_discord_probe import _run_marker
from e2e_google_probe import _event_item_url, _google_request
from google_auth import get_google_access_token


class GoogleBatchError(Exception):
    pass


class GoogleBatchEnv(_DeltaEnv):
    DISCORD_TO_GOOGLE_SYNC_ENABLED = "true"

    def __init__(self, env, token: str):
        super().__init__(env)
        # リクエスト内だけで再利用し、通常KVのtoken cacheには触れない。
        self.GOOGLE_API_BEARER_TOKEN = token


class GoogleBatch:
    def __init__(self, env, token: str):
        self.calendar = _env_text(env, "GOOGLE_CALENDAR_ID")
        self.token = token
        self.env = GoogleBatchEnv(env, token)

    @classmethod
    async def connect(cls, env):
        if not _env_text(env, "GOOGLE_CALENDAR_ID"):
            raise GoogleBatchError("discord_batch_google_calendar_required")
        token = await get_google_access_token(env, None)
        if not token:
            raise GoogleBatchError("discord_batch_google_token_required")
        return cls(env, token)

    async def verify_target(self, stages):
        error = await _verify_calendar(self.calendar, self.token, stages)
        if error:
            raise GoogleBatchError("discord_batch_google_calendar_mismatch")

    @staticmethod
    def owned(event, slot):
        if not isinstance(event, dict):
            return False
        private = (event.get("extendedProperties") or {}).get("private") or {}
        return (
            event.get("id") == slot["google_event_id"]
            and event.get("status") != "cancelled"
            and event.get("summary") == _event_name(slot["run_id"])
            and _run_marker(slot["run_id"]) in str(event.get("description") or "")
            and private.get("ie_origin") == "discord"
            and private.get("ie_discord_event_id") == slot["discord_event_id"]
        )

    async def read(self, slot, *, source=None, absent=False):
        status, event = await _google_request(
            "GET",
            _event_item_url(self.calendar, slot["google_event_id"]),
            self.token,
        )
        if absent:
            if status != 404:
                raise GoogleBatchError("discord_batch_google_expected_absent")
            return
        if status != 200 or not self.owned(event, slot):
            raise GoogleBatchError("discord_batch_google_target_mismatch")
        if source is not None and (
            event.get("description") != source.get("description")
            or event.get("location")
            != (source.get("entity_metadata") or {}).get("location")
            or _parse_instant((event.get("start") or {}).get("dateTime"))
            != _parse_instant(source.get("scheduled_start_time"))
            or _parse_instant((event.get("end") or {}).get("dateTime"))
            != _parse_instant(source.get("scheduled_end_time"))
        ):
            raise GoogleBatchError("discord_batch_google_content_mismatch")

    async def cleanup(self, slot, stages):
        if not slot["create_attempted"]["google_event"] or slot.get(
            "google_cleanup_done"
        ):
            return True
        url = _event_item_url(self.calendar, slot["google_event_id"])
        for _ in range(4):
            status, event = await _google_request("GET", url, self.token)
            stages["google_cleanup_verify"] = status
            if status in (404, 410):
                return True
            if status != 200:
                continue
            if not self.owned(event, slot):
                return False
            status, _ = await _google_request(
                "DELETE", url + "?sendUpdates=none", self.token
            )
            stages["google_delete"] = status
            if status in (200, 204, 404, 410):
                return True
        return False
