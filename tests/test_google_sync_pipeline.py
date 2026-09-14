"""通常Google同期を取得から適用・状態保存まで接続する。外部APIは代替する。"""

import asyncio
import json
from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import google_apply_sync as apply
import google_calendar_sync as calendar
from entry import Default
from state import StateStore
from tests.conftest import Response
from tests.test_e2e_sync_lock_probe import environment

CURSOR = "sync:updated_min"
QUEUE = "sync:google_apply_queue"


def event(event_id, *, updated="2099-01-01T12:00:00Z", **changes):
    return {
        "id": event_id, "summary": event_id, "updated": updated,
        "start": {"dateTime": "2099-02-01T12:00:00Z"},
        "end": {"dateTime": "2099-02-01T13:00:00Z"}, **changes,
    }


class Pipeline:
    def __init__(self, monkeypatch):
        self.worker = Default()
        self.worker.env = environment()
        env = self.worker.env
        env.SYNC_ALL_INCLUDE_DISCORD_NOTION = "false"
        env.GOOGLE_CALENDAR_ID = "test-calendar"
        env.NOTION_TOKEN = "test-token"
        env.NOTION_EVENT_INTERNAL_ID = "test-database"
        env.DISCORD_TOKEN = "test-token"
        env.DISCORD_GUILD_ID = "test-guild"
        env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = "1"
        self.pages = {}
        self.discord = {}
        self.requests = []
        self.responses = []
        self.fail_query = False
        self.discord_calls = []

        async def token(*args):
            return "test-token"

        async def fetch(url, options):
            self.requests.append(parse_qs(urlparse(url).query))
            status, data = self.responses.pop(0)
            return Response(json.dumps(data), status=status)

        async def query(_env, database, target):
            if self.fail_query:
                self.fail_query = False
                raise RuntimeError("injected_query_failure")
            return next((deepcopy(page) for page in self.pages.values()
                         if page["google_event_id"] == target and not page.get("archived")), None)

        async def get_page(_env, page_id):
            return deepcopy(self.pages[page_id])

        async def create(_env, database, **fields):
            page_id = "page-" + fields["google_event_id"]
            assert page_id not in self.pages
            self.pages[page_id] = {"id": page_id, "properties": {}, **fields}
            return page_id

        async def update(_env, page_id, **fields):
            self.pages[page_id].update(fields)
            return True

        async def archive(_env, page):
            self.pages[page["id"]]["archived"] = True
            return True

        async def discord_api(_env, method, path, payload=None):
            self.discord_calls.append((method, path))
            if method == "POST":
                assert isinstance(payload, dict)
                discord_id = "discord-" + payload["name"]
                assert discord_id not in self.discord
                self.discord[discord_id] = deepcopy(payload)
                return {"id": discord_id}
            discord_id = path.rsplit("/", 1)[-1]
            assert discord_id in self.discord
            if method == "PATCH":
                self.discord[discord_id] = deepcopy(payload)
                return {"id": discord_id}
            assert method == "DELETE"
            del self.discord[discord_id]
            return {}

        monkeypatch.setattr(calendar, "get_google_access_token", token)
        monkeypatch.setattr(calendar, "fetch", fetch)
        for name, fn in {
            "_notion_query_by_google_event_id": query, "_notion_get_page": get_page,
            "_notion_create_event": create, "_notion_update_event": update,
            "_notion_archive_page": archive, "_discord_api_request": discord_api,
        }.items():
            monkeypatch.setattr(apply, name, fn)

    @property
    def kv(self):
        return self.worker.env.STATE_KV

    def run(self, *responses):
        self.responses = list(responses)

        async def invoke():
            # 本番の取得・適用をそのまま使い、呼出しごとにStateStoreを作り直す。
            response = await self.worker._run_sync_dispatch(
                None, StateStore(self.worker.env), "manual",
            )
            return response.status, json.loads(await response.text())

        result = asyncio.run(invoke())
        assert not self.responses
        return result


def test_paginated_fetch_queue_drain_update_and_delete(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    first, second = event("first"), event("second", updated="2099-01-01T12:01:00Z")
    status, result = pipeline.run(
        (200, {"items": [first], "nextPageToken": "page-two"}),
        (200, {"items": [second]}),
    )
    assert status == 200 and result["google_apply"]["pending_events"] == 1
    assert "updatedMin" not in pipeline.requests[0]
    assert pipeline.requests[1]["pageToken"] == ["page-two"]
    assert pipeline.kv.data[CURSOR] == second["updated"]
    assert json.loads(pipeline.kv.data[QUEUE]) == [second]
    assert set(pipeline.pages) == {"page-first"}

    # 2分の重複範囲を再取得しても、既存残件を先に処理する。
    status, result = pipeline.run((200, {"items": [second]}))
    assert status == 200 and result["google_apply"]["pending_events"] == 0
    assert pipeline.requests[-1]["updatedMin"] == ["2099-01-01T11:59:00Z"]
    assert json.loads(pipeline.kv.data["map:gcal_discord"]) == {
        "first": "discord-first", "second": "discord-second",
    }
    assert json.loads(pipeline.kv.data["map:gcal_notion"])["internal"] == {
        "first": "page-first", "second": "page-second",
    }
    changed = event("first", summary="changed", updated="2099-01-01T12:02:00Z")
    assert pipeline.run((200, {"items": [changed]}))[0] == 200
    assert pipeline.pages["page-first"]["name"] == "changed"
    assert pipeline.discord["discord-first"]["name"] == "changed"
    cancelled = event("first", status="cancelled", updated="2099-01-01T12:03:00Z")
    assert pipeline.run((200, {"items": [cancelled]}))[0] == 200
    assert pipeline.pages["page-first"]["archived"]
    assert set(pipeline.discord) == {"discord-second"}
    assert "first" not in json.loads(pipeline.kv.data["map:gcal_discord"])
    assert "first" not in json.loads(pipeline.kv.data["map:gcal_notion"])["internal"]
    assert [method for method, _ in pipeline.discord_calls] == ["POST", "POST", "PATCH", "DELETE"]
    assert pipeline.kv.data[CURSOR] == cancelled["updated"]


def test_apply_exception_keeps_cursor_and_retries_before_remainder(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    pipeline.kv.data[CURSOR] = "2099-01-01T11:00:00Z"
    first, second = event("first"), event("second")
    pipeline.fail_query = True
    status, result = pipeline.run((200, {"items": [first, second]}))
    assert status == 500 and result["google_apply"]["pending_events"] == 2
    assert pipeline.kv.data[CURSOR] == "2099-01-01T11:00:00Z"
    assert json.loads(pipeline.kv.data[QUEUE]) == [first, second]
    assert not pipeline.pages and not pipeline.discord
    status, result = pipeline.run((200, {"items": [first, second]}))
    assert status == 200 and result["google_apply"]["pending_events"] == 1
    assert set(pipeline.pages) == {"page-first"}
    assert json.loads(pipeline.kv.data[QUEUE]) == [second]
    assert pipeline.kv.data[CURSOR] == first["updated"]


def test_second_page_error_does_not_apply_or_advance_cursor(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    pipeline.kv.data[CURSOR] = "2099-01-01T11:00:00Z"
    previous = dict(pipeline.kv.data)
    status, result = pipeline.run(
        (200, {"items": [event("first")], "nextPageToken": "page-two"}),
        (503, {"error": "injected_failure"}),
    )
    assert status == 500 and result["google"]["error"] == "google_list_failed"
    assert result["google_apply"]["skipped"]
    assert not pipeline.pages and not pipeline.discord
    assert pipeline.kv.data[CURSOR] == previous[CURSOR]
    assert QUEUE not in pipeline.kv.data and "map:gcal_notion" not in pipeline.kv.data
