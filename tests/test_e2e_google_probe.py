"""E2E専用Google Calendar CRUDプローブを外部通信なしで検証する。"""

import asyncio
import json
import re
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import unquote

from workers import Response

import e2e_entry
import e2e_google_probe
from e2e_entry import Default
from state import StateStore
from sync_lock_do import SyncCoordinator
from tests.fakes import (
    CamelCaseDurableObjectNamespace,
    FetchTypeErrorDurableObjectNamespace,
    JavaScriptProxyDurableObjectNamespace,
    MemoryKV,
    NestedAwaitableDurableObjectNamespace,
    Request,
    make_durable_object,
    make_sync_coordinator_namespace,
)


ROUTE_RUN_ID = "E2E-20260901T000000Z-1234abcd"
ROUTE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-E2E-Run-ID": ROUTE_RUN_ID,
}


def run(coroutine):
    return asyncio.run(coroutine)


def response_json(response: Response) -> dict:
    return json.loads(run(response.text()))


def make_worker(env) -> Default:
    worker = Default()
    worker.env = env
    return worker


def make_probe_env(
    *,
    calendar_id: str = "calendar@example.test",
    kv: MemoryKV | None = None,
    manifest: dict | None = None,
):
    manifests = {"google": manifest} if manifest is not None else None
    return SimpleNamespace(
        GOOGLE_CALENDAR_ID=calendar_id,
        STATE_KV=kv or MemoryKV(),
        SYNC_COORDINATOR=make_sync_coordinator_namespace(manifests),
    )


def install_google_api_stub(monkeypatch, *, delete_statuses: list[int] | None = None):
    events: dict[str, dict] = {}
    methods: list[str] = []
    pending_delete_statuses = list(delete_statuses or [204])

    async def fake_access_token(env, state):
        return "access-token"

    async def fake_fetch(url, options=None):
        request = options or {}
        method = str(request.get("method") or "GET")
        methods.append(method)

        if method == "POST":
            event = json.loads(str(request.get("body") or "{}"))
            event_id = str(event["id"])
            events[event_id] = dict(event)
            return Response(json.dumps(events[event_id]), status=200)

        event_id = unquote(url.split("/events/", 1)[1].split("?", 1)[0])
        if method == "GET":
            if event_id not in events:
                return Response("", status=404)
            return Response(json.dumps(events[event_id]), status=200)
        if method == "PUT":
            event = json.loads(str(request.get("body") or "{}"))
            events[event_id] = dict(event)
            return Response(json.dumps(events[event_id]), status=200)
        if method == "DELETE":
            status = pending_delete_statuses.pop(0) if pending_delete_statuses else 204
            if status < 300:
                events.pop(event_id, None)
            return Response("", status=status)
        raise AssertionError(f"想定外のHTTP method: {method}")

    monkeypatch.setattr(e2e_google_probe, "get_google_access_token", fake_access_token)
    monkeypatch.setattr(e2e_google_probe, "fetch", fake_fetch)
    return events, methods


def test_google_crud_probe_cleans_event_and_manifest(monkeypatch) -> None:
    events, methods = install_google_api_stub(monkeypatch)
    env = make_probe_env()

    result = run(
        e2e_google_probe.run_google_calendar_crud_probe(env, StateStore(env))
    )

    assert result["ok"] is True
    assert result["dirty"] is False
    assert re.fullmatch(r"E2E-\d{8}T\d{6}Z-[0-9a-f]{8}", result["run_id"])
    assert result["cleanup"] == {"ok": True, "attempts": 1}
    assert result["stages"] == {
        "create": 200,
        "read": 200,
        "update": 200,
        "read_updated": 200,
        "delete": 204,
    }
    assert methods == ["POST", "GET", "PUT", "GET", "GET", "DELETE"]
    assert events == {}
    manifest = run(StateStore(env).get_e2e_manifest("google"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False
    assert manifest["last_run_id"] == result["run_id"]
    assert manifest["stages"] == result["stages"]
    assert manifest["started_at"]
    assert manifest["completed_at"]
    assert set(manifest["resource_fingerprints"]) == {
        "calendar_id_sha256",
        "google_event_id_sha256",
    }
    assert "event_id" not in manifest
    assert "calendar_id_sha256" not in manifest


def test_google_crud_probe_retries_cleanup_and_blocks_next_run(monkeypatch) -> None:
    events, methods = install_google_api_stub(
        monkeypatch,
        delete_statuses=[500, 500, 500, 500],
    )
    env = make_probe_env()
    state = StateStore(env)

    failed = run(e2e_google_probe.run_google_calendar_crud_probe(env, state))

    assert failed["ok"] is False
    assert failed["dirty"] is True
    assert failed["cleanup"] == {"ok": False, "attempts": 4}
    assert methods[-8:] == ["GET", "DELETE"] * 4
    manifest = run(state.get_e2e_manifest("google"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is True
    assert manifest["event_id"] in events

    call_count = len(methods)
    blocked = run(e2e_google_probe.run_google_calendar_crud_probe(env, state))

    assert blocked == {
        "ok": False,
        "dirty": True,
        "error": "environment_dirty",
        "cleanup_required": True,
    }
    assert len(methods) == call_count


def test_google_probe_rejects_legacy_kv_manifest_before_external_call(monkeypatch) -> None:
    _, methods = install_google_api_stub(monkeypatch)
    kv = MemoryKV(
        {
            "e2e:google_calendar_crud": json.dumps(
                {"dirty": False, "event_id": "legacy-resource-id"}
            )
        }
    )
    env = make_probe_env(kv=kv)

    result = run(e2e_google_probe.run_google_calendar_crud_probe(env, StateStore(env)))

    assert result == {
        "ok": False,
        "dirty": True,
        "error": "legacy_e2e_manifest_review_required",
    }
    assert methods == []


def test_confirmed_cleanup_clears_dirty_manifest(monkeypatch) -> None:
    events, _ = install_google_api_stub(monkeypatch)
    events["event-id"] = {
        "id": "event-id",
        "extendedProperties": {
            "private": {"ie_event_bot_e2e_run": ROUTE_RUN_ID},
        },
    }
    env = make_probe_env(
        manifest={
            "version": 1,
            "kind": "google_calendar_event",
            "dirty": True,
            "run_id": ROUTE_RUN_ID,
            "calendar_id_sha256": sha256(b"calendar@example.test").hexdigest(),
            "event_id": "event-id",
        }
    )

    result = run(
        e2e_google_probe.cleanup_google_calendar_crud_probe(env, StateStore(env))
    )

    assert result == {
        "ok": True,
        "dirty": False,
        "action": "cleanup",
        "cleanup": {"ok": True, "attempts": 1},
    }
    manifest = run(StateStore(env).get_e2e_manifest("google"))
    assert isinstance(manifest, dict)
    assert manifest["dirty"] is False
    assert "event_id" not in manifest
    assert "calendar_id_sha256" not in manifest


def test_google_crud_probe_rejects_primary_calendar() -> None:
    env = make_probe_env(calendar_id="primary")

    result = run(
        e2e_google_probe.run_google_calendar_crud_probe(env, StateStore(env))
    )

    assert result == {
        "ok": False,
        "dirty": False,
        "error": "primary_calendar_forbidden",
    }


def test_confirmed_cleanup_rejects_calendar_mismatch() -> None:
    env = make_probe_env(
        calendar_id="different@example.test",
        manifest={
            "version": 1,
            "kind": "google_calendar_event",
            "dirty": True,
            "run_id": ROUTE_RUN_ID,
            "calendar_id_sha256": sha256(b"expected@example.test").hexdigest(),
            "event_id": "event-id",
        },
    )

    result = run(
        e2e_google_probe.cleanup_google_calendar_crud_probe(env, StateStore(env))
    )

    assert result == {
        "ok": False,
        "dirty": True,
        "error": "dirty_manifest_target_mismatch",
    }


def test_confirmed_cleanup_rejects_run_id_mismatch(monkeypatch) -> None:
    events, methods = install_google_api_stub(monkeypatch)
    events["event-id"] = {
        "id": "event-id",
        "extendedProperties": {
            "private": {"ie_event_bot_e2e_run": "different-run-id"},
        },
    }
    calendar_id = "calendar@example.test"
    env = make_probe_env(
        calendar_id=calendar_id,
        manifest={
            "version": 1,
            "kind": "google_calendar_event",
            "dirty": True,
            "run_id": ROUTE_RUN_ID,
            "calendar_id_sha256": sha256(calendar_id.encode()).hexdigest(),
            "event_id": "event-id",
        },
    )

    result = run(
        e2e_google_probe.cleanup_google_calendar_crud_probe(env, StateStore(env))
    )

    assert result["ok"] is False
    assert result["dirty"] is True
    assert result["error"] == "cleanup_target_mismatch"
    assert methods == ["GET"]
    assert "event-id" in events


def test_e2e_route_requires_auth_and_post(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(env, state, run_id=None):
        assert isinstance(run_id, str)
        calls.append(run_id)
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_calendar_crud_probe", fake_probe)
    env = SimpleNamespace(
        E2E_GOOGLE_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_DO_LOCK_ENABLED="false",
    )
    worker = make_worker(env)

    unauthorized = run(
        worker.fetch(Request("https://bot.test/admin/e2e/google-crud", method="POST"))
    )
    wrong_method = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="GET",
                headers=ROUTE_HEADERS,
            )
        )
    )
    missing_run_id = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers={"Authorization": "Bearer test-token"},
            )
        )
    )
    success = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert unauthorized.status == 401
    assert wrong_method.status == 405
    assert missing_run_id.status == 400
    assert response_json(missing_run_id) == {"ok": False, "error": "invalid_run_id"}
    assert success.status == 200
    assert response_json(success) == {"ok": True, "dirty": False}
    assert calls == [ROUTE_RUN_ID]


def test_e2e_route_supports_runtime_get_by_name(monkeypatch) -> None:
    async def fake_probe(env, state, run_id=None):
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_calendar_crud_probe", fake_probe)
    coordinator = make_durable_object(SyncCoordinator())
    namespace = CamelCaseDurableObjectNamespace(coordinator)
    env = SimpleNamespace(
        E2E_GOOGLE_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=namespace,
    )
    worker = make_worker(env)

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 200
    assert response_json(response) == {"ok": True, "dirty": False}
    assert namespace.requested_names == ["global", "global"]


def test_e2e_route_uses_official_method_on_javascript_proxy(monkeypatch) -> None:
    async def fake_probe(env, state, run_id=None):
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_calendar_crud_probe", fake_probe)
    coordinator = make_durable_object(SyncCoordinator())
    namespace = JavaScriptProxyDurableObjectNamespace(coordinator)
    env = SimpleNamespace(
        E2E_GOOGLE_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=namespace,
    )
    worker = make_worker(env)

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 200
    assert response_json(response) == {"ok": True, "dirty": False}
    assert namespace.requested_names == ["global", "global"]


def test_e2e_route_uses_rpc_when_stub_fetch_rejects_request_init(monkeypatch) -> None:
    async def fake_probe(env, state, run_id=None):
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_calendar_crud_probe", fake_probe)
    coordinator = make_durable_object(SyncCoordinator())
    namespace = FetchTypeErrorDurableObjectNamespace(coordinator)
    env = SimpleNamespace(
        E2E_GOOGLE_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=namespace,
    )
    worker = make_worker(env)

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 200
    assert response_json(response) == {"ok": True, "dirty": False}
    assert namespace.requested_names == ["global", "global"]


def test_e2e_route_unwraps_nested_rpc_awaitable(monkeypatch) -> None:
    async def fake_probe(env, state, run_id=None):
        return {"ok": True, "dirty": False}

    monkeypatch.setattr(e2e_entry, "run_google_calendar_crud_probe", fake_probe)
    coordinator = make_durable_object(SyncCoordinator())
    namespace = NestedAwaitableDurableObjectNamespace(coordinator)
    env = SimpleNamespace(
        E2E_GOOGLE_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=namespace,
    )
    worker = make_worker(env)

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 200
    assert response_json(response) == {"ok": True, "dirty": False}
    assert namespace.requested_names == ["global", "global"]


def test_e2e_route_reports_safe_lock_failure_stage() -> None:
    class FailingNamespace:
        def getByName(self, name: str):  # noqa: N802
            raise RuntimeError("runtime detail must stay internal")

    env = SimpleNamespace(
        E2E_GOOGLE_CRUD_ENABLED="true",
        INTERNAL_API_TOKEN="test-token",
        STATE_KV=MemoryKV(),
        SYNC_COORDINATOR=FailingNamespace(),
    )
    worker = make_worker(env)

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 503
    assert response_json(response) == {
        "ok": False,
        "error": "e2e_lock_unavailable",
        "lock_error": "do_acquire_exception",
        "lock_stage": "get_stub",
        "lock_error_type": "RuntimeError",
    }


def test_e2e_route_is_hidden_when_disabled() -> None:
    worker = make_worker(
        SimpleNamespace(
            E2E_GOOGLE_CRUD_ENABLED="false",
            INTERNAL_API_TOKEN="test-token",
        )
    )

    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/google-crud",
                method="POST",
                headers=ROUTE_HEADERS,
            )
        )
    )

    assert response.status == 404
    assert response_json(response) == {"ok": False, "error": "not_found"}


def test_e2e_entry_preserves_application_routes() -> None:
    worker = make_worker(
        SimpleNamespace(
            E2E_GOOGLE_CRUD_ENABLED="true",
            STATE_KV=MemoryKV(),
        )
    )

    response = run(worker.fetch(Request("https://bot.test/health")))

    assert response.status == 200
    assert response_json(response) == {"ok": True, "kv_state_enabled": True}
