"""The automation tier spends on the TEST Anthropic key (Scott, 2026-09-28).

On 2026-09-28 the harness rounds billed production's key: 1,985 test turns
($23.03) on the key real users spend on. Now a signed-in automation user's
Anthropic calls go out on `anthropic_test_api_key`, a separate key with its
own spend limit, and everyone else's stay on `anthropic_api_key`.

The flag is set in the auth dependency and read in the provider router, a
different task boundary on a streamed response, so these tests drive the
REAL app through /v1/chat and fake only the adapter's network call,
recording which key the adapter that made it was built with. Users alternate
in one app, because the router caches adapters for the life of the process
and a flag that leaked between requests would show up there.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.models.chat import ChatResponse
from app.services.providers.anthropic import AnthropicAdapter
from tests.conftest import _insert_user, _jwt_token

PROD, TEST = "prod-key-tail-TwAA", "test-key-tail-YwAA"


def _canned(**_):
    return ChatResponse(text="ok", input_tokens=10, output_tokens=5,
                        model="claude-sonnet-5", provider="anthropic",
                        usage={"input_tokens": 10, "output_tokens": 5})


@pytest.fixture
def keys_used(monkeypatch):
    used = []

    async def fake_send(self, request):
        used.append(self.api_key)
        return _canned()

    async def fake_stream(self, request):
        used.append(self.api_key)
        yield {"type": "text", "text": "ok"}
        yield {"done": True, "response": _canned()}

    monkeypatch.setattr(AnthropicAdapter, "send_request", fake_send)
    monkeypatch.setattr(AnthropicAdapter, "send_request_stream", fake_stream)
    return used


def _app(app_env, mock_pricing, test_key: str):
    os.environ["CZ_ANTHROPIC_API_KEY"] = PROD
    # Set, never popped: a popped env var falls through to a local .env that
    # may carry a real key, and the "no test key" case then passes on the
    # developer's machine for the wrong reason (it failed that way 2026-09-29).
    os.environ["CZ_ANTHROPIC_TEST_API_KEY"] = test_key
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _users(tmp_db_path):
    _insert_user(tmp_db_path, user_id="harness", tier="automation", monthly_limit=10.0)
    _insert_user(tmp_db_path, user_id="real-pro", tier="pro", monthly_limit=5.10)
    return {u: {"Authorization": f"Bearer {_jwt_token(u)}"} for u in ("harness", "real-pro")}


@pytest.fixture(autouse=True)
def _clean_env():
    yield
    for k in ("CZ_ANTHROPIC_API_KEY", "CZ_ANTHROPIC_TEST_API_KEY"):
        os.environ.pop(k, None)


def _chat(c, headers, stream=False):
    body = {"provider": "auto", "model": "auto", "system_prompt": "s",
            "user_content": "hi", "stream": stream}
    r = c.post("/v1/chat", json=body, headers=headers)
    if stream:
        r.read()
    return r


@pytest.mark.parametrize("stream", [False, True])
def test_the_harness_spends_on_the_test_key_and_users_on_production(
        app_env, mock_pricing, tmp_db_path, keys_used, stream):
    with _app(app_env, mock_pricing, TEST) as c:
        users = _users(tmp_db_path)
        for who in ("real-pro", "harness", "real-pro", "harness"):
            r = _chat(c, users[who], stream)
            assert r.status_code == 200, r.text
    assert keys_used == [PROD, TEST, PROD, TEST]


def test_without_a_test_key_the_harness_fails_rather_than_spend_production(
        app_env, mock_pricing, tmp_db_path, keys_used):
    with _app(app_env, mock_pricing, "") as c:
        users = _users(tmp_db_path)
        r = _chat(c, users["harness"])
        assert r.status_code == 502, r.text
        assert "test provider key" in r.text
        assert _chat(c, users["real-pro"]).status_code == 200
    assert keys_used == [PROD]


@pytest.mark.parametrize("stream", [False, True])
def test_a_failing_test_key_never_falls_back_to_openrouter(
        app_env, mock_pricing, tmp_db_path, monkeypatch, stream):
    """A capped test workspace answers with an error. GP's fallback would
    retry it on OpenRouter, production's account, so the cap would move the
    spend instead of stopping it. The harness gets the error; a real user
    with the same failure still falls back, which is what proves this test
    could see a fallback at all."""
    from fastapi import HTTPException
    from app.services.provider_router import ProviderRouter
    calls = []
    real_route, real_stream = ProviderRouter.route, ProviderRouter.route_stream

    async def failing(self, request):
        raise HTTPException(status_code=429, detail={"code": "provider_error", "message": "limit"})

    async def failing_stream(self, request):
        raise HTTPException(status_code=429, detail={"code": "provider_error", "message": "limit"})
        yield  # pragma: no cover

    async def route(self, request):
        calls.append(request.provider)
        if request.provider == "openrouter":
            return _canned()
        return await real_route(self, request)

    def route_stream(self, request):
        calls.append(request.provider)
        if request.provider == "openrouter":
            async def ok():
                yield {"type": "text", "text": "ok"}
                yield {"done": True, "response": _canned()}
            return ok()
        return real_stream(self, request)

    monkeypatch.setattr(AnthropicAdapter, "send_request", failing)
    monkeypatch.setattr(AnthropicAdapter, "send_request_stream", failing_stream)
    monkeypatch.setattr(ProviderRouter, "route", route)
    monkeypatch.setattr(ProviderRouter, "route_stream", route_stream)
    os.environ["CZ_OPENROUTER_API_KEY"] = "or-key"
    try:
        with _app(app_env, mock_pricing, TEST) as c:
            users = _users(tmp_db_path)
            _chat(c, users["harness"], stream)
            harness_calls = list(calls)
            calls.clear()
            _chat(c, users["real-pro"], stream)
    finally:
        os.environ.pop("CZ_OPENROUTER_API_KEY", None)
    assert harness_calls == ["anthropic"]
    assert calls == ["anthropic", "openrouter"]
