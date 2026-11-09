"""通常Discord同期の認証・検索障害とGoogle由来情報の保持を検証する。"""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import discord_notion_sync as sync
from state import StateStore
from google_event_content import DISCORD_CONTENT_HASH, google_content_hash
from tests.conftest import Response
from tests.fakes import MemoryKV

QUEUE = "sync:discord_notion_queue"


class DiscordAPI:
    """同期先のページと予定を保持し、HTTP応答だけを代替する。"""

    def __init__(self, monkeypatch):
        self.env = SimpleNamespace(
            STATE_KV=MemoryKV(), GOOGLE_CALENDAR_ID="test-calendar",
            DISCORD_TO_GOOGLE_SYNC_ENABLED="true", NOTION_TOKEN="test-token",
            NOTION_EVENT_INTERNAL_ID="test-database", DISCORD_GUILD_ID="test-guild",
        )
        self.events = [{
            "id": "discord-one", "name": "検証予定", "description": "変更前",
            "status": 1, "creator_id": "test-creator", "entity_type": 3,
            "scheduled_start_time": "2099-01-01T01:00:00Z",
            "scheduled_end_time": "2099-01-01T02:00:00Z",
            "entity_metadata": {"location": "検証会場"},
        }]
        self.pages = {}
        self.google = {}
        self.token: str | None = "test-google-token"
        self.query_failure: tuple[int, str] | None = None
        self.writes = []
        monkeypatch.setattr(sync, "fetch", self.fetch)
        monkeypatch.setattr(sync, "_list_discord_scheduled_events", self.list_events)
        monkeypatch.setattr(sync, "get_google_access_token", self.access_token)

    async def access_token(self, *args):
        return self.token

    async def list_events(self, env):
        return deepcopy(self.events), None

    async def fetch(self, url, options=None):
        options = options or {}
        method = options.get("method", "GET")
        body = json.loads(options.get("body") or "{}")
        if "api.notion.com" in url and url.endswith("/query"):
            if self.query_failure is not None:
                status, text = self.query_failure
                return Response(text, status=status)
            message_id = body["filter"]["rich_text"]["equals"]
            matches = [page for page in self.pages.values()
                       if not page.get("archived") and
                       page["properties"]["メッセージID"]["rich_text"][0]["text"]["content"] == message_id]
            return Response(json.dumps({"results": matches}))
        self.writes.append((method, url))
        if "api.notion.com" in url and url.endswith("/pages") and method == "POST":
            page_id = f"page-{len(self.pages) + 1}"
            self.pages[page_id] = {"id": page_id, **deepcopy(body)}
            return Response(json.dumps(self.pages[page_id]), status=201)
        if "api.notion.com" in url and "/pages/" in url and method == "PATCH":
            page = self.pages[url.rsplit("/", 1)[1]]
            page["properties"].update(body.pop("properties", {}))
            page.update(body)
            return Response(json.dumps(page))
        if "googleapis.com" in url and url.endswith("/events") and method == "POST":
            event_id = f"google-{len(self.google) + 1}"
            self.google[event_id] = {"id": event_id, **deepcopy(body)}
            return Response(json.dumps(self.google[event_id]), status=201)
        if "googleapis.com" in url and "/events/" in url:
            event_id = url.rsplit("/", 1)[1]
            if method == "PATCH":
                if "extendedProperties" in body:
                    body["extendedProperties"]["private"] = {
                        **self.google[event_id].get("extendedProperties", {}).get("private", {}),
                        **body["extendedProperties"]["private"],
                    }
                self.google[event_id].update(deepcopy(body))
                return Response(json.dumps(self.google[event_id]))
            if method == "DELETE":
                self.google.pop(event_id)
                return Response("", status=204)
        raise AssertionError((method, url))

    def poll(self):
        return asyncio.run(sync.run_discord_notion_poll_sync(self.env, StateStore(self.env)))

    def queue(self):
        return asyncio.run(StateStore(self.env).get_json(QUEUE, []))

    def change(self, operation):
        if operation == "delete":
            self.events.clear()
        else:
            self.events[0]["description"] = "変更後"


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_missing_google_token_keeps_operation_until_auth_recovers(monkeypatch, operation):
    api = DiscordAPI(monkeypatch)
    if operation != "create":
        assert api.poll()["ok"]
        api.change(operation)
    before_pages, before_google = deepcopy(api.pages), deepcopy(api.google)
    api.writes.clear()
    api.token = None

    failed = api.poll()

    assert failed["ok"] is False
    assert failed["pending_changes"] == 1
    assert api.queue() == [{"op": "delete" if operation == "delete" else "upsert", "id": "discord-one"}]
    assert api.pages == before_pages and api.google == before_google
    assert api.writes == []
    api.token = "test-google-token"
    recovered = api.poll()
    assert recovered["ok"] and recovered["pending_changes"] == 0
    assert api.queue() == []
    if operation == "delete":
        assert not api.google and api.pages["page-1"]["archived"]
    else:
        assert len(api.pages) == len(api.google) == 1
        assert api.google["google-1"]["description"] == api.events[0]["description"]
    applied = list(api.writes)
    assert api.poll()["processed_changes"] == 0
    assert api.writes == applied


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("failure", [
    (403, "{}"), (429, "{}"), (503, "{}"),
    (200, "{"), (200, "[]"), (200, "{}"),
    (200, '{"results":null}'), (200, '{"results":{}}'),
    (200, '{"results":[{}]}'),
])
def test_failed_notion_query_keeps_operation_without_duplicate_creation(monkeypatch, operation, failure):
    api = DiscordAPI(monkeypatch)
    assert api.poll()["ok"]
    api.change(operation)
    before_pages, before_google = deepcopy(api.pages), deepcopy(api.google)
    api.writes.clear()
    api.query_failure = failure

    failed = api.poll()

    assert failed["ok"] is False
    assert failed["pending_changes"] == 1
    assert api.queue() == [{"op": "delete" if operation == "delete" else "upsert", "id": "discord-one"}]
    assert api.pages == before_pages and api.google == before_google
    assert api.writes == []
    api.query_failure = None
    recovered = api.poll()
    assert recovered["ok"] and recovered["pending_changes"] == 0
    assert api.queue() == []
    assert len(api.pages) == 1
    if operation == "delete":
        assert not api.google and api.pages["page-1"]["archived"]
    else:
        assert len(api.google) == 1
        assert api.google["google-1"]["description"] == "変更後"


@pytest.mark.parametrize("private", [{"source": "google"}, {
    "ie_origin": "discord", "ie_discord_event_id": "discord-one",
}])
def test_existing_google_origin_is_preserved_when_discord_changes(monkeypatch, private):
    api = DiscordAPI(monkeypatch)
    assert api.poll()["ok"]
    assert api.google["google-1"]["extendedProperties"]["private"]["ie_origin"] == "discord"
    api.google["google-1"]["extendedProperties"] = {"private": deepcopy(private)}
    api.change("update")

    assert api.poll()["ok"]

    assert api.google["google-1"]["extendedProperties"]["private"] == {
        **private, DISCORD_CONTENT_HASH: google_content_hash(api.google["google-1"]),
    }
    assert api.google["google-1"]["description"] == "変更後"


def test_removing_discord_location_clears_google_location_and_keeps_matching_echo(monkeypatch):
    api = DiscordAPI(monkeypatch)
    assert api.poll()["ok"]
    api.events[0]["entity_metadata"] = {}

    assert api.poll()["ok"]

    event = api.google["google-1"]
    assert event.get("location", "") == ""
    assert event["extendedProperties"]["private"][DISCORD_CONTENT_HASH] == google_content_hash(event)
