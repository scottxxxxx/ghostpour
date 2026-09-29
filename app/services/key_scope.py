"""Which Anthropic key a request spends on (Scott, 2026-09-28).

Production traffic spends on `anthropic_api_key`. The automation tier (the
TR and N-400 test harnesses) spends on `anthropic_test_api_key`, a separate
key in its own workspace with its own spend limit, so test rounds can never
eat production's budget and show up on their own line of the bill.

The flag is a ContextVar set by the auth dependency for every signed-in
request. Everything that request awaits, and every task it spawns, inherits
it, so the provider router, the stream, and the generated-file download all
see the same answer without passing the user through every call.

When the test key is not configured, a harness request FAILS rather than
quietly spending on production's key: a silent fallback is how test spend
landed on the production bill in the first place.
"""
from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException

TEST_TIERS = frozenset({"automation"})

_use_test_key: ContextVar[bool] = ContextVar("anthropic_use_test_key", default=False)


def mark(tier: str | None) -> None:
    """Called once per signed-in request, with the user's tier."""
    _use_test_key.set(tier in TEST_TIERS)


def using_test_key() -> bool:
    return _use_test_key.get()


def anthropic_key(settings) -> str:
    """The Anthropic key this request must spend on."""
    if not using_test_key():
        return settings.anthropic_api_key
    key = getattr(settings, "anthropic_test_api_key", "") or ""
    if not key:
        raise HTTPException(
            status_code=502,
            detail={"code": "provider_error",
                    "message": "The test provider key is not configured."},
        )
    return key
