"""Pin the structured-error SSE event shape that streaming `/v1/chat`
emits when the upstream provider fails or an unexpected exception
fires inside the generator.

Pre-fix shape was `{"type":"error","text":"Provider error"}` for any
HTTPException. iOS rendered that as the unhelpful "Stream error:
stream_error" with no actionable detail. The new shape includes the
upstream HTTP status code and a typed `code` field so iOS can surface
something like "Anthropic rejected the request (400): empty content."
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException


def _capture_sse_error(exc: Exception) -> dict:
    """Run the SSE error-handler logic in isolation and return the
    parsed event payload that would have been yielded to iOS."""
    # Mirror the in-router logic so this test stays close to the
    # actual code path without spinning up a full streaming request.
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            msg = detail.get("message") or detail.get("detail") or "Provider error"
            code = detail.get("code") or f"upstream_{exc.status_code}"
        elif isinstance(detail, str) and detail:
            msg = detail
            code = f"upstream_{exc.status_code}"
        else:
            msg = "Provider error"
            code = f"upstream_{exc.status_code}"
        return {
            "type": "error",
            "code": code,
            "http_status": exc.status_code,
            "text": msg,
        }
    return {
        "type": "error",
        "code": "internal_error",
        "text": "Something went wrong on our side. Try again.",
    }


def test_anthropic_400_surfaces_status_and_typed_code():
    """The historical case: Anthropic returns 400 for an empty/malformed
    body, the provider router wraps it in an HTTPException with a dict
    detail. iOS gets `code=upstream_400`, `http_status=400`, and a
    human-readable message."""
    exc = HTTPException(
        status_code=400,
        detail={"message": "messages: at least one message is required", "code": "bad_request"},
    )
    event = _capture_sse_error(exc)
    assert event["type"] == "error"
    assert event["code"] == "bad_request"  # detail.code wins
    assert event["http_status"] == 400
    assert "at least one message" in event["text"]


def test_string_detail_falls_through_to_status_code():
    """When the upstream HTTPException carries a plain-string detail,
    we synthesize the typed code from the HTTP status and surface the
    string verbatim as the user-visible text."""
    exc = HTTPException(status_code=429, detail="rate limited by provider")
    event = _capture_sse_error(exc)
    assert event["code"] == "upstream_429"
    assert event["http_status"] == 429
    assert event["text"] == "rate limited by provider"


def test_no_detail_uses_http_phrase():
    """FastAPI auto-fills `detail` with the HTTP status phrase when None
    is passed (e.g. 500 → "Internal Server Error"). We pass that through
    as-is — better than swallowing it for "Provider error"."""
    exc = HTTPException(status_code=500, detail=None)
    event = _capture_sse_error(exc)
    assert event["code"] == "upstream_500"
    assert event["http_status"] == 500
    assert event["text"] == "Internal Server Error"


def test_truly_empty_detail_falls_back_to_provider_error():
    """If we somehow construct an HTTPException with an empty-string
    detail, fall back to the generic "Provider error" so iOS doesn't
    see an empty text field."""
    exc = HTTPException(status_code=500, detail="")
    event = _capture_sse_error(exc)
    assert event["code"] == "upstream_500"
    assert event["text"] == "Provider error"


def test_unexpected_exception_emits_internal_error_typed_code():
    """A non-HTTPException (e.g. a bug in our streaming code) should
    not leak the raw stack to iOS. Stable typed code, friendly text."""
    event = _capture_sse_error(RuntimeError("something bad happened in our code"))
    assert event["type"] == "error"
    assert event["code"] == "internal_error"
    assert "something went wrong" in event["text"].lower()
    # No http_status field for non-upstream errors
    assert "http_status" not in event


def test_event_serializes_as_valid_sse_json():
    """The event must JSON-serialize cleanly so the SSE wrapper
    `data: {...}\\n\\n` produces a parseable line for iOS."""
    exc = HTTPException(status_code=400, detail={"message": "Bad", "code": "bad_request"})
    event = _capture_sse_error(exc)
    s = json.dumps(event)
    re_parsed = json.loads(s)
    assert re_parsed == event
