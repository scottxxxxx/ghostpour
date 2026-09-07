"""The served n400_interviewer_turn prompt config, and what must stay true of it.

The conversation-owning lane (Scott, 2026-09-04: all conversational
intelligence lives in GP). Same discipline as the extractor lane's file:
properties that fail for a real reason, not "the prompt mentions X". The
one source-text family kept on purpose is the DECODER CONTRACT, the JSON
keys and the intent vocabulary the client parses, because those are wire
tokens rather than prose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).parent.parent
_CONFIG = _ROOT / "config" / "remote" / "n400" / "interviewer-turn.json"
_ROUTING = _ROOT / "config" / "remote" / "model-routing.json"
_DOSSIER = _ROOT / "docs" / "prompt-dossiers" / "n400-interviewer-turn.md"

# Every placeholder the client is expected to fill, from the contract's
# request table. `user_input` is the request payload; the rest are read by
# name from request metadata.
_DECLARED = {
    "form_code", "jurisdiction", "locale", "turn_id", "case_id",
    "conversation", "known_facts", "agenda", "section_boundary",
    "applicant_context", "volunteer_fields", "spoken_numerals",
    "opening_questions", "user_input",
}
_REQUIRED = {"form_code", "jurisdiction", "locale", "turn_id",
             "conversation", "known_facts", "agenda"}
_OPTIONAL = {"case_id", "section_boundary", "applicant_context",
             "volunteer_fields", "spoken_numerals", "opening_questions"}

# v27: the client's marker for "there are no before-we-begin questions".
# A literal token, not an empty string, and the reason is mechanical rather
# than stylistic: see test_absent_and_none_are_different_prompts.
_NO_OPENING = "[no opening questions]"

# The contract's intent vocabulary, verbatim. The client journals these and
# the reviewer grades against them.
_INTENTS = (
    "answer", "partial_answer", "volunteered_extra", "dont_know",
    "help_explain", "repeat", "question_back", "legal_question", "control",
    "correction", "off_topic", "small_talk", "language_switch", "noise",
)
_RESPONSE_KEYS = (
    "schema_version", "turn_id", "intent", "reply", "facts", "asking",
    "clarification", "conflict", "section_checkpoint", "escalation",
    "deferred", "complete", "interview_over",
)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return json.loads(_CONFIG.read_text())


def test_the_config_would_actually_load(cfg):
    assert isinstance(cfg.get("version"), int)
    assert cfg.get("server_only") is True
    assert cfg["systemPrompt"].strip()
    assert cfg["userPromptTemplate"].strip()


def test_every_placeholder_is_one_the_client_was_told_about(cfg):
    used = set(re.findall(r"\{\{(\w+)\}\}", cfg["userPromptTemplate"]))
    assert used == _DECLARED, (
        f"undeclared: {used - _DECLARED}; declared but unused: {_DECLARED - used}")


def test_required_and_optional_partition_the_metadata_variables(cfg):
    """Every metadata placeholder is either required (422 without it) or
    declared optional (blanks cleanly). One in neither set survives into the
    prompt as literal braces when the client omits it."""
    assert set(cfg["requiredVariables"]) == _REQUIRED
    assert set(cfg["optionalVariables"]) == _OPTIONAL
    assert _REQUIRED | _OPTIONAL | {"user_input"} == _DECLARED
    assert not (_REQUIRED & _OPTIONAL)


def test_the_payload_is_not_in_the_required_list(cfg):
    assert "user_input" not in cfg["requiredVariables"]


def test_the_conversation_and_agenda_cannot_be_omitted(cfg):
    """The two inputs that make this lane different from the extractor. A
    turn assembled without them is the old lane with a longer prompt."""
    assert {"conversation", "agenda"} <= set(cfg["requiredVariables"])


def test_the_decoder_contract_is_spelled_out(cfg):
    """The client decodes these tokens. A missing one is a response the
    client cannot parse, and the model would look correct."""
    sp = cfg["systemPrompt"]
    for key in _RESPONSE_KEYS:
        assert f'"{key}"' in sp, key
    for intent in _INTENTS:
        assert f'"{intent}"' in sp, intent


def test_the_never_complete_a_missing_piece_rule_carries_over(cfg):
    """The load-bearing rule from the extractor lane (v3), kept verbatim: the
    worked example is the sentence the rule is remembered by."""
    sp = cfg["systemPrompt"]
    assert '"12, 1987." is a day and a year with no month. It is NOT April' in sp
    assert "verbatim span" in sp


def test_thinking_is_disabled_so_the_reply_cannot_be_starved(cfg):
    """Live rows 2026-09-05 03:32Z: two of the first seven turns hit the
    2048 cap inside Sonnet 5's default thinking block, one with no text at
    all, logged as success. `thinking: disabled` keeps the whole budget for
    the JSON reply, the same guard tr_counterpart_turn carries. Checked
    through the real assembler so the key has to reach the request, not
    merely sit in the file."""
    from app.services.prompt_assembly import assemble_prompt

    assert cfg["thinking"] == "disabled"
    out = assemble_prompt(
        "n400_interviewer_turn", "[start of interview]",
        {"n400/interviewer-turn": cfg},
        variables={
            "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
            "turn_id": "t_001", "conversation": "[start of interview]",
            "known_facts": "nothing yet", "agenda": "q | Part 1 | f | Q?",
        },
    )
    assert out["thinking"] == "disabled"


def test_the_few_shot_corpus_stays_empty_until_it_is_real(cfg):
    for shot in cfg.get("fewShots") or []:
        assert (shot.get("utterance") or "").strip(), (
            "a few-shot example with no source utterance is invented speech")


@pytest.mark.parametrize("field", ["systemPrompt", "userPromptTemplate"])
def test_no_dash_punctuation_reaches_the_model(cfg, field):
    text = cfg[field]
    assert "—" not in text, "em dash in served prompt"
    assert "–" not in text, "en dash in served prompt"


def test_no_dash_punctuation_anywhere_in_the_document(cfg):
    blob = json.dumps(cfg, ensure_ascii=False)
    assert "—" not in blob and "–" not in blob


def test_every_required_variable_is_actually_used_by_the_template(cfg):
    used = set(re.findall(r"\{\{(\w+)\}\}", cfg["userPromptTemplate"]))
    assert set(cfg["requiredVariables"]) <= used


def test_the_jurisdiction_axis_exists_and_is_empty(cfg):
    juris = cfg["jurisdictions"]
    assert isinstance(juris, dict)
    for key, override in juris.items():
        assert isinstance(override, dict), f"{key} must be a partial config"
        assert override, f"{key} overrides nothing"


# --- assembly through the real function -------------------------------------

def test_an_optional_variable_the_client_omits_blanks_rather_than_leaking(cfg):
    """Run the ACTUAL assembler. section_boundary is absent on most turns;
    the prompt must not reach the model with '{{section_boundary}}' in it."""
    from app.services.prompt_assembly import assemble_prompt

    configs = {"n400/interviewer-turn": cfg}
    out = assemble_prompt(
        "n400_interviewer_turn", "[start of interview]", configs,
        variables={
            "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
            "turn_id": "t_001", "conversation": "[start of interview]",
            "known_facts": "nothing yet",
            "agenda": "q_p1_basis | Part 1: Eligibility | p1.eligibility_basis | Why are you eligible?",
        },
    )
    assert out is not None
    assert "{{" not in out["user_content"], out["user_content"]
    assert "[start of interview]" in out["user_content"]
    assert out["max_tokens"] == cfg["maxTokens"]


# --- v27: the before-we-begin set is the client's, not the prompt's --------
#
# The auditor's ledger #23. The lane named four questions in its own text;
# the client now sends the set. Three states, and the whole design rests on
# them staying DISTINGUISHABLE inside the assembled prompt:
#
#   absent            an older client that does not send the field at all
#   [no opening ...]  a current client saying the set is empty
#   lines             the set, one question per line, in the interview locale
#
# prompt_assembly blanks an absent optional placeholder, so "absent" and
# "sent as an empty string" both arrive as an empty block. That is exactly
# why the empty SET carries a literal marker instead. If someone ever
# "simplifies" the client to send "" for none, these tests go red.

def _assemble(cfg, **extra):
    """The real assembler, with the required bag filled in."""
    from app.services.prompt_assembly import assemble_prompt

    variables = {
        "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
        "turn_id": "t_001", "conversation": "[start of interview]",
        "known_facts": "nothing yet",
        "agenda": "q_p1_basis | Part 1: Eligibility | p1.eligibility_basis | Why are you eligible?",
    }
    variables.update(extra)
    out = assemble_prompt("n400_interviewer_turn", "[start of interview]",
                          {"n400/interviewer-turn": cfg}, variables=variables)
    assert out is not None
    return out["user_content"]


def test_an_older_client_that_never_sends_the_set_still_assembles(cfg):
    """Optional, not required. If this became required, every turn from a
    client that has not shipped the field would raise instead of running."""
    assert "opening_questions" not in cfg["requiredVariables"]
    body = _assemble(cfg)
    assert "{{opening_questions}}" not in body
    assert "{{" not in body, body


def test_the_questions_the_client_sends_reach_the_prompt_verbatim(cfg):
    asked = ("Are you completing this for yourself?\n"
             "Will you use an interpreter?\n"
             "Do you have your Green Card with you?")
    body = _assemble(cfg, opening_questions=asked)
    for line in asked.splitlines():
        assert line in body, f"question dropped on the way to the model: {line}"


def test_the_empty_set_marker_reaches_the_prompt(cfg):
    """⚠ The obvious version of this test CANNOT FAIL, and sabotage is the
    only reason I know. `assert _NO_OPENING in body` stayed green with
    {{opening_questions}} deleted from the template outright, because the
    template's own LABEL names the marker to explain what it means. The
    check was reading the label and reporting it as the value.

    So compare against the same assembly without the variable: the marker
    must appear one MORE time when it is sent, and it must appear under the
    heading rather than only inside the parenthetical."""
    absent = _assemble(cfg)
    body = _assemble(cfg, opening_questions=_NO_OPENING)
    assert body.count(_NO_OPENING) == absent.count(_NO_OPENING) + 1, (
        "the marker did not arrive as a VALUE; this test is reading the "
        "template's explanatory label back to itself")
    block = body[body.index("BEFORE WE BEGIN"):]
    after_heading = block.split("\n", 1)[1]
    assert after_heading.lstrip().startswith(_NO_OPENING)


def test_absent_and_none_are_different_prompts(cfg):
    """The load-bearing one. 'ask nothing' and 'ask the four you know' are
    different instructions, so they must not assemble to the same bytes.
    Sabotage check: make the client send "" for the empty set and this is
    the test that goes red."""
    absent = _assemble(cfg)
    none = _assemble(cfg, opening_questions=_NO_OPENING)
    assert absent != none, (
        "an empty set and an older client produce an identical prompt, so "
        "the lane cannot tell 'ask nothing' from 'fall back to the four'")
    empty_string = _assemble(cfg, opening_questions="")
    assert empty_string == absent, (
        "sending an empty string is indistinguishable from not sending the "
        "field; that is why the contract specifies a literal marker")


def test_the_prompt_tells_the_model_what_each_state_means(cfg):
    """Source-text, and deliberately so: this contract lives in the prompt,
    there is no other artifact that carries it. Scoped to the opening
    section rather than the whole 56k document so it cannot pass on a
    coincidental mention somewhere else."""
    sp = cfg["systemPrompt"]
    start = sp.index("OPENING THE INTERVIEW")
    section = sp[start:sp.index("HOW TO TALK", start)]
    assert "BEFORE WE BEGIN" in section
    assert _NO_OPENING in section
    assert "EMPTY" in section
    # The four survive ONLY as the older-client fallback. If the prompt ever
    # goes back to naming them unconditionally, the client's set stops
    # governing and nobody would see it from the wire.
    assert "interpreter" in section
    assert section.index(_NO_OPENING) < section.index("The four")


def test_the_template_says_which_state_an_empty_block_is(cfg):
    """The model reads the template on every turn and the system prompt
    once. An empty block with no label there is silent about which of the
    two silences it is."""
    t = cfg["userPromptTemplate"]
    assert "{{opening_questions}}" in t
    assert _NO_OPENING in t
    assert "EMPTY" in t


# --- v27: two rules about what the lane may SAY ----------------------------
#
# The auditor's ledger asks 1 and 2. Both are prompt rules rather than new
# response fields, on their reasoning: both teams have shipped guards on
# fields the applicant cannot see, the guards worked, and she was harmed
# anyway by a sentence she could hear.
#
# These are source-text assertions and that family has been proven blind
# before, so each one is scoped to the section that must carry it and
# asserts a relationship (this literal, inside this section, ahead of that
# one) rather than "the prompt mentions closing".

_AGENDA_EMPTY = "[agenda empty]"


def _section(cfg, header: str) -> str:
    """One top-level section of the system prompt, by its ALL CAPS header.

    Scoping matters: the document is 58k characters and a bare `in` check
    against the whole of it passes on any coincidental mention, which is how
    a knowledge-pack test once fired on the pack's own warning sentence.

    ⚠ Anchors on the header as a WHOLE LINE, not as a substring. The
    substring version silently mispointed the moment a v28 rule referred to
    "the DEFERRALS section" from inside SECTION CHECKPOINTS: `.index()` took
    that earlier mention, and every assertion about the real section then
    ran against the wrong slice. A locator that can be captured by prose
    about the thing it locates is not a locator.
    """
    sp = cfg["systemPrompt"]
    m = re.search(r"^%s$" % re.escape(header), sp, re.M)
    assert m, f"no section header line {header!r}"
    start = m.start()
    nxt = re.compile(r"^[A-Z][A-Z0-9 ,'`-]{6,}$", re.M)
    n = nxt.search(sp, m.end())
    return sp[start:n.start() if n else len(sp)]


def test_the_empty_agenda_literal_is_the_one_on_the_wire(cfg):
    """`[agenda empty]` is not a token I chose. It was read off 2359 logged
    turns, 95 of which carry exactly it. The rule below is only worth
    anything while the prompt names the SAME string the client sends, so if
    the client ever changes it, this is where it surfaces."""
    assert _AGENDA_EMPTY in cfg["systemPrompt"]


def test_closing_language_is_gated_on_the_agenda(cfg):
    """Ask 1, and the important one. The lane must not be able to say the
    interview is finished while the agenda it was handed is non-empty.

    #922 already refuses the interview_over FLAG in that state
    (clear_interview_over_while_agenda_open). That guard cannot reach this
    defect: eight of the ten measured cases spoke a closing SENTENCE, and
    the worst run in the project never set the flag at all."""
    section = _section(cfg, "SECTION CHECKPOINTS")
    assert "CLOSING LANGUAGE IS GATED ON THE AGENDA" in section
    assert _AGENDA_EMPTY in section
    # It has to govern the spoken line. A rule that only talks about the
    # flag is the guard we already have, restated.
    assert "SPOKEN LINE" in section
    # And it has to say what to do instead, or it is a prohibition with no
    # exit and the model picks one.
    assert "ENDS ON A QUESTION" in section


def test_the_close_rule_does_not_ban_the_section_checkpoint(cfg):
    """The first draft of the close rule banned the mechanism it was meant to
    protect, and no test I wrote would have caught it.

    It said the reply may not imply "the interview, the sections, or the form"
    are complete. A section checkpoint says ONE part is done and ends on its
    confirmation question, deliberately with a non-empty agenda, and it is the
    mechanism the whole read-back walk is built on. conf-es-full-1 t64 is the
    real case: Part 8 confirmed with "esta todo completo y correcto?", closing
    flags set, agenda non-empty, and entirely legitimate.

    Caught by the auditor reading the rule against that turn. So this test
    pins the carve-out AND pins that the over-broad wording cannot come back,
    because the failure mode is a plausible-looking edit, not a deletion."""
    section = _section(cfg, "SECTION CHECKPOINTS")
    assert "THIS IS NOT A BAN ON THE SECTION CHECKPOINT" in section
    assert "THE LINE IS BETWEEN ONE PART AND THE WHOLE THING" in section
    # The operational form. A prohibition the model cannot apply to a
    # specific sentence is a prohibition it will apply wrongly.
    assert "cannot name the ONE part number" in section
    # The over-broad draft, pinned as forbidden.
    assert "the interview, the sections, or the form are finished" not in section, (
        "the over-broad wording is back; it bans the section checkpoint")


def test_a_deferral_must_be_audible_in_the_same_reply(cfg):
    """v28, the auditor's measurement: 24 of 225 deferring turns said nothing
    about it at all.

    ⚠ The prompt already carried HALF of this and had since v1: "a promise
    with no entry is a defect" guards reply -> deferred. The direction that
    harms her is deferred -> reply, and it was never stated, which is how it
    survived 27 versions. BOTH directions must be present, so this asserts
    the old one has not been replaced by the new one."""
    section = _section(cfg, "DEFERRALS")
    assert "a promise with no entry is a defect" in section, (
        "the original direction was dropped while adding its mirror")
    assert "AN ENTRY WITH NO PROMISE IS THE SAME DEFECT" in section
    # It must not cost a question, or it collides with the one-question rule
    # and the model resolves the collision on its own.
    assert "still ends on exactly one question" in section


def test_a_part_is_not_complete_while_a_field_in_it_is_deferred(cfg):
    """v28, the same defect at part scale: "that completes Part 4" spoken
    with a Part 4 field deferred in the same turn.

    Written so the CHECKPOINT still happens and still reads values back, and
    only the CLAIM is constrained. That distinction is exactly what v27's
    first draft got wrong, so it is pinned here rather than trusted."""
    section = _section(cfg, "SECTION CHECKPOINTS")
    assert "A PART IS NOT COMPLETE WHILE A FIELD IN IT IS DEFERRED" in section
    assert "The checkpoint still happens and still reads the values back" in section
    # The v27 carve-out must survive underneath it. Adding a constraint on
    # top of a carve-out is the obvious way to re-break what #928 fixed.
    assert "THIS IS NOT A BAN ON THE SECTION CHECKPOINT" in section
    assert "cannot name the ONE part number" in section


def test_the_close_rule_names_the_flag_guard_it_is_not_duplicating(cfg):
    """The prompt rule and the server guard are different mechanisms on the
    same harm. If someone later reads one as redundant and deletes it, the
    dossier is where the distinction lives, so pin that it is written down."""
    dossier = _DOSSIER.read_text()
    assert "clear_interview_over_while_agenda_open" in dossier
    assert _AGENDA_EMPTY in dossier


def test_claimed_coverage_is_limited_to_this_turns_facts(cfg):
    """Ask 2. Telling her a group of questions is covered asserts a record,
    and the record is this response's facts array. conf-es-full-1 turn 85
    claimed five oath clauses on one blanket yes and minted one of six."""
    section = _section(cfg, "SECTION CHECKPOINTS")
    assert "facts` ARRAY CARRIES" in section
    # The escape hatch has to be there: a blanket yes CAN legitimately
    # answer several clauses, and the rule is mint-them-then-claim-them,
    # not never-claim-more-than-one.
    assert "MINT THEM ALL IN THIS RESPONSE" in section


def test_a_turn_without_the_conversation_is_refused(cfg):
    from app.services.prompt_assembly import MissingPromptVariables, assemble_prompt

    configs = {"n400/interviewer-turn": cfg}
    with pytest.raises(MissingPromptVariables) as exc:
        assemble_prompt(
            "n400_interviewer_turn", "my name is Lucia", configs,
            variables={
                "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
                "turn_id": "t_002", "known_facts": "nothing yet",
                "agenda": "q_p2_name | Part 2: Your name | p2.name | What is your full legal name?",
            },
        )
    assert "conversation" in exc.value.missing


# --- the model dial, resolved through the real resolver ---------------------

def _fake_request(configs: dict, app_id: str):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(remote_configs=configs)),
        state=SimpleNamespace(app_id=app_id),
    )


class _Body:
    def __init__(self, **meta):
        self._meta = meta

    def get_meta(self, key):
        return self._meta.get(key)


@pytest.fixture(scope="module")
def routing() -> dict:
    return json.loads(_ROUTING.read_text())


def test_the_dial_resolves_for_every_tier_name(routing):
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/should-not-be-used")
    for tier_name in ("free", "plus", "pro", "automation", "anything"):
        model = _resolve_model_routing(
            _fake_request(configs, "n400"),
            _Body(call_type="n400_interviewer_turn"),
            fallback, tier_name,
        )
        assert model == "anthropic/claude-sonnet-5", tier_name


def test_the_extractor_lane_still_dials_where_it_did(routing):
    """Adding a sibling row must not move the lane that is live on Scott's
    phone tonight."""
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/fallback-model")
    model = _resolve_model_routing(
        _fake_request(configs, "n400"),
        _Body(call_type="n400_interview_turn"), fallback, "free",
    )
    assert model == "anthropic/claude-sonnet-5"


# --- dossier reconciliation and reachability ---------------------------------

def test_the_dossier_is_reconciled_to_the_version_that_ships(cfg):
    front = _DOSSIER.read_text().split("---")[1]
    served = int(re.search(r"served_version:\s*(\d+)", front).group(1))
    slug = re.search(r"config_slug:\s*(\S+)", front).group(1)
    assert slug == "n400/interviewer-turn"
    assert served == cfg["version"], (
        f"dossier says v{served}, config ships v{cfg['version']}")


def test_the_call_type_is_registered_or_nothing_reads_this_file():
    from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG

    assert _CALL_TYPE_TO_CONFIG.get("n400_interviewer_turn") == "n400/interviewer-turn"
    # And the extractor lane is still mapped: cutover is the client's, later.
    assert _CALL_TYPE_TO_CONFIG.get("n400_interview_turn") == "n400/interview-turn"
