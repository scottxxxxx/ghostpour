"""End-to-end coverage for the memory-nudge emission site in chat.py.

WHY THIS FILE EXISTS. The nudge gate was added 2026-09-08 with unit tests on
the pure discriminator, and a full-suite sabotage proved the CALL SITE had no
coverage at all: deleting the guard entirely, so the nudge is suppressed on
every surface, left 3907 tests green. A correct function that is never called
looks exactly like a correct function that is. These tests exercise the route.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import chat_request

_RECALL = "app.services.features.context_quilt_hook.cq.recall"
_EXCLUDED = {
    "context": "[PEOPLE] someone: owes you a thing",
    "matched_entities": [],
    "excluded": {"by_scope": {"meetings": 7}},
}


def _post(client, headers, **meta):
    with patch(_RECALL, new_callable=AsyncMock, return_value=dict(_EXCLUDED)):
        return client.post(
            "/v1/chat",
            json=chat_request(metadata=meta or None),
            headers=headers,
        )


def _cta_kind(resp):
    return ((resp.json().get("feature_state") or {}).get("cta") or {}).get("kind")


def test_live_session_turn_does_not_carry_the_memory_nudge(
    client, free_user, mock_provider
):
    """Scott, mid-recording on his iPad. The turn must succeed and carry no
    upsell."""
    r = _post(client, free_user["headers"], call_type="query")
    assert r.status_code == 200
    assert _cta_kind(r) != "memory_excluded_scope"


def test_legacy_project_chat_still_carries_the_memory_nudge(
    client, free_user, mock_provider
):
    """THE regression guard, and the proof the gate is actually wired.

    A client that has not migrated sends call_type=query INSIDE ProjectChat.
    If this goes silent, the suppression is over-broad and has eaten the
    surface where a project memory nudge belongs most.
    """
    r = _post(client, free_user["headers"],
              call_type="query", prompt_mode="ProjectChat")
    assert r.status_code == 200
    assert _cta_kind(r) == "memory_excluded_scope"
