"""Workers KV と Durable Object を介した状態管理を検証する。"""

import asyncio
from types import SimpleNamespace

import state as state_module
from state import StateStore
from sync_lock_do import SyncCoordinator
from tests.fakes import (
    CamelCaseDurableObjectNamespace,
    DurableObjectNamespace,
    MemoryKV,
    make_durable_object,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_get_text_treats_javascript_null_as_missing() -> None:
    class JavaScriptNull:
        def __str__(self) -> str:
            return "jsnull"

    class JavaScriptNullKV:
        async def get(self, key: str) -> object:
            return JavaScriptNull()

    store = StateStore(SimpleNamespace(STATE_KV=JavaScriptNullKV()))

    assert run(store.get_text("missing")) is None


def test_json_round_trip_avoids_unchanged_write() -> None:
    kv = MemoryKV()
    store = StateStore(SimpleNamespace(STATE_KV=kv))

    first_write = run(store.put_json_if_changed("example", {"b": 2, "a": 1}))
    second_write = run(store.put_json_if_changed("example", {"a": 1, "b": 2}))

    assert first_write is True
    assert second_write is False
    assert run(store.get_json("example")) == {"a": 1, "b": 2}
    assert len(kv.put_calls) == 1


def test_google_message_dedupe_uses_kv_without_durable_object() -> None:
    store = StateStore(SimpleNamespace(STATE_KV=MemoryKV()))

    first = run(store.mark_google_message_seen("channel", "100"))
    second = run(store.mark_google_message_seen("channel", "100"))

    assert first is False
    assert second is True


def test_e2e_google_message_dedupe_uses_owned_durable_object_state() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    namespace = CamelCaseDurableObjectNamespace(coordinator)
    store = StateStore(
        SimpleNamespace(
            STATE_KV=MemoryKV(),
            SYNC_COORDINATOR=namespace,
            GCAL_DEDUPE_TTL_SECONDS="60",
        )
    )
    run_id = "E2E-20260902T100000Z-1234abcd"

    first = run(
        store.mark_e2e_google_message_seen(
            "e2e-webhook-channel",
            "100",
            run_id,
        )
    )
    duplicate = run(
        store.mark_e2e_google_message_seen(
            "e2e-webhook-channel",
            "100",
            run_id,
        )
    )
    cleared = run(
        store.clear_e2e_google_message_seen(
            "e2e-webhook-channel",
            "100",
            run_id,
        )
    )

    assert first is False
    assert duplicate is True
    assert cleared is True
    assert not any(key.startswith("gcal_msg:") for key in coordinator.ctx.storage.data)
    assert store.env.STATE_KV.put_calls == []


def test_sync_epoch_uses_durable_object_when_available(monkeypatch) -> None:
    coordinator = make_durable_object(SyncCoordinator())
    namespace = DurableObjectNamespace(coordinator)
    kv = MemoryKV({"sync:last_epoch": "1"})
    store = StateStore(
        SimpleNamespace(
            STATE_KV=kv,
            SYNC_COORDINATOR=namespace,
        )
    )
    monkeypatch.setattr(state_module.time, "time", lambda: 1234.5)

    run(store.set_sync_last_epoch_now())

    assert run(store.get_sync_last_epoch()) == 1234.5
    assert kv.data["sync:last_epoch"] == "1"
    assert namespace.requested_names == ["global", "global"]


def test_sync_epoch_supports_runtime_get_by_name(monkeypatch) -> None:
    coordinator = make_durable_object(SyncCoordinator())
    namespace = CamelCaseDurableObjectNamespace(coordinator)
    kv = MemoryKV({"sync:last_epoch": "1"})
    store = StateStore(
        SimpleNamespace(
            STATE_KV=kv,
            SYNC_COORDINATOR=namespace,
        )
    )
    monkeypatch.setattr(state_module.time, "time", lambda: 1234.5)

    run(store.set_sync_last_epoch_now())

    assert run(store.get_sync_last_epoch()) == 1234.5
    assert kv.data["sync:last_epoch"] == "1"
    assert namespace.requested_names == ["global", "global"]


def test_invalid_environment_numbers_fall_back_to_safe_defaults() -> None:
    env = SimpleNamespace(
        KV_RESULT_MIN_WRITE_SECONDS="invalid",
        GCAL_DEDUPE_TTL_SECONDS="invalid",
    )

    assert StateStore.result_write_min_interval_seconds(env) == 3600.0
    assert StateStore.google_message_dedupe_ttl_seconds(env) == 86400.0


def test_e2e_manifest_round_trip_uses_durable_object_not_kv() -> None:
    coordinator = make_durable_object(SyncCoordinator())
    namespace = CamelCaseDurableObjectNamespace(coordinator)
    kv = MemoryKV()
    store = StateStore(
        SimpleNamespace(
            STATE_KV=kv,
            SYNC_COORDINATOR=namespace,
        )
    )
    manifest = {
        "version": 1,
        "kind": "discord_event_message",
        "dirty": True,
        "run_id": "E2E-20260901T000000Z-1234abcd",
        "event_id": "temporary-resource-id",
    }

    run(store.put_e2e_manifest("discord", manifest))

    assert run(store.get_e2e_manifest("discord")) == manifest
    assert kv.put_calls == []
    assert "e2e:manifest:discord" in coordinator.ctx.storage.data


def test_e2e_manifest_fails_closed_without_durable_object() -> None:
    store = StateStore(SimpleNamespace(STATE_KV=MemoryKV()))

    assert store.e2e_manifest_enabled() is False
    try:
        run(store.get_e2e_manifest("google"))
    except RuntimeError as exc:
        assert str(exc) == "e2e_manifest_durable_object_required"
    else:
        raise AssertionError("Durable Objectなしのreadを拒否する必要がある")
    try:
        run(
            store.put_e2e_manifest(
                "google",
                {
                    "version": 1,
                    "kind": "google_calendar_event",
                    "dirty": False,
                    "last_run_id": "E2E-20260901T000000Z-1234abcd",
                },
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "e2e_manifest_durable_object_required"
    else:
        raise AssertionError("Durable Objectなしのwriteを拒否する必要がある")
