"""TypeSafe (Jev) offer-reply judgment and its shadow.

No test here touches the network: `typesafe_judge.ask` is replaced. What
is under test is OUR half: the reading of a typed answer into
`interpret_offer_reply`'s shape, the confidence floor, and that the shadow
can never change or delay a turn.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import typesafe_judge as tj


def _answers(confirm=("accept", 0.99), fmt=("keep", 0.99),
             style=("none", 0.99), version=None):
    out = {
        "confirm": {"type": "choice", "choice": confirm[0], "confidence": confirm[1]},
        "format": {"type": "choice", "choice": fmt[0], "confidence": fmt[1]},
        "style": {"type": "choice", "choice": style[0], "confidence": style[1]},
    }
    if version is not None:
        out["version"] = {"type": "choice", "choice": version[0], "confidence": version[1]}
    return {"model": tj.MODEL, "answers": out, "usage": {"input_tokens": 400}}


# --- reading a typed answer ------------------------------------------------

def test_accept_keeps_the_offered_format():
    verdict, conf = tj.read_offer_reply(_answers(), "docx")
    assert verdict == {"confirm": True, "format": "docx", "style": None, "version": None}
    assert conf["confirm"] == 0.99


def test_accept_with_a_revised_format():
    verdict, _ = tj.read_offer_reply(_answers(fmt=("xlsx", 0.9)), "docx")
    assert verdict["confirm"] is True and verdict["format"] == "xlsx"


def test_a_decline_never_carries_a_revised_format():
    # "no, not a spreadsheet" names a format while declining. Haiku's
    # contract is that format is the offered one whenever confirm is false.
    verdict, _ = tj.read_offer_reply(
        _answers(confirm=("decline", 0.99), fmt=("xlsx", 0.95)), "docx")
    assert verdict == {"confirm": False, "format": "docx", "style": None, "version": None}


def test_other_is_not_a_confirm():
    verdict, _ = tj.read_offer_reply(_answers(confirm=("other", 0.9)), "docx")
    assert verdict["confirm"] is False


def test_an_accept_under_the_floor_does_not_arm():
    # The spike's finding: a near tie is the honest answer to a hard case,
    # and a label read off a near tie is a coin flip that looks like a verdict.
    verdict, conf = tj.read_offer_reply(
        _answers(confirm=("accept", tj.CONFIDENCE_FLOOR - 0.01)), "docx")
    assert verdict["confirm"] is False
    assert conf["confirm"] == round(tj.CONFIDENCE_FLOOR - 0.01, 3)


def test_an_accept_exactly_at_the_floor_arms():
    verdict, _ = tj.read_offer_reply(
        _answers(confirm=("accept", tj.CONFIDENCE_FLOOR)), "docx")
    assert verdict["confirm"] is True


def test_a_format_under_the_floor_keeps_the_offered_one():
    verdict, _ = tj.read_offer_reply(_answers(fmt=("pdf", 0.3)), "docx")
    assert verdict == {"confirm": True, "format": "docx", "style": None, "version": None}


def test_style_and_version_are_read_and_floored():
    verdict, _ = tj.read_offer_reply(
        _answers(style=("detailed", 0.9), version=("workbook", 0.9)), "xlsx")
    assert verdict["style"] == "detailed" and verdict["version"] == "workbook"
    verdict, _ = tj.read_offer_reply(
        _answers(style=("detailed", 0.2), version=("custom", 0.2)), "xlsx")
    assert verdict["style"] is None and verdict["version"] is None


def test_an_empty_body_is_the_safe_direction():
    verdict, _ = tj.read_offer_reply({}, "pptx")
    assert verdict == {"confirm": False, "format": "pptx", "style": None, "version": None}


def test_the_version_question_is_asked_only_when_two_versions_were_offered():
    assert "version" not in tj.offer_reply_questions(lane_choice=False)
    assert "version" in tj.offer_reply_questions(lane_choice=True)


# --- the state we send -------------------------------------------------------

@pytest.mark.asyncio
async def test_the_state_is_the_offer_line_and_the_reply_and_nothing_else(monkeypatch):
    seen = {}

    async def fake_ask(api_key, state, questions, timeout=tj.TIMEOUT_SECONDS):
        seen["state"] = state
        return _answers()

    monkeypatch.setattr(tj, "ask", fake_ask)
    await tj.judge_offer_reply("k", "OFFER: a docx file for onboarding", "yes", "docx", False)
    assert seen["state"] == {"offer": "OFFER: a docx file for onboarding", "user_reply": "yes"}


# --- the shadow ---------------------------------------------------------------

async def _drain():
    while tj._LIVE:
        await asyncio.gather(*list(tj._LIVE), return_exceptions=True)
    await asyncio.sleep(0)


def _shadow_lines(caplog):
    return [r.getMessage() for r in caplog.records if "offer_reply_shadow" in r.getMessage()]


def test_no_key_means_no_shadow():
    assert tj.shadow_offer_reply("", "OFFER: x", "yes", "docx", False) is None


@pytest.mark.asyncio
async def test_agreement_is_logged_without_user_text(monkeypatch, caplog):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers()))
    caplog.set_level(logging.INFO, logger="ghostpour.typesafe_judge")
    finish = tj.shadow_offer_reply("k", "OFFER: a docx file", "SECRET-REPLY-WORDS", "docx", False)
    finish({"confirm": True, "format": "docx", "style": None, "version": None}, 900, True)
    await _drain()
    lines = _shadow_lines(caplog)
    assert len(lines) == 1
    assert "agree=True" in lines[0] and "diff=-" in lines[0] and "haiku_ok=True" in lines[0]
    assert "SECRET-REPLY-WORDS" not in lines[0] and "OFFER" not in lines[0]


@pytest.mark.asyncio
async def test_a_disagreement_names_the_field(monkeypatch, caplog):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers(confirm=("decline", 0.99))))
    caplog.set_level(logging.INFO, logger="ghostpour.typesafe_judge")
    finish = tj.shadow_offer_reply("k", "OFFER: a docx file", "nah", "docx", False)
    finish({"confirm": True, "format": "docx", "style": None, "version": None}, 900, True)
    await _drain()
    (line,) = _shadow_lines(caplog)
    assert "agree=False" in line and "diff=confirm " in line


@pytest.mark.asyncio
async def test_a_jev_failure_is_logged_and_raises_nowhere(monkeypatch, caplog):
    monkeypatch.setattr(tj, "ask", AsyncMock(side_effect=RuntimeError("boom")))
    caplog.set_level(logging.INFO, logger="ghostpour.typesafe_judge")
    finish = tj.shadow_offer_reply("k", "OFFER: a docx file", "yes", "docx", False)
    finish({"confirm": True, "format": "docx", "style": None, "version": None}, 900, True)
    await _drain()
    (line,) = _shadow_lines(caplog)
    assert "jev_failed=RuntimeError" in line


# --- the shadow inside interpret_offer_reply ------------------------------------

def _settings(enabled: bool, key: str = "k"):
    return MagicMock(typesafe_shadow_enabled=enabled, typesafe_api_key=key)


@pytest.mark.asyncio
async def test_off_by_default_no_request_leaves(monkeypatch):
    from app.services.document_generation import interpret_offer_reply
    ask = AsyncMock(return_value=_answers())
    monkeypatch.setattr(tj, "ask", ask)
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(False))
    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(text='{"confirm": true, "format": null}'))
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes")
    await _drain()
    assert out["confirm"] is True
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_the_shadow_cannot_change_the_verdict(monkeypatch, caplog):
    from app.services.document_generation import interpret_offer_reply
    # Jev says decline with full confidence; Haiku says yes. Haiku's verdict
    # is what the turn gets.
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers(confirm=("decline", 1.0))))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(True))
    caplog.set_level(logging.INFO, logger="ghostpour.typesafe_judge")
    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(text='{"confirm": true, "format": null}'))
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes")
    assert out == {"confirm": True, "format": "docx", "style": None, "version": None}
    await _drain()
    (line,) = _shadow_lines(caplog)
    assert "agree=False" in line


@pytest.mark.asyncio
async def test_the_turn_never_waits_on_jev(monkeypatch):
    from app.services.document_generation import interpret_offer_reply
    release = asyncio.Event()

    async def hung_ask(*a, **k):
        await release.wait()
        return _answers()

    monkeypatch.setattr(tj, "ask", hung_ask)
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(True))
    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(text='{"confirm": true, "format": null}'))
    out = await asyncio.wait_for(
        interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes"), timeout=1.0)
    assert out["confirm"] is True
    assert tj._LIVE, "the Jev task should still be in flight after the verdict returned"
    release.set()
    await _drain()


@pytest.mark.asyncio
async def test_both_judges_get_the_same_isolated_reply(monkeypatch):
    from app.services.document_generation import interpret_offer_reply
    seen = {}

    async def fake_ask(api_key, state, questions, timeout=tj.TIMEOUT_SECONDS):
        seen["state"] = state
        return _answers()

    monkeypatch.setattr(tj, "ask", fake_ask)
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(True))
    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(text='{"confirm": true, "format": null}'))
    assembled = "ATTACHED TEMPLATE: y Red/Yellow? lots of document text\nUser question: Yes"
    await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, assembled)
    await _drain()
    # The attachment text must not reach TypeSafe: only the isolated reply.
    assert seen["state"]["user_reply"] == "Yes"
    haiku_content = router.route.call_args.args[0].user_content
    assert haiku_content.endswith("USER REPLY: Yes")
    assert seen["state"]["offer"] == haiku_content.split("\n")[0]


@pytest.mark.asyncio
async def test_a_haiku_failure_is_marked_so_it_is_not_counted_against_jev(monkeypatch, caplog):
    from app.services.document_generation import interpret_offer_reply
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers()))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(True))
    caplog.set_level(logging.INFO, logger="ghostpour.typesafe_judge")
    router = MagicMock()
    router.route = AsyncMock(side_effect=RuntimeError("boom"))
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes")
    assert out["confirm"] is False
    await _drain()
    (line,) = _shadow_lines(caplog)
    assert "haiku_ok=False" in line
