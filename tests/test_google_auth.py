"""Google Calendar 認証が必要最小限の scope を要求することを検証する。"""

import asyncio
import json
from types import SimpleNamespace

from workers import Response

import google_auth
from google_auth import get_google_access_token
from state import StateStore


EXPECTED_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.calendars.readonly"
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_token_broker_requests_minimum_scopes(monkeypatch) -> None:
    env = SimpleNamespace(GOOGLE_TOKEN_BROKER_URL="https://broker.test/token")
    calls: list[dict] = []

    async def fake_fetch(url, options=None):
        calls.append({"url": url, **(options or {})})
        return Response(json.dumps({"access_token": "access-token"}), status=200)

    monkeypatch.setattr(google_auth, "fetch", fake_fetch)

    token = run(get_google_access_token(env, StateStore(env)))

    assert token == "access-token"
    assert json.loads(calls[0]["body"])["scope"] == EXPECTED_SCOPES


def test_service_account_requests_minimum_scopes(monkeypatch) -> None:
    env = SimpleNamespace(
        GOOGLE_SERVICE_ACCOUNT_JSON=json.dumps(
            {
                "client_email": "service-account@example.test",
                "private_key": "private-key-placeholder",
            }
        )
    )
    requested_scopes: list[str] = []

    async def fake_assertion(sa_info, scope):
        requested_scopes.append(scope)
        return "signed-assertion"

    async def fake_fetch(url, options=None):
        return Response(
            json.dumps({"access_token": "access-token", "expires_in": 3600}),
            status=200,
        )

    monkeypatch.setattr(google_auth, "_build_service_account_assertion", fake_assertion)
    monkeypatch.setattr(google_auth, "fetch", fake_fetch)

    token = run(get_google_access_token(env, StateStore(env)))

    assert token == "access-token"
    assert requested_scopes == [EXPECTED_SCOPES]
