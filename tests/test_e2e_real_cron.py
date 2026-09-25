import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import e2e_cron_entry as cron


START = 1_800_000_000_000
RUN = "E2E-20260925T000000Z-1234abcd"


def worker(monkeypatch):
    monkeypatch.setattr(cron.time, "time", lambda: (START + 60_100) / 1000)
    env = SimpleNamespace(
        E2E_CRON_RUN_ID=RUN, E2E_CRON_START_MS=str(START),
        E2E_CRON_DEADLINE_MS=str(START + 1_200_000), E2E_CRON_COMMIT="a" * 40,
        CF_VERSION_METADATA=SimpleNamespace(tag=RUN, id="version-1"),
        STATE_KV=SimpleNamespace(put=AsyncMock()),
        **dict.fromkeys(cron.CRON_FLAGS, "false"),
    )
    handler = cron.Default()
    handler.env = env
    controller = SimpleNamespace(scheduledTime=START + 60_000, cron="* * * * *")
    return handler, controller


def test_real_handler_records_controller_and_version(monkeypatch):
    handler, controller = worker(monkeypatch)
    asyncio.run(handler.scheduled(controller, handler.env, None))
    key, text = handler.env.STATE_KV.put.call_args.args
    assert key == f"e2e:real-cron:{RUN}:{START + 60_000}"
    receipt = json.loads(text)
    assert receipt["source"] == "scheduled"
    assert receipt["version_tag"] == RUN
    assert receipt["version_id"] == "version-1"
    assert receipt["scheduled_time_ms"] == START + 60_000
    assert receipt["job_count"] == 0
    assert receipt["normal_dispatch_completed"] is True


@pytest.mark.parametrize("flag", cron.CRON_FLAGS)
def test_external_job_flags_fail_closed(monkeypatch, flag):
    handler, controller = worker(monkeypatch)
    setattr(handler.env, flag, "true")
    asyncio.run(handler.scheduled(controller, handler.env, None))
    handler.env.STATE_KV.put.assert_not_called()


@pytest.mark.parametrize("change", ["expired", "before_start", "future", "wrong_cron", "wrong_version", "bad_run", "long_window"])
def test_outside_scope_does_not_write(monkeypatch, change):
    handler, controller = worker(monkeypatch)
    if change == "expired":
        handler.env.E2E_CRON_DEADLINE_MS = str(START + 60_100)
    elif change == "before_start":
        controller.scheduledTime = START - 60_000
    elif change == "future":
        controller.scheduledTime = START + 120_000
    elif change == "wrong_cron":
        controller.cron = "*/5 * * * *"
    elif change == "wrong_version":
        handler.env.CF_VERSION_METADATA.tag = "other"
    elif change == "bad_run":
        handler.env.E2E_CRON_RUN_ID = "../../other"
    elif change == "long_window":
        handler.env.E2E_CRON_DEADLINE_MS = str(START + 1_200_001)
    asyncio.run(handler.scheduled(controller, handler.env, None))
    handler.env.STATE_KV.put.assert_not_called()


def test_scheduled_time_does_not_require_minute_boundary(monkeypatch):
    handler, controller = worker(monkeypatch)
    controller.scheduledTime += 50
    asyncio.run(handler.scheduled(controller, handler.env, None))
    receipt = json.loads(handler.env.STATE_KV.put.call_args.args[1])
    assert receipt["scheduled_time_ms"] == START + 60_050


def test_http_cannot_trigger_or_read_receipts(monkeypatch):
    handler, _ = worker(monkeypatch)
    response = asyncio.run(handler.fetch(SimpleNamespace(url="https://invalid/__scheduled", method="POST")))
    assert response.status == 404
    handler.env.STATE_KV.put.assert_not_called()


def test_storage_failure_is_not_success(monkeypatch):
    handler, controller = worker(monkeypatch)
    handler.env.STATE_KV.put.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(handler.scheduled(controller, handler.env, None))
