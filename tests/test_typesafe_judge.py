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


@pytest.fixture(autouse=True)
def recorded(monkeypatch):
    """Every row the code under test tries to record, and a fresh breaker.
    No unit test here may reach a real database or carry a breaker over."""
    rows: list[tuple[dict, str | None]] = []

    async def fake_record(row, app_id):
        rows.append((dict(row), app_id))

    monkeypatch.setattr(tj, "record", fake_record)
    monkeypatch.setattr(tj, "breaker", tj.Breaker())
    # `_LIVE` is module state and a task in it belongs to the event loop of
    # the test that made it. One left behind is gathered by a LATER test on a
    # different loop ("The future belongs to a different loop"), which is how
    # a route test here broke fourteen unrelated tests on CI's Python 3.12
    # while the same order passed on a local 3.14. Production has one loop.
    tj._LIVE.clear()
    yield rows
    tj._LIVE.clear()


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
    # The sleep(0) is load-bearing. A done-callback can start a NEW task (the
    # shadow's log step starts the record), and gather over a task that has
    # already finished returns WITHOUT yielding to the loop, so the callback
    # that would remove it from _LIVE never runs and this spins forever.
    for _ in range(200):
        if not tj._LIVE:
            break
        await asyncio.gather(*list(tj._LIVE), return_exceptions=True)
        await asyncio.sleep(0)
    assert not tj._LIVE, "background Jev work never finished"
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

def _settings(mode: str, key: str = "k"):
    return MagicMock(typesafe_mode=mode, typesafe_api_key=key)


@pytest.mark.asyncio
async def test_off_by_default_no_request_leaves(monkeypatch):
    from app.services.document_generation import interpret_offer_reply
    ask = AsyncMock(return_value=_answers())
    monkeypatch.setattr(tj, "ask", ask)
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("off"))
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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("shadow"))
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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("shadow"))
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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("shadow"))
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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("shadow"))
    caplog.set_level(logging.INFO, logger="ghostpour.typesafe_judge")
    router = MagicMock()
    router.route = AsyncMock(side_effect=RuntimeError("boom"))
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes")
    assert out["confirm"] is False
    await _drain()
    (line,) = _shadow_lines(caplog)
    assert "haiku_ok=False" in line


# --- the mode switch ---------------------------------------------------------------

@pytest.mark.parametrize("mode,key,expected", [
    ("off", "k", "off"), ("shadow", "k", "shadow"), ("primary", "k", "primary"),
    ("PRIMARY ", "k", "primary"),
    ("primary", "", "off"),        # no key is off, whatever the mode says
    ("on", "k", "off"),            # a typo must not start sending user text
    ("true", "k", "off"),
    (None, "k", "off"),
])
def test_only_a_defined_mode_with_a_key_turns_it_on(monkeypatch, mode, key, expected):
    from app.services.document_generation import _typesafe_mode
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(mode, key))
    assert _typesafe_mode()[0] == expected


# --- the breaker ---------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_two_failures_in_a_row_do_not_open_it_and_the_third_does():
    b = tj.Breaker(clock=_Clock())
    b.failure("X"); b.failure("X")
    assert b.allow() and not b.state()["open"]
    b.failure("X")
    assert not b.allow() and b.state()["open"]
    assert b.state()["opened_count_since_boot"] == 1


def test_a_success_between_failures_resets_the_run():
    b = tj.Breaker(clock=_Clock())
    b.failure("X"); b.failure("X"); b.success(); b.failure("X"); b.failure("X")
    assert b.allow()


def test_after_the_cooldown_one_probe_is_allowed_and_a_success_closes_it():
    clock = _Clock()
    b = tj.Breaker(cooldown=300, clock=clock)
    for _ in range(3):
        b.failure("X")
    clock.t += 299
    assert not b.allow()
    clock.t += 1
    assert b.allow()
    b.success()
    assert b.allow() and not b.state()["open"] and b.state()["consecutive_failures"] == 0


def test_a_failed_probe_starts_another_cooldown():
    clock = _Clock()
    b = tj.Breaker(cooldown=300, clock=clock)
    for _ in range(3):
        b.failure("X")
    clock.t += 300
    assert b.allow()
    b.failure("X")
    assert not b.allow()
    assert b.state()["opened_count_since_boot"] == 1   # still the same outage
    clock.t += 300
    assert b.allow()


# --- primary ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_primary_ok_returns_jevs_verdict(monkeypatch):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers(fmt=("xlsx", 0.9))))
    verdict, row = await tj.try_offer_reply("k", "OFFER: a docx file", "make it a spreadsheet", "docx", False)
    assert verdict == {"confirm": True, "format": "xlsx", "style": None, "version": None}
    assert row["outcome"] == "ok" and row["fell_back"] is False and row["input_tokens"] == 400


@pytest.mark.asyncio
async def test_an_unsure_jev_hands_over_and_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers(confirm=("accept", 0.43))))
    for _ in range(5):
        verdict, row = await tj.try_offer_reply("k", "OFFER: x", "keep it simple", "xlsx", False)
        assert verdict is None and row["outcome"] == "low_confidence" and row["fell_back"] is True
    # Five unsure answers in a row are five answers. The breaker stays closed.
    assert tj.breaker.allow() and tj.breaker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_an_error_and_a_timeout_are_failures_and_are_told_apart(monkeypatch):
    import httpx
    monkeypatch.setattr(tj, "ask", AsyncMock(side_effect=RuntimeError("boom")))
    verdict, row = await tj.try_offer_reply("k", "OFFER: x", "yes", "docx", False)
    assert verdict is None and row["outcome"] == "error" and row["error_type"] == "RuntimeError"
    monkeypatch.setattr(tj, "ask", AsyncMock(side_effect=httpx.ReadTimeout("slow")))
    verdict, row = await tj.try_offer_reply("k", "OFFER: x", "yes", "docx", False)
    assert verdict is None and row["outcome"] == "timeout" and row["error_type"] == "ReadTimeout"
    assert tj.breaker.consecutive_failures == 2


@pytest.mark.asyncio
async def test_after_three_failures_jev_is_not_asked_at_all(monkeypatch):
    ask = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(tj, "ask", ask)
    for _ in range(3):
        await tj.try_offer_reply("k", "OFFER: x", "yes", "docx", False)
    assert ask.call_count == 3
    verdict, row = await tj.try_offer_reply("k", "OFFER: x", "yes", "docx", False)
    assert verdict is None and row["outcome"] == "breaker_open" and row["fell_back"] is True
    assert ask.call_count == 3, "an open breaker must not call TypeSafe"


@pytest.mark.asyncio
async def test_primary_uses_the_short_timeout(monkeypatch):
    seen = {}

    async def fake_ask(api_key, state, questions, timeout=tj.TIMEOUT_SECONDS):
        seen["timeout"] = timeout
        return _answers()

    monkeypatch.setattr(tj, "ask", fake_ask)
    await tj.try_offer_reply("k", "OFFER: x", "yes", "docx", False)
    assert seen["timeout"] == tj.PRIMARY_TIMEOUT_SECONDS < tj.TIMEOUT_SECONDS


# --- primary inside interpret_offer_reply ------------------------------------------------

def _haiku_router(text='{"confirm": false, "format": null}'):
    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(text=text))
    return router


@pytest.mark.asyncio
async def test_in_primary_jev_decides_and_haiku_is_never_called(monkeypatch, recorded):
    from app.services.document_generation import interpret_offer_reply
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers()))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("primary"))
    router = _haiku_router()          # Haiku would say NO
    meter = AsyncMock()
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes",
                                      on_subcall=meter, app_id="shouldersurf")
    await _drain()
    assert out["confirm"] is True
    router.route.assert_not_called()
    meter.assert_not_called()
    assert [(r["outcome"], r["fell_back"], a) for r, a in recorded] == [("ok", False, "shouldersurf")]


@pytest.mark.asyncio
async def test_in_primary_a_jev_failure_falls_back_to_haiku_on_the_same_turn(monkeypatch, recorded):
    from app.services.document_generation import interpret_offer_reply
    monkeypatch.setattr(tj, "ask", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("primary"))
    router = _haiku_router('{"confirm": true, "format": "pdf"}')
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes as a pdf",
                                      app_id="shouldersurf")
    await _drain()
    assert out == {"confirm": True, "format": "pdf", "style": None, "version": None}
    router.route.assert_awaited_once()
    (row, app_id), = recorded
    assert row["outcome"] == "error" and row["fell_back"] is True and app_id == "shouldersurf"
    assert isinstance(row["fallback_ms"], int)


@pytest.mark.asyncio
async def test_in_primary_an_unsure_jev_lets_haiku_decide(monkeypatch, recorded):
    from app.services.document_generation import interpret_offer_reply
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers(confirm=("accept", 0.43))))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("primary"))
    router = _haiku_router('{"confirm": true, "format": null, "style": "simple"}')
    out = await interpret_offer_reply(router, {"format": "xlsx", "gist": "x"}, "keep it simple")
    await _drain()
    assert out["confirm"] is True and out["style"] == "simple"
    assert recorded[0][0]["outcome"] == "low_confidence"


@pytest.mark.asyncio
async def test_in_primary_an_open_breaker_goes_straight_to_haiku(monkeypatch, recorded):
    from app.services.document_generation import interpret_offer_reply
    ask = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(tj, "ask", ask)
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("primary"))
    for _ in range(4):
        router = _haiku_router('{"confirm": true, "format": null}')
        out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes")
        assert out["confirm"] is True          # every turn still got its answer
    await _drain()
    assert ask.call_count == 3
    assert [r["outcome"] for r, _ in recorded] == ["error", "error", "error", "breaker_open"]


@pytest.mark.asyncio
async def test_a_bug_in_the_primary_path_still_gives_the_turn_to_haiku(monkeypatch):
    from app.services.document_generation import interpret_offer_reply
    monkeypatch.setattr(tj, "try_offer_reply", AsyncMock(side_effect=KeyError("bug")))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("primary"))
    router = _haiku_router('{"confirm": true, "format": null}')
    out = await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes")
    assert out["confirm"] is True


@pytest.mark.asyncio
async def test_shadow_records_agreement_and_leaves_it_empty_when_haiku_failed(monkeypatch, recorded):
    from app.services.document_generation import interpret_offer_reply
    monkeypatch.setattr(tj, "ask", AsyncMock(return_value=_answers()))
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("shadow"))
    await interpret_offer_reply(_haiku_router('{"confirm": true, "format": null}'),
                                {"format": "docx", "gist": "x"}, "yes", app_id="a")
    router = MagicMock()
    router.route = AsyncMock(side_effect=RuntimeError("boom"))
    await interpret_offer_reply(router, {"format": "docx", "gist": "x"}, "yes", app_id="a")
    await _drain()
    assert [(r["mode"], r["agreed"]) for r, _ in recorded] == [("shadow", True), ("shadow", None)]
