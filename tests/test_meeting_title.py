"""Suggested meeting titles on the auto summary (2026-08-17).

The hole: a title was written by post session analysis OR the full
report, never by the auto summary, and analysis is deliberately SKIPPED
for meetings long enough to earn a report. So a meeting whose report
never arrives gets neither and nothing repairs it. A real 23 minute
meeting rendered as "Meeting Summary" while its own summary named QA
issues, latency and Service Now form availability.

The rule these encode, which came from the client and is the one way
this feature could make things worse: a SERVED title is authoritative
and skips their fallback, so a generic title we send is one that
renders. Absent beats generic. They have a date; they cannot recover
from "Weekly Sync".
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import meeting_title as mt


class TestGenericTitlesNeverReachTheClient:
    """The blocklist runs on OUR side, after the model. Compliance with
    a prompt is a hope, not a guarantee."""

    @pytest.mark.parametrize("title", [
        "Meeting Summary", "Summary", "Status Update", "Weekly Sync",
        "Team Meeting", "Project Discussion", "Meeting Notes",
        "Daily Standup", "Weekly Team Sync Meeting", "Check In",
        "Catch Up", "Monthly Review", "General Discussion",
        "Project Update", "The Meeting", "Notes", "Recap",
    ])
    def test_it_is_rejected(self, title):
        assert mt.clean_title(title) is None, (
            f"{title!r} would render as an authoritative title and the "
            "client would skip its own date fallback")

    @pytest.mark.parametrize("title", [
        "Latency and QA Blockers", "Cigna Demo Prep",
        "Pricing Model Rework", "Hardware Form Testing",
        "Una Project Status Update",   # a NAME plus filler still names it
        "Service Now Form Availability",
    ])
    def test_a_real_name_survives(self, title):
        assert mt.clean_title(title) == title

    def test_filler_around_a_real_word_is_kept(self):
        """Only a title made ENTIRELY of filler is a label. One
        distinguishing word is enough to tell meetings apart, which is
        the whole job."""
        assert mt.clean_title("Weekly Cigna Sync") == "Weekly Cigna Sync"


class TestTheFieldIsAbsentRatherThanWrong:
    def test_empty_and_junk_yield_nothing(self):
        for bad in ["", "   ", None, 42, {"a": 1}, "...", "-"]:
            assert mt.clean_title(bad) is None, bad

    def test_a_sentence_is_too_long_to_be_a_name(self):
        long = ("The team reviewed open QA issues and latency metrics "
                "and Service Now form availability problems")
        assert mt.clean_title(long) is None

    def test_surrounding_punctuation_is_stripped(self):
        assert mt.clean_title('  "Latency Blockers."  ') == "Latency Blockers"


class TestTheCallItself:
    def _router(self, text):
        r = AsyncMock()
        r.route.return_value = type("R", (), {"text": text})()
        return r

    def test_a_good_answer_becomes_the_title(self):
        got = asyncio.run(mt.suggest_title(
            self._router('{"title": "Latency and QA Blockers"}'),
            "The team reviewed open QA issues, latency metrics and form "
            "availability problems blocking hardware testing."))
        assert got == "Latency and QA Blockers"

    def test_a_generic_answer_is_dropped_even_though_the_model_gave_it(self):
        got = asyncio.run(mt.suggest_title(
            self._router('{"title": "Meeting Summary"}'),
            "The team reviewed open QA issues, latency metrics and form "
            "availability problems blocking hardware testing."))
        assert got is None

    def test_an_explicit_null_is_honoured(self):
        got = asyncio.run(mt.suggest_title(
            self._router('{"title": null}'),
            "We talked about a few things for a while and then stopped "
            "talking about them again later on that same day."))
        assert got is None

    def test_a_broken_model_answer_fails_open(self):
        for junk in ["not json at all", "", "{oops"]:
            assert asyncio.run(mt.suggest_title(
                self._router(junk),
                "The team reviewed open QA issues, latency metrics and "
                "form availability problems blocking testing.")) is None

    def test_a_provider_failure_fails_open(self):
        r = AsyncMock()
        r.route.side_effect = RuntimeError("provider down")
        assert asyncio.run(mt.suggest_title(
            r, "The team reviewed open QA issues, latency metrics and "
               "form availability problems blocking testing.")) is None

    def test_a_summary_too_thin_to_name_spends_no_call(self):
        """Do not pay a model to tell us there is nothing there."""
        r = self._router('{"title": "Something"}')
        assert asyncio.run(mt.suggest_title(r, "Short.")) is None
        assert not r.route.called

    def test_the_served_prompt_carries_no_dash_punctuation(self):
        """Composers are upstream of text_hygiene, so a dash we write
        into the model's own context is a dash it copies back."""
        for ch in ("—", "–"):
            assert ch not in mt._SYSTEM


class TestItReachesTheResponse:
    """Unit tests on clean_title prove the rejector works and would pass
    happily while the field never reached the wire at all. That exact
    mistake was made twice today, so this drives the real turn."""

    def _summary_turn(self, client, pro_user, title_json, **meta):
        from unittest.mock import patch
        from app.models.chat import ChatResponse

        summary = ChatResponse(
            text="The team reviewed open QA issues, latency metrics and "
                 "Service Now form availability problems blocking "
                 "hardware form testing. Latency was acceptable.",
            input_tokens=10, output_tokens=20, model="claude-sonnet-4-6",
            provider="anthropic", usage={}, raw_request_json="{}",
            raw_response_json="{}")
        titler = AsyncMock()
        titler.route.return_value = type("R", (), {"text": title_json})()

        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock, return_value=summary), \
             patch("app.services.meeting_title.suggest_title",
                   _make_fake(title_json)):
            return client.post("/v1/chat", json={
                "provider": "auto", "model": "auto",
                "user_content": "summarise this",
                "system_prompt": "You summarise meetings.",
                "metadata": {"call_type": "summary",
                             "prompt_mode": "AutoSummary", **meta},
            }, headers=pro_user["headers"])

    def test_a_summary_turn_carries_a_suggested_title(self, client, pro_user):
        r = self._summary_turn(client, pro_user,
                               '{"title": "Latency and QA Blockers"}')
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("suggested_title") == "Latency and QA Blockers", (
            "the field never reached the wire; the client keeps rendering "
            "its own fallback and the hole is still open")

    def test_a_generic_title_is_absent_from_the_wire(self, client, pro_user):
        r = self._summary_turn(client, pro_user, '{"title": "Meeting Summary"}')
        assert "suggested_title" not in r.json(), (
            "a generic title reached the client, which treats it as "
            "authoritative and skips its own date fallback")

    def test_an_ordinary_chat_turn_gets_no_title(self, client, pro_user):
        """Proves the GATE excluded it, not a failing title call. The
        stub returns a perfectly good title, so if the field appears the
        gate is open on every call type; if this test let the real
        titler run it would pass for the wrong reason (the call fails
        and the field is absent either way)."""
        from unittest.mock import patch
        from app.models.chat import ChatResponse
        resp = ChatResponse(text="sure thing", input_tokens=1, output_tokens=1,
                            model="claude-sonnet-4-6", provider="anthropic",
                            usage={}, raw_request_json="{}",
                            raw_response_json="{}")
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock, return_value=resp), \
             patch("app.services.meeting_title.suggest_title",
                   _make_fake('{"title": "Latency and QA Blockers"}')):
            r = client.post("/v1/chat", json={
                "provider": "auto", "model": "auto",
                "user_content": "hi", "system_prompt": "s",
                "metadata": {"call_type": "meeting_chat",
                             "prompt_mode": "PostMeetingChat"},
            }, headers=pro_user["headers"])
        assert "suggested_title" not in r.json(), (
            "a title was attached to an ordinary chat turn, so we are "
            "paying for a model call on every request")


def _make_fake(title_json):
    """Stub the MODEL answer, keep the REAL cleaning path.

    So the generic-title rejection under test is the shipping one, not a
    reimplementation of it that could drift.
    """
    async def _fake(provider_router, summary_text, on_subcall=None):
        import json as _json
        try:
            parsed = _json.loads(title_json)
        except Exception:
            return None
        return mt.clean_title(parsed.get("title"))
    return _fake
