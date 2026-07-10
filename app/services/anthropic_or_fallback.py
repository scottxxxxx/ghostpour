"""Anthropic-direct call wrapper with OpenRouter fallback.

When a request targeted at the `anthropic` provider fails (auth, rate
limit, 5xx, timeout, network), retry the same model through OpenRouter
so user-facing calls stay unblocked while we investigate. Every
fallback fires an operator email via the existing alerting infra
under category `anthropic_fallback_to_or`.

Fallback is only triggered on conditions where OR could plausibly
succeed:
- 401/403 (our Anthropic key is bad → OR has a different key)
- 429 (Anthropic rate limited us → OR may have headroom)
- 5xx (Anthropic infra blip → OR routes via a different path)
- Network / timeout (likewise)

NOT triggered on:
- 400 / 422 (request shape problem; OR wouldn't fix it)
- Any explicit Anthropic-side rejection that's about content/policy

Why route through OR even for a 401? Because the goal is "user query
keeps working while operator investigates," not "hide the auth issue."
The alert email lights up immediately; the user just doesn't notice.

Streaming considerations: the fallback only kicks in BEFORE any
tokens have flowed downstream. Once the SSE stream has started, we
can't reset and reissue without surfacing the failure. The wrapper
catches initial-connection errors only on the streaming path.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.models.chat import ChatRequest, ChatResponse

logger = logging.getLogger("ghostpour.anthropic_or_fallback")


# Anthropic native model id → OpenRouter model id.
# OR uses dotted versions and a vendor prefix. Add entries as new
# Anthropic models go into production routing.
_OR_MODEL_TRANSLATION: dict[str, str] = {
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-opus-4-7": "anthropic/claude-opus-4.7",
}


def translate_to_or_model_id(anthropic_model: str) -> str | None:
    """Look up the OR-compatible model id for a native Anthropic id.
    Returns None when we don't have a mapping — caller should NOT
    attempt fallback in that case (no point routing to OR if we don't
    know which OR model name to use)."""
    return _OR_MODEL_TRANSLATION.get(anthropic_model)


def _should_fallback(exc: Exception) -> bool:
    """Decide whether this exception is the kind OR could plausibly
    recover from. Keep narrow so we don't paper over real bugs."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, HTTPException):
        code = exc.status_code
        # Auth: OR has its own key → may succeed.
        # Rate limit: independent quota → may succeed.
        # 5xx: infrastructure flap → likely succeeds elsewhere.
        return code in (401, 403, 429) or 500 <= code < 600
    return False


async def _alert_on_fallback(
    db,
    settings: Settings,
    *,
    original_model: str,
    or_model: str,
    failure: Exception,
) -> None:
    """Fire an operator email so the underlying Anthropic issue gets
    investigated. Dedupe by subject so a sustained outage emails once
    per 30 minutes, not once per request."""
    try:
        from app.services.alerting import report_incident
        await report_incident(
            db,
            category="anthropic_fallback_to_or",
            subject=f"anthropic_call_failed_{type(failure).__name__}",
            details={
                "original_provider": "anthropic",
                "original_model": original_model,
                "fallback_provider": "openrouter",
                "fallback_model": or_model,
                "failure_type": type(failure).__name__,
                "failure_message": str(failure)[:500],
            },
            from_addr=settings.alert_email_from,
        )
    except Exception as e:
        # Alerting failure must not break the user-facing fallback.
        logger.warning("anthropic fallback alert dispatch failed: %s", e)


async def _or_request(request: ChatRequest, or_model: str) -> ChatRequest:
    """Clone a request to retarget OpenRouter with the translated id.
    Passthrough documents are flattened to extracted text first — the OR
    adapters don't render document blocks, so leaving them on the request
    would silently drop the attachment's content from the fallback answer."""
    from app.services.documents import flatten_documents_for_or

    flattened = await flatten_documents_for_or(request)
    return flattened.model_copy(update={
        "provider": "openrouter",
        "model": or_model,
    })


async def route_with_fallback(
    provider_router,
    request: ChatRequest,
    db,
    settings: Settings,
) -> ChatResponse:
    """Non-streaming wrapper around `provider_router.route()`. Use this
    in code paths that explicitly want the fallback behavior on
    Anthropic-direct calls."""
    if request.provider != "anthropic":
        return await provider_router.route(request)

    or_model = translate_to_or_model_id(request.model)
    if or_model is None:
        # No mapping → no fallback. Let the original call raise normally.
        return await provider_router.route(request)

    try:
        return await provider_router.route(request)
    except Exception as exc:
        if not _should_fallback(exc):
            raise
        logger.warning(
            "anthropic call failed (%s), falling back to OR model=%s",
            type(exc).__name__, or_model,
        )
        await _alert_on_fallback(
            db, settings,
            original_model=request.model, or_model=or_model, failure=exc,
        )
        return await provider_router.route(await _or_request(request, or_model))


async def route_stream_with_fallback(
    provider_router,
    request: ChatRequest,
    db,
    settings: Settings,
) -> AsyncIterator[dict]:
    """Streaming wrapper around `provider_router.route_stream()`. The
    fallback only kicks in on the INITIAL connection error (before any
    SSE event has flowed). Once we've sent the first event downstream,
    we're committed — a mid-stream failure surfaces as a stream error.

    Implementation: peek the first event, if the upstream raises before
    yielding anything, fall back. Otherwise pass through."""
    if request.provider != "anthropic":
        async for event in provider_router.route_stream(request):
            yield event
        return

    or_model = translate_to_or_model_id(request.model)
    if or_model is None:
        async for event in provider_router.route_stream(request):
            yield event
        return

    upstream = provider_router.route_stream(request)
    try:
        first_event = await upstream.__anext__()
    except StopAsyncIteration:
        # Empty stream from Anthropic — fall back like a normal failure.
        await _alert_on_fallback(
            db, settings,
            original_model=request.model, or_model=or_model,
            failure=RuntimeError("anthropic stream returned no events"),
        )
        async for event in provider_router.route_stream(await _or_request(request, or_model)):
            yield event
        return
    except Exception as exc:
        if not _should_fallback(exc):
            raise
        logger.warning(
            "anthropic stream init failed (%s), falling back to OR model=%s",
            type(exc).__name__, or_model,
        )
        await _alert_on_fallback(
            db, settings,
            original_model=request.model, or_model=or_model, failure=exc,
        )
        async for event in provider_router.route_stream(await _or_request(request, or_model)):
            yield event
        return

    # Anthropic stream is alive. Pass through, no fallback past this point.
    yield first_event
    async for event in upstream:
        yield event
