"""Anthropic → OpenRouter fallback wrapper tests.

Pins:
- model id translation table
- _should_fallback covers auth, rate limit, 5xx, network/timeout
- _should_fallback rejects 400/422 (real bugs, OR can't fix)
- route_with_fallback: success path passes through
- route_with_fallback: failure on Anthropic retries via OR with translated id
- route_with_fallback: unmapped model id raises original error, no retry
- route_with_fallback: non-Anthropic provider bypasses fallback entirely
- alert fires on fallback with the right category + subject
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models.chat import ChatRequest, ChatResponse
from app.services import anthropic_or_fallback as ph


def _request(provider: str, model: str) -> ChatRequest:
    return ChatRequest(
        provider=provider,
        model=model,
        system_prompt="x",
        user_content="y",
    )


def _response() -> ChatResponse:
    return ChatResponse(
        text="ok",
        input_tokens=1,
        output_tokens=1,
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def _settings() -> Settings:
    return Settings(
        jwt_secret="test-secret-key-that-is-long-enough-for-hs256-validation",
    )


# --- translation table ---------------------------------------------------


def test_translate_known_anthropic_ids():
    assert ph.translate_to_or_model_id("claude-haiku-4-5-20251001") == "anthropic/claude-haiku-4.5"
    assert ph.translate_to_or_model_id("claude-sonnet-4-6") == "anthropic/claude-sonnet-4.6"
    assert ph.translate_to_or_model_id("claude-opus-4-7") == "anthropic/claude-opus-4.7"


def test_translate_unknown_returns_none():
    assert ph.translate_to_or_model_id("claude-unicorn-99") is None


# --- _should_fallback ---------------------------------------------------


def test_should_fallback_on_5xx():
    assert ph._should_fallback(HTTPException(status_code=503)) is True
    assert ph._should_fallback(HTTPException(status_code=500)) is True


def test_should_fallback_on_auth():
    assert ph._should_fallback(HTTPException(status_code=401)) is True
    assert ph._should_fallback(HTTPException(status_code=403)) is True


def test_should_fallback_on_rate_limit():
    assert ph._should_fallback(HTTPException(status_code=429)) is True


def test_should_fallback_on_timeout():
    assert ph._should_fallback(httpx.TimeoutException("slow")) is True


def test_should_fallback_on_network_error():
    assert ph._should_fallback(httpx.NetworkError("dns")) is True


def test_should_not_fallback_on_bad_request():
    """400/422 = our bug. Don't paper over with a fallback."""
    assert ph._should_fallback(HTTPException(status_code=400)) is False
    assert ph._should_fallback(HTTPException(status_code=422)) is False


def test_should_not_fallback_on_arbitrary_value_error():
    assert ph._should_fallback(ValueError("oops")) is False


# --- route_with_fallback ------------------------------------------------


@pytest.mark.asyncio
async def test_route_passthrough_on_success():
    router = MagicMock()
    router.route = AsyncMock(return_value=_response())
    db = MagicMock()
    out = await ph.route_with_fallback(
        router, _request("anthropic", "claude-haiku-4-5-20251001"),
        db, _settings(),
    )
    assert out.text == "ok"
    router.route.assert_called_once()


@pytest.mark.asyncio
async def test_route_falls_back_to_or_on_5xx():
    router = MagicMock()
    router.route = AsyncMock(side_effect=[
        HTTPException(status_code=503),
        _response(),
    ])
    db = MagicMock()
    with patch.object(ph, "_alert_on_fallback", new=AsyncMock()) as alert:
        out = await ph.route_with_fallback(
            router, _request("anthropic", "claude-haiku-4-5-20251001"),
            db, _settings(),
        )
    assert out.text == "ok"
    # Second call should have been on OR with translated id.
    second_call = router.route.call_args_list[1].args[0]
    assert second_call.provider == "openrouter"
    assert second_call.model == "anthropic/claude-haiku-4.5"
    alert.assert_called_once()


@pytest.mark.asyncio
async def test_route_falls_back_on_401():
    router = MagicMock()
    router.route = AsyncMock(side_effect=[
        HTTPException(status_code=401),
        _response(),
    ])
    db = MagicMock()
    with patch.object(ph, "_alert_on_fallback", new=AsyncMock()) as alert:
        out = await ph.route_with_fallback(
            router, _request("anthropic", "claude-haiku-4-5-20251001"),
            db, _settings(),
        )
    assert out.text == "ok"
    alert.assert_called_once()


@pytest.mark.asyncio
async def test_route_does_not_fall_back_on_400():
    router = MagicMock()
    router.route = AsyncMock(side_effect=HTTPException(status_code=400))
    db = MagicMock()
    with patch.object(ph, "_alert_on_fallback", new=AsyncMock()) as alert:
        with pytest.raises(HTTPException):
            await ph.route_with_fallback(
                router, _request("anthropic", "claude-haiku-4-5-20251001"),
                db, _settings(),
            )
    alert.assert_not_called()
    router.route.assert_called_once()  # no retry


@pytest.mark.asyncio
async def test_unmapped_model_does_not_fall_back():
    """No translation means no fallback — let original error surface."""
    router = MagicMock()
    router.route = AsyncMock(side_effect=HTTPException(status_code=503))
    db = MagicMock()
    with patch.object(ph, "_alert_on_fallback", new=AsyncMock()) as alert:
        with pytest.raises(HTTPException):
            await ph.route_with_fallback(
                router, _request("anthropic", "claude-unicorn-99"),
                db, _settings(),
            )
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_non_anthropic_provider_bypasses_fallback():
    router = MagicMock()
    router.route = AsyncMock(return_value=_response())
    db = MagicMock()
    out = await ph.route_with_fallback(
        router, _request("openrouter", "deepseek/deepseek-v3.2-exp"),
        db, _settings(),
    )
    assert out.text == "ok"
    router.route.assert_called_once()


@pytest.mark.asyncio
async def test_alert_dispatch_failure_does_not_break_fallback():
    """Even if the alerting infra is down, the user-facing call still
    completes via OR. We just lose the email."""
    router = MagicMock()
    router.route = AsyncMock(side_effect=[
        HTTPException(status_code=503),
        _response(),
    ])
    db = MagicMock()
    with patch(
        "app.services.alerting.report_incident",
        new=AsyncMock(side_effect=RuntimeError("alert broken")),
    ):
        out = await ph.route_with_fallback(
            router, _request("anthropic", "claude-haiku-4-5-20251001"),
            db, _settings(),
        )
    assert out.text == "ok"


# --- _alert_on_fallback -------------------------------------------------


@pytest.mark.asyncio
async def test_alert_fires_with_right_category():
    captured = {}

    async def _stub(*args, **kwargs):
        captured["category"] = kwargs.get("category")
        captured["subject"] = kwargs.get("subject")
        captured["details"] = kwargs.get("details")
        class _R: incident_id="t"; is_new=True; emailed_to=[]; suppressed_reason=None
        return _R()

    with patch("app.services.alerting.report_incident", new=_stub):
        await ph._alert_on_fallback(
            MagicMock(), _settings(),
            original_model="claude-haiku-4-5-20251001",
            or_model="anthropic/claude-haiku-4.5",
            failure=HTTPException(status_code=503),
        )
    assert captured["category"] == "anthropic_fallback_to_or"
    assert "anthropic_call_failed" in captured["subject"]
    assert captured["details"]["original_provider"] == "anthropic"
    assert captured["details"]["fallback_provider"] == "openrouter"


# --- streaming ---------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_passthrough_on_success():
    router = MagicMock()
    async def _gen(req):
        yield {"text": "hello"}
        yield {"done": True, "response": _response()}
    router.route_stream = _gen
    db = MagicMock()
    events = []
    async for ev in ph.route_stream_with_fallback(
        router, _request("anthropic", "claude-haiku-4-5-20251001"),
        db, _settings(),
    ):
        events.append(ev)
    assert events[0] == {"text": "hello"}
    assert events[1]["done"] is True


@pytest.mark.asyncio
async def test_stream_falls_back_on_init_error():
    router = MagicMock()
    call_count = {"n": 0}

    async def _gen(req):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call (Anthropic) raises before any yield
            raise HTTPException(status_code=503)
            yield  # pragma: no cover (unreachable; tells Python this is a generator)
        else:
            yield {"text": "from OR"}
            yield {"done": True, "response": _response()}

    router.route_stream = _gen
    db = MagicMock()
    with patch.object(ph, "_alert_on_fallback", new=AsyncMock()) as alert:
        events = []
        async for ev in ph.route_stream_with_fallback(
            router, _request("anthropic", "claude-haiku-4-5-20251001"),
            db, _settings(),
        ):
            events.append(ev)
    assert events[0] == {"text": "from OR"}
    alert.assert_called_once()
