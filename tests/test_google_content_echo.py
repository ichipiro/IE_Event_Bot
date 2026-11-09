"""Googleの日時表記差に影響されず、実際の編集を区別する。"""

from copy import deepcopy
import json

import pytest

import google_apply_sync as apply
from google_event_content import DISCORD_CONTENT_HASH, google_content_hash
from tests.conftest import Response
from tests.test_google_sync_pipeline import Pipeline, QUEUE, event as google_event


def event():
    return {
        "summary": "予定", "description": "本文", "location": "会場",
        "start": {"dateTime": "2099-01-01T10:00:00+09:00", "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": "2099-01-01T11:00:00+09:00", "timeZone": "Asia/Tokyo"},
    }


def test_same_instant_with_different_google_time_format_has_same_hash():
    original = event()
    returned = {**original,
                "start": {"dateTime": "2099-01-01T01:00:00.000Z", "timeZone": "UTC"},
                "end": {"dateTime": "2099-01-01T02:00:00+00:00"},
                "updated": "2099-01-01T00:00:00Z"}

    assert google_content_hash(original) == google_content_hash(returned)


@pytest.mark.parametrize("field,value", [
    ("summary", "変更後の予定"), ("description", "変更後の本文"), ("location", "別会場"),
    ("start", {"dateTime": "2099-01-01T01:01:00Z"}),
    ("end", {"dateTime": "2099-01-01T02:01:00Z"}),
    ("start", {"date": "2099-01-01"}),
])
def test_google_edit_changes_hash(field, value):
    original = event()
    changed = {**deepcopy(original), field: value}

    assert google_content_hash(original) != google_content_hash(changed)


def test_retrieved_discord_write_preserves_new_discord_edit_but_google_edit_applies(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    pipeline.worker.env.GOOGLE_API_BEARER_TOKEN = "test-token"

    async def acknowledge(url, options=None):
        return Response("{}")

    monkeypatch.setattr(apply, "fetch", acknowledge)
    source = google_event("first", description="元の本文")
    assert pipeline.run((200, {"items": [source]}))[0] == 200
    pipeline.discord["discord-first"]["description"] = "Discordでの新しい編集"
    echo = {**source, "extendedProperties": {"private": {
        DISCORD_CONTENT_HASH: google_content_hash(source),
    }}}
    before_calls = list(pipeline.discord_calls)

    assert pipeline.run((200, {"items": [echo]}))[0] == 200

    assert pipeline.discord["discord-first"]["description"] == "Discordでの新しい編集"
    assert pipeline.discord_calls == before_calls
    changed = {**deepcopy(echo), "description": "Googleでの新しい編集"}
    assert pipeline.run((200, {"items": [changed]}))[0] == 200
    assert pipeline.discord["discord-first"]["description"] == "Googleでの新しい編集"
    assert pipeline.discord_calls[-1][0] == "PATCH"


def test_discord_echo_without_known_mapping_still_creates_discord_event(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    source = google_event("first", description="元の本文")
    source["extendedProperties"] = {"private": {DISCORD_CONTENT_HASH: google_content_hash(source)}}

    assert pipeline.run((200, {"items": [source]}))[0] == 200

    assert "discord-first" in pipeline.discord
    assert pipeline.discord_calls[-1][0] == "POST"


def test_google_cancellation_with_discord_echo_marker_still_deletes_discord(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    source = google_event("first", description="元の本文")
    assert pipeline.run((200, {"items": [source]}))[0] == 200
    cancelled = {**source, "status": "cancelled", "extendedProperties": {"private": {
        DISCORD_CONTENT_HASH: google_content_hash(source),
    }}}

    assert pipeline.run((200, {"items": [cancelled]}))[0] == 200

    assert not pipeline.discord
    assert pipeline.pages["page-first"]["archived"]


def test_real_google_edit_acknowledges_new_content_before_content_changes_back(monkeypatch):
    pipeline = Pipeline(monkeypatch)
    pipeline.worker.env.GOOGLE_API_BEARER_TOKEN = "test-token"
    remote = google_event("first", description="本文A")
    remote["extendedProperties"] = {"private": {DISCORD_CONTENT_HASH: google_content_hash(remote)}}
    acknowledgements = []

    async def acknowledge(url, options=None):
        assert options is not None
        payload = json.loads(options["body"])
        assert options["method"] == "PATCH" and "googleapis.com" in url
        marker = payload["extendedProperties"]["private"][DISCORD_CONTENT_HASH]
        assert marker == google_content_hash(remote)
        acknowledgements.append(marker)
        remote["extendedProperties"]["private"][DISCORD_CONTENT_HASH] = marker
        return Response(json.dumps(remote))

    monkeypatch.setattr(apply, "fetch", acknowledge)
    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200
    remote["description"] = "本文B"
    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200
    assert pipeline.discord["discord-first"]["description"] == "本文B"
    remote["description"] = "本文A"

    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200

    assert pipeline.discord["discord-first"]["description"] == "本文A"
    assert len(acknowledgements) == 2


@pytest.mark.parametrize("fail_first_ack", [False, True])
def test_repeated_google_content_preserves_later_discord_edit_even_after_ack_failure(monkeypatch, fail_first_ack):
    pipeline = Pipeline(monkeypatch)
    pipeline.worker.env.GOOGLE_API_BEARER_TOKEN = "test-token"
    remote = google_event("first", description="本文A")
    remote["extendedProperties"] = {"private": {DISCORD_CONTENT_HASH: google_content_hash(remote)}}
    statuses = [503, 200] if fail_first_ack else [200]
    acknowledgements = []

    async def acknowledge(url, options=None):
        assert options is not None
        status = statuses.pop(0)
        marker = json.loads(options["body"])["extendedProperties"]["private"][DISCORD_CONTENT_HASH]
        acknowledgements.append(marker)
        if status == 200:
            remote["extendedProperties"]["private"][DISCORD_CONTENT_HASH] = marker
        return Response(json.dumps(remote), status=status)

    monkeypatch.setattr(apply, "fetch", acknowledge)
    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200
    remote["description"] = "本文B"

    status, result = pipeline.run((200, {"items": [deepcopy(remote)]}))

    assert status == (500 if fail_first_ack else 200)
    assert result["google_apply"]["pending_events"] == int(fail_first_ack)
    assert pipeline.discord["discord-first"]["description"] == "本文B"
    pipeline.discord["discord-first"]["description"] = "Discordで本文Cへ編集"
    before_calls = list(pipeline.discord_calls)
    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200
    assert pipeline.discord["discord-first"]["description"] == "Discordで本文Cへ編集"
    assert pipeline.discord_calls == before_calls
    assert len(acknowledgements) == (2 if fail_first_ack else 1)


@pytest.mark.parametrize("incoming_change", ["content", "cancelled", "equivalent_time"])
def test_ack_retry_uses_new_google_content_but_keeps_equivalent_retry(monkeypatch, incoming_change):
    pipeline = Pipeline(monkeypatch)
    pipeline.worker.env.GOOGLE_API_BEARER_TOKEN = "test-token"
    remote = google_event("first", description="本文A")
    remote["extendedProperties"] = {"private": {DISCORD_CONTENT_HASH: google_content_hash(remote)}}
    statuses = [503, 200]

    async def acknowledge(url, options=None):
        assert options is not None
        status = statuses.pop(0)
        if status == 200:
            remote["extendedProperties"]["private"][DISCORD_CONTENT_HASH] = json.loads(
                options["body"])["extendedProperties"]["private"][DISCORD_CONTENT_HASH]
        return Response(json.dumps(remote), status=status)

    monkeypatch.setattr(apply, "fetch", acknowledge)
    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200
    remote["description"] = "本文B"
    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 500
    assert len(json.loads(pipeline.kv.data[QUEUE])) == 1
    before_calls = list(pipeline.discord_calls)
    if incoming_change == "content":
        remote["description"] = "Googleで本文Cへ編集"
    elif incoming_change == "cancelled":
        remote["status"] = "cancelled"
    else:
        remote["start"]["dateTime"] = "2099-02-01T21:00:00.000+09:00"
        pipeline.discord["discord-first"]["description"] = "Discordで本文Cへ編集"

    assert pipeline.run((200, {"items": [deepcopy(remote)]}))[0] == 200

    assert json.loads(pipeline.kv.data[QUEUE]) == []
    if incoming_change == "cancelled":
        assert not pipeline.discord and pipeline.pages["page-first"]["archived"]
    elif incoming_change == "content":
        assert pipeline.discord["discord-first"]["description"] == "Googleで本文Cへ編集"
    else:
        assert pipeline.discord["discord-first"]["description"] == "Discordで本文Cへ編集"
        assert pipeline.discord_calls == before_calls
