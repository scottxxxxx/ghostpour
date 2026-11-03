"""The generic interviewer lane (I-765 lead, 2026-10-29): `form_interviewer_turn`
serves every form after the I-765. The prompt is the lane text, which names no
form, followed by the FORM BLOCK that `metadata.form_code` picks. The block is
generated from the app's form definition by the app's own generator, vendored
under qa/form_blocks/ so CI can rebuild it without the app repo.

Pinned here: the served block IS the generator's output on the vendored
definition, and the vendored definition IS the one the app pinned. A
definition that moves without a rebuild, or a hand edit of the block, goes
red. The live I-765 stays on i765_interviewer_turn, untouched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.services.interviewer_lanes import (
    FORM_BLOCK_CALL_TYPES, INTERVIEWER_CALL_TYPES, form_block_slug, form_block_text)
from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG
from tests.conftest import chat_request
from tests.test_i765_interviewer_lane import ENVELOPE, _canned, _headers

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = ROOT / "qa" / "form_blocks"
I765_DIR = ROOT / "config" / "remote" / "i765"
CALL = "form_interviewer_turn"
# The I-765 app's pins (i765-app-06, 2026-10-29), from its definition at the
# vendored commit. Moving one is a re-vendor, never an edit.
PINS = {"form_definition_i131.json": "fd3989d462dee262d56d66f64d3e41e3193ec45215b1e87e8d53c6c6f4e07ede"}
AGENDA = "q_p1_item5a | Part 1: Application type | p1.item_5a | (opens the part)\n"


def _build():
    spec = importlib.util.spec_from_file_location("form_blocks_build", BLOCKS / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _metadata(**over):
    md = {"call_type": CALL, "form_code": "I-131", "jurisdiction": "US-TX", "locale": "en",
          "turn_id": "t-i131-1", "conversation": "APPLICANT: hi", "known_facts": "(none yet)",
          "agenda": AGENDA, "opening_questions": "[no opening questions]"}
    md.update(over)
    return {k: v for k, v in md.items() if v is not None}


def _turn(client, headers, **over):
    body = chat_request(system_prompt="", user_content="hi", metadata=_metadata(**over))
    return client.post("/v1/chat", json=body, headers=headers)


def _sent(mock_provider):
    return mock_provider.call_args.args[0] if mock_provider.call_args.args else mock_provider.call_args.kwargs["request"]


# --- the pins ---------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PINS))
def test_the_vendored_definition_is_the_one_the_app_pinned(name):
    assert hashlib.sha256((BLOCKS / name).read_bytes()).hexdigest() == PINS[name]


@pytest.mark.parametrize("name", sorted(PINS))
def test_the_served_block_is_the_generators_output_on_that_definition(name):
    build = _build()
    code = name.removeprefix("form_definition_").removesuffix(".json")
    cfg = json.loads((I765_DIR / f"form-block-{code}.json").read_text(encoding="utf-8"))
    assert cfg["block"] == build.served_block(str(BLOCKS / name)), "rebuild with qa/form_blocks/build.py"
    assert cfg["definition_sha256"] == PINS[name]
    assert cfg["server_only"] is True
    assert cfg["block"].startswith("## FORM BLOCK: Form "), "the people-only header comments are stripped"


def test_the_i765_is_not_served_on_the_generic_lane():
    """It moves only after a parity run and the N-400 lead's sign-off."""
    assert not (I765_DIR / "form-block-i765.json").exists()
    assert "form_definition_i765_tx.json" not in _build().SERVED


# --- the wiring -------------------------------------------------------------

def test_the_call_type_is_a_lane_owned_by_the_i765_app():
    assert CALL in INTERVIEWER_CALL_TYPES, "inherits the lane contract (cache, backstop, sentence stream)"
    assert _CALL_TYPE_TO_CONFIG[CALL] == "i765/interviewer-lane"
    assert FORM_BLOCK_CALL_TYPES[CALL] == "i765"
    for code in ("I-131", "i131", "I131", " I-131 "):
        assert form_block_slug(CALL, code) == "i765/form-block-i131", code
    assert form_block_slug(CALL, None) is None and form_block_slug(CALL, "") is None
    assert form_block_slug("i765_interviewer_turn", "I-131") is None


def test_no_block_is_none_never_the_lane_alone():
    assert form_block_text(CALL, "I-131", {"i765/form-block-i131": {"block": "  "}}) is None
    assert form_block_text(CALL, "I-131", {}) is None
    assert form_block_text(CALL, "I-131", {"i765/form-block-i131": {"block": "## FORM BLOCK"}}) == "## FORM BLOCK"


def test_the_lane_config_requires_opening_questions_and_names_no_form():
    cfg = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))
    assert cfg["server_only"] is True
    assert "opening_questions" in cfg["requiredVariables"], "review C7: the EMPTY state is gone"
    assert "opening_questions" not in cfg["optionalVariables"]
    assert "EMPTY means an older client" not in cfg["userPromptTemplate"]
    # M8: the client's exact suffix (InterviewerEnvelope.swift:48, tentativeKnownFactSuffix).
    assert "except a line ending (mentioned earlier, not yet confirmed), which is a proposal" in cfg["userPromptTemplate"]
    sp = cfg["systemPrompt"]
    assert "I-131" not in sp, "a literal form code in the lane would name the wrong form on another one"
    assert not re.search("[–—−]", sp + cfg["userPromptTemplate"])
    assert " - " not in sp


def test_an_opening_question_turn_names_no_node():
    # I-131 wire proof 2026-10-31 (i765-helper 0b6469e): the opening asked the
    # interpreter question but tagged the standing node, so the engine would
    # have read "yes, my cousin interprets" as a yes on the stop screen
    # (InterviewEngine laneAskedUnmappedQuestion needs asking null). The rule
    # sits where the opening is defined, not only in the asking paragraph.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    opening = sp.index("The opening turn's intent is control.")
    assert sp.index("While a BEFORE WE BEGIN question is the one asked, asking is null") == opening + len(
        "The opening turn's intent is control. ")


def test_a_hedge_on_a_field_without_unsure_reasks_with_clarification_null():
    # I-765 parity batch 1, 2026-10-31: on the c9 gate (no unsure option) the
    # generic lane put a clarification on a hedge re-ask 5 of 6 times, live 0
    # of 6. The engine re-parsed her words when a clarification came back with
    # nothing minted and read "no sé" as no (fixed client side, i765-helper
    # 5c8f419); the lane matches live anyway. It follows the unsure sentence
    # it is the other half of. Live itself is mixed on these hedges (SSN,
    # filed before); generic null against live mixed is intended (lead's
    # ruling). A value heard but unreadable keeps its clarification: Feb 31
    # held 3 of 3 on v2 and v3.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    unsure = "on a field that offers unsure, a hedge is not low confidence, it is unsure. "
    assert sp.index("On a field that does not offer unsure, a hedge is not low confidence either: it is "
                    "dont_know, its one re-ask of the same node first says") == sp.index(unsure) + len(unsure)
    assert "and then asks it again, carries clarification null," in sp


def test_the_one_reask_after_a_hedge_explains_from_the_block_only():
    # I-765 seam 18, lead's ruling 2026-11-03: live explained on the re-ask
    # after a hedge and the lane did not, and when it was told to, it reached
    # past the form ("su tarjeta"). The clause lets the explanation in only
    # from what the block says. Blind on a6be71a6: 27 of 27 explain first, 25
    # of 27 stay inside the block; the two SSN misses are accepted and logged.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    assert "first says in one short sentence what the question means, using only what the FORM BLOCK says " \
           "about this question and the field it leads to" in sp
    assert "explain only when they ask what the question means, or on the one re-ask after a hedge." in sp


def test_the_last_line_answered_counts_as_empty_and_the_offer_answer_settles_optional_rows():
    # Manager's ruling on CQ's notes, 2026-11-03: a phone-only contact answer
    # got "is that right?" instead of the review offer, then "yes" got "first,
    # what's your email?". The client keeps q_p3_contact on the agenda until
    # mobile and email are answered (lead's scripted test on build 15), so the
    # second sentence is what lets their choice stand. Blind on b4b3668e against
    # item 1 alone: the form on "go to the form" 3 to 9 of 9, the read-back on
    # "yes" 0 to 5 of 9.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    gate = 'AGENDA reads exactly "[agenda empty]" when nothing is left.'
    assert sp.index("The reply whose facts answer the last line on the agenda counts as empty, unless the answer "
                    "just opened rows: read the values back, then make the review offer below as the one question, "
                    "with asking null.") == sp.index(gate) + len(gate) + 1
    assert "A value you just read back is never the question." in sp
    assert "Once that offer has been made, their answer to it settles the optional rows still on the agenda: " \
           "do not ask them, do what they chose." in sp


def test_an_escalation_turn_files_nothing_and_reads_nothing_back():
    # I-765 seam 34, lead's ruling 2026-11-03: "512 555 0147. Do I have to
    # mention my DUI?" filed the phone and read it back on the turn that hands
    # them to an attorney. Now it asks for the value again, and never says
    # whether the form asks about the trigger. A stop answer is the exception.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    assert "That turn mints nothing and reads nothing back as filed, not even a value the same line gave, " \
           "except a stop answer" in sp
    assert "never says whether the form asks about the trigger, and asks the standing question again, " \
           "for that value too;" in sp
    assert "extract any facts the person did give you unless the turn carries an escalation" in sp


def test_two_whole_addresses_ask_once_and_a_non_pick_never_asks_again():
    # Scott's ruling through the I-765 side 2026-11-03: mention it, never act
    # as a judge. Two addresses for one line can both be true, so it asks once
    # which they want, and a line that does not pick is never asked again. A
    # line that does not take the question up has not answered, so it holds.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    assert "Ask once which one they want on the form (never \"which should I use\", never which is right, " \
           "and never about the state on file), and file nothing from either." in sp
    assert "on \"either one\" or the like, file the address they said last" in sp
    assert "on \"I don't know\" or the like, file nothing, leave what is on file, put each required field " \
           "of that line still empty in deferred (reason dont_know, partial_value null, origin applicant)" in sp
    assert "is a turn that answered nothing like any other: asking stays on the standing node" in sp


def test_a_digits_read_back_takes_the_one_format_the_client_can_restore():
    # Lane v4 (I-765 lead, 2026-11-02). The lane sees a same-shaped fake; the
    # client swaps the real value back by matching the fake's characters with
    # only [-,\\s().]{0,3} between them. "0, 1 y 8", digit words and a digit
    # spaced last four "0, 0, 5, 8" match nothing, and leftoverFakes uses the
    # same pattern, so the applicant would hear the FAKE as her own number
    # (run against the client's SurrogateBook, not just read). The bare "0058"
    # restores but breaks the one format, so last four is gone. The receipt has
    # no digits_only flag, so the rule names identifiers too. The live I-765
    # lane is untouched: a parity seam for when the I-765 moves.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    for copy in ('joined by a comma and a space and nothing else ("2, 0, 1, 4, 5, 6, 7, 8" for a number, '
                 '"M, S, C, 2, 1, 9, 0, 0, 1, 2, 3, 4, 5" for a receipt number)',
                 'never "and", "y" or "e" before the last character, never a period or a line break inside it',
                 'never only the last four',
                 'reads the number back in the read-back format below',
                 'read it back in the read-back format below',
                 'speak both candidates in the read-back format',
                 'each character joined by a comma and a space and nothing else.'):
        assert copy in sp, copy
    for gone in ("spoken groups", "512, 555, 0143", "whole or last four"):
        assert gone not in sp, gone
    live = json.loads((I765_DIR / "interviewer-turn.json").read_text(encoding="utf-8"))["systemPrompt"]
    assert "read-back format" not in live


def test_an_address_reads_back_whole_with_only_the_zip_in_the_format():
    # Same lead, same day. Blind, 3 of 9 replies spoke only the ZIP and then
    # asked "is that also where you live" about an address the applicant never
    # heard, with the whole thing already filed. The read-back is their one
    # chance to catch a wrong street or city. The first wording, "never spelled
    # out", lost the house number in 1 of 9 replies; the fake only restores whole
    # when its number arrives as digits.
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    for copy in ("An address is read back whole in the reply that files it, in one sentence",
                 "the street with its house number, unit, city and state in ordinary words",
                 "then the ZIP, and only the ZIP, in the read-back format",
                 "never the street without its number",
                 "the house number and unit are said whole, as written, never digit by digit."):
        assert copy in sp, copy


# Every copy of the unsure rule (2026-10-31 parity sabotage, qa/runs/
# i131-parity-sabotage-2026-10-31): cutting the paragraph alone left plain
# hedges filing unsure 9 of 9, because these restatements carry it; cutting
# all of them broke 6 of 9. So any one can go without a visible regression,
# and the last one going must fail here, not in front of a user.
UNSURE_COPIES = (
    "This rule applies ONLY to a field whose options segment includes unsure.",
    "the hedge is the answer: mint unsure and do not ask again.",
    "A field whose options include unsure is never deferred: doubt there is unsure.",
    "on a field that offers unsure, a hedge is not low confidence, it is unsure",
    "a field that offers unsure never gets a deferred entry",
    "(an unsure filed on a field that offers it is an answer)",
)


@pytest.mark.parametrize("copy", UNSURE_COPIES)
def test_every_copy_of_the_unsure_rule_is_still_served(copy):
    sp = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    assert sp.count(copy) == 1, copy


def test_the_dial_routes_the_lane_to_sonnet():
    routing = json.loads((ROOT / "config" / "remote" / "model-routing.json").read_text())
    assert routing["apps"]["i765"]["call_types"][CALL]["models"]["default"] == "anthropic/claude-sonnet-5"


# --- the route --------------------------------------------------------------

def test_a_turn_is_the_lane_then_the_block(client, tmp_db_path, mock_provider):
    h = _headers(tmp_db_path, user_id="form-lane-user")
    mock_provider.return_value = _canned(json.dumps(ENVELOPE))
    r = _turn(client, h)
    assert r.status_code == 200, r.text
    sent = _sent(mock_provider)
    lane = json.loads((I765_DIR / "interviewer-lane.json").read_text(encoding="utf-8"))["systemPrompt"]
    block = json.loads((I765_DIR / "form-block-i131.json").read_text(encoding="utf-8"))["block"]
    # The route's locale line (RESPONSE LANGUAGE) follows, as on every lane.
    assert sent.system_prompt.startswith(f"{lane}\n\n{block}\n\n--- RESPONSE LANGUAGE ---")
    assert "BEFORE WE BEGIN" in sent.user_content and "[no opening questions]" in sent.user_content


@pytest.mark.parametrize("form", ["I-765", "N-400", "I-130"])
def test_a_form_with_no_block_is_refused_before_anything_is_spent(client, tmp_db_path, mock_provider, form):
    h = _headers(tmp_db_path, user_id=f"form-lane-{form}")
    r = _turn(client, h, form_code=form)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "unknown_form"
    assert mock_provider.call_count == 0


def test_an_absent_form_code_is_refused_too(client, tmp_db_path, mock_provider):
    """Unlike the I-765 lane, where an absent form_code meant the one form:
    here there is no form to fall back to."""
    h = _headers(tmp_db_path, user_id="form-lane-none")
    r = _turn(client, h, form_code=None)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unknown_form"
    assert mock_provider.call_count == 0


def test_a_turn_without_opening_questions_is_a_422(client, tmp_db_path, mock_provider):
    h = _headers(tmp_db_path, user_id="form-lane-noq")
    r = _turn(client, h, opening_questions=None)
    assert r.status_code == 422, r.text
    assert "opening_questions" in r.json()["detail"]["details"]["missing"]
    assert mock_provider.call_count == 0


def test_another_app_cannot_call_the_lane(client, tmp_db_path, mock_provider):
    """A real N-400 session, token and header both n400, so the refusal is
    this guard and not authentication. (The first version reused the I-765
    token with an n400 header; it got a 401 whether or not the guard existed,
    and the sabotage that removed the guard left it green.)"""
    from app.services.jwt_service import JWTService
    from tests.test_i765_interviewer_lane import SECRET
    _headers(tmp_db_path, user_id="form-lane-n400")
    svc = JWTService(secret=SECRET, algorithm="HS256", access_expire_minutes=60, refresh_expire_days=30)
    h = {"Authorization": f"Bearer {svc.create_access_token('form-lane-n400', 'n400')}", "X-App-ID": "n400"}
    mock_provider.return_value = _canned(json.dumps(ENVELOPE))
    r = _turn(client, h)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "unknown_call_type"
    assert mock_provider.call_count == 0
