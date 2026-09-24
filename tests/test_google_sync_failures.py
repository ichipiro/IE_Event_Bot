"""HTTP失敗後も通常Google同期の残件と対応IDを保持する。外部APIは代替する。"""

import asyncio
import json
from copy import deepcopy

import pytest

import google_apply_sync as apply
from entry import Default
from state import StateStore
from tests.conftest import Response
from tests.test_e2e_google_sync_probe import Scenario
from tests.test_google_sync_pipeline import CURSOR, QUEUE, event


def dispatch(scenario):
    async def invoke():
        worker = Default()
        worker.env = scenario.env
        response = await worker._run_sync_dispatch(None, StateStore(scenario.env), "manual")
        return response.status, json.loads(await response.text())

    return asyncio.run(invoke())


@pytest.mark.parametrize("operation", ["create", "archive", "delete", "writeback", "query", "read"])
@pytest.mark.parametrize("http_status", [403, 429, 503])
def test_http_failure_keeps_queue_cursor_and_recovers(monkeypatch, operation, http_status):
    scenario = Scenario(monkeypatch)
    source = event("first")
    scenario.google[source["id"]] = source
    if operation not in ("create", "query"):
        assert dispatch(scenario)[0] == 200
    previous = dict(scenario.env.STATE_KV.data)
    previous_epoch = asyncio.run(scenario.store.get_sync_last_epoch())
    if operation in ("archive", "delete"):
        source = event("first", status="cancelled", updated="2099-01-01T12:05:00Z")
    else:
        source = event("first", description="changed", updated="2099-01-01T12:05:00Z")
    scenario.google[source["id"]] = source
    original = apply.fetch
    failures = []

    async def failing(url, options=None):
        options = options or {}
        method = options.get("method", "GET")
        body = json.loads(options.get("body") or "{}")
        props = body.get("properties", {})
        match = {
            "create": "notion.com" in url and url.endswith("/pages") and method == "POST",
            "archive": "notion.com" in url and body.get("archived") is True,
            "delete": "discord.com" in url and method == "DELETE",
            "writeback": "notion.com" in url and set(props) == {"メッセージID"},
            "query": "notion.com" in url and url.endswith("/query"),
            "read": "notion.com" in url and method == "GET",
        }[operation]
        if match:
            failures.append(method)
            return Response('{"error":"private failure details"}', status=http_status)
        return await original(url, options)

    monkeypatch.setattr(apply, "fetch", failing)
    status, result = dispatch(scenario)
    assert failures
    assert status == 500
    assert result["google_apply"]["pending_events"] == 1
    assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == [source]
    assert scenario.env.STATE_KV.data.get(CURSOR) == previous.get(CURSOR)
    assert asyncio.run(scenario.store.get_sync_last_epoch()) == previous_epoch
    assert "private failure details" not in json.dumps(result)
    if operation in ("delete", "writeback", "read"):
        assert scenario.env.STATE_KV.data["map:gcal_discord"] == previous["map:gcal_discord"]
    if operation in ("archive", "read"):
        assert scenario.env.STATE_KV.data["map:gcal_notion"] == previous["map:gcal_notion"]
    if operation in ("create", "query"):
        assert not scenario.pages and not scenario.discord
    # Googleから再取得しなくても、永続化したqueueだけで回復できる。
    scenario.google.clear()
    monkeypatch.setattr(apply, "fetch", original)
    assert dispatch(scenario)[0] == 200
    assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == []
    assert len(scenario.pages) == 1
    page = next(iter(scenario.pages.values()))
    if operation in ("archive", "delete"):
        assert page["archived"] and not scenario.discord
    else:
        assert len(scenario.discord) == 1
        assert page["properties"]["内容"]["rich_text"][0]["text"]["content"] == "changed"


def test_subrequest_limit_keeps_unattempted_events(monkeypatch):
    scenario = Scenario(monkeypatch)
    scenario.env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = "3"
    sources = [event(name) for name in ("first", "second", "third", "fourth")]
    scenario.google = {source["id"]: source for source in sources}
    original = apply.fetch

    async def failing(url, options=None):
        if url.endswith("/query"):
            raise RuntimeError("Too many subrequests")
        return await original(url, options)

    monkeypatch.setattr(apply, "fetch", failing)
    status, result = dispatch(scenario)
    assert status == 500 and result["google_apply"]["processed"] == 1
    assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == sources
    monkeypatch.setattr(apply, "fetch", original)
    scenario.google.clear()
    assert dispatch(scenario)[0] == 200
    assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == [sources[-1]]
    assert dispatch(scenario)[0] == 200
    assert len(scenario.pages) == len(scenario.discord) == 4


def test_repeated_cancellation_accepts_discord_already_deleted(monkeypatch):
    scenario = Scenario(monkeypatch)
    scenario.google = {"first": event("first")}
    assert dispatch(scenario)[0] == 200
    scenario.discord.clear()
    scenario.google = {"first": event("first", status="cancelled")}
    assert dispatch(scenario)[0] == 200
    assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == []
    assert json.loads(scenario.env.STATE_KV.data["map:gcal_discord"]) == {}


def test_discord_origin_cancel_does_not_delete_original(monkeypatch):
    scenario = Scenario(monkeypatch)
    scenario.google = {"first": event("first")}
    assert dispatch(scenario)[0] == 200
    original_discord = deepcopy(scenario.discord)
    discord_id = next(iter(original_discord))
    scenario.google = {"first": event("first", status="cancelled", extendedProperties={
        "private": {"ie_origin": "discord", "ie_discord_event_id": discord_id},
    })}
    assert dispatch(scenario)[0] == 200
    assert scenario.discord == original_discord
    assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == []


@pytest.mark.parametrize("count", [1, 2, 5, 17])
@pytest.mark.parametrize("limit", [1, 2, 5])
def test_event_counts_drain_without_loss_or_duplicate_creation(monkeypatch, count, limit):
    scenario = Scenario(monkeypatch)
    scenario.env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = str(limit)
    sources = [event(f"event-{index}") for index in range(count)]
    scenario.google = {source["id"]: source for source in sources}
    processed = 0
    while processed < count:
        status, result = dispatch(scenario)
        assert status == 200, result
        processed = min(processed + limit, count)
        assert json.loads(scenario.env.STATE_KV.data[QUEUE]) == sources[processed:]
        assert len(scenario.pages) == len(scenario.discord) == processed
        scenario.google.clear()
    assert len([call for call in scenario.calls if call == ("discord", "POST")]) == count
    assert len(json.loads(scenario.env.STATE_KV.data["map:gcal_notion"])["internal"]) == count
    assert len(json.loads(scenario.env.STATE_KV.data["map:gcal_discord"])) == count
