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
    client cannot parse, and the model would look correct.

    ⚠ This test is blind to the SHAPE of `intent`. v31 moved it from a bare
    string to an object, and every one of the fourteen values survives as a
    value of `type`, so this stayed green across the exact change that most
    affects the client's decoder. The shape lives in the next test."""
    sp = cfg["systemPrompt"]
    for key in _RESPONSE_KEYS:
        assert f'"{key}"' in sp, key
    for intent in _INTENTS:
        assert f'"{intent}"' in sp, intent


def _output_schema(cfg) -> str:
    """The response shape block and everything after it: the JSON skeleton,
    the intent taxonomy paragraph, and the Fact schema. Scoped to that tail
    rather than the whole document because `intent` is also mentioned in
    prose earlier (DEFERRALS, WHERE YOU STOP) and a whole-document check
    would pass on a sentence ABOUT the field rather than its declaration."""
    sp = cfg["systemPrompt"]
    return sp[sp.index('{\n  "schema_version": 1,'):]


def test_intent_is_declared_as_an_object_with_a_type_key(cfg):
    """v31, Scott's ruling 2026-09-11: `intent` is an OBJECT on every turn,
    even when `type` is the only key. Both code halves already read both
    shapes (client LaneIntent, GP intent_label() in 95756ab); the prompt is
    the irreversible half, and this pins what the client decodes from it.

    Presence first, because an absence check is true of the old text too
    and proves nothing on its own. Seen red against v30 on the first
    assertion before it was trusted green against v31."""
    tail = _output_schema(cfg)
    # The schema line: the object, with `type` as its labelled key.
    assert '"intent": an OBJECT, never a bare string, {"type": string' in tail
    # The minimal worked example, so "only key" has an operational form.
    assert ('Write `intent` as an object, always, even when `type` is the only '
            'key you have: {"type": "answer"}') in tail
    # The retirement, stated, so a later edit cannot quietly re-admit the
    # string as an alternative.
    assert "The bare string is the old shape and is being retired." in tail
    # The optional keys are named in the schema line as OMITTED by default;
    # the client's decoder treats each as optional, and a prompt that made
    # one required would change the wire without changing this file's
    # fourteen-token check.
    for key in ("target", "confidence", "disambiguation"):
        assert f'"{key}": omitted' in tail, key
    # The fourteen values are the values of `type`, declared after the
    # object line rather than before it.
    # The taxonomy names `intent.type`, not `intent`: two sentences that
    # disagreed about what the field IS (a string here, an object above)
    # were the shape that came back as 11 of 39 turns disagreeing with
    # themselves. fable-auditor's replacement, v31.
    taxonomy = tail.index("`intent.type` is exactly one of:")
    assert "`intent` is exactly one of:" not in tail, "the old opening would contradict the object line"
    assert tail.index('"intent": an OBJECT') < taxonomy
    for intent in _INTENTS:
        assert f'"{intent}"' in tail[taxonomy:], intent
    # Only now the absence: the old schema line must be gone, or the prompt
    # declares both shapes and the model picks one per turn.
    assert '"intent": string,' not in tail, (
        "the v30 bare-string schema line is back beside the object form")


def test_the_target_keys_the_client_decodes_are_named(cfg):
    """The structured half: `target` carries her own words plus at most two
    ids, and `disambiguation` carries a locale-keyed question with two or
    more options that each name a real field id. Each key is asserted inside
    its own paragraph, because "field_id" alone is also the Fact schema's
    key and a whole-tail check would pass on that."""
    tail = _output_schema(cfg)
    target = tail[tail.index("`target` is present when, and only when"):
                  tail.index("`confidence` is your read")]
    for key in ("field_id", "node_id", "said"):
        assert f'"{key}"' in target, key
    assert "verbatim, always present when target is" in target
    # The presence rule is a fact about her utterance, not a shape choice.
    assert "if she pointed at something, fill it; if she did not, omit the key" in target
    disamb = tail[tail.index("`disambiguation` is present only when"):
                  tail.index("None of this changes the reply")]
    for key in ("question", "options", "label", "field_id"):
        assert f'"{key}"' in disamb, key
    assert "two or more" in disamb
    # The reply still does the whole job on its own; a disambiguation written
    # only in `intent` is one she never hears.
    assert "still has to be asked out loud in `reply`" in tail


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


# --- the yes/no value shape the client's document gate depends on ----------
#
# The N-400 client's document gate does not read a flag; it string-compares
# `p9.oath_disability == "no"` (the field gates four oath fields on the
# client through `if:p9.oath_disability==false`). Production measurement,
# 2026-09-06, SSH probe of usage_log (call_type='n400_interviewer_turn',
# 2461 parsed turn objects): every emitted p9.oath_disability fact (27 of
# 27) was the string 'no', value_type 'string', and the same held across
# every other measured p9.* yes/no field (arrested_ever, understand_oath,
# willing_full_oath, and 50-odd more): always the literal 'yes' or 'no',
# never true/false, never 'Yes'/'No', never the field's own id.
#
# This suite does not talk to prod, so it cannot re-run that measurement.
# What it pins is the RULE that produced it, so a change to the rule is
# caught here before the next measurement would even be taken.

def test_the_yes_no_value_shape_the_client_gate_depends_on(cfg):
    """If this instruction ever allowed true/false, a capitalised 'Yes', or
    the field's own id/label as the value, the gate's string comparison
    would stop holding and an applicant could export a form she should not.

    Scoped to HOW TO TALK, not a bare `in` check on the whole 58k document:
    `value_type "string"` also appears once more, in the unrelated
    STATED-NONE rule for optional fields, and the general Fact schema later
    in the document separately lists "boolean" as a legal value_type for
    fields that ARE genuinely boolean (complete, interview_over). A check
    against the whole prompt would pass on either of those and prove
    nothing about the yes/no rule specifically."""
    section = _section(cfg, "HOW TO TALK")
    assert (
        "when the options are `yes, no`, the value is the literal "
        "identifier `yes` or `no`, never true or false"
    ) in section
    assert 'value_type "string"' in section
    assert "never the field's own id or label" in section
    # The worked example ties the rule to a concrete field rather than
    # leaving it as prose about itself; a rule with no example is easier to
    # weaken by accident and have nothing catch it.
    assert "a race checkbox answered yes is `yes`, not `race_black`" in section


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


def test_deferred_entries_declare_origin_and_absent_means_applicant(cfg):
    """v32, Scott's rule 2 WITH the origin discriminator (DECISIONS
    2026-09-13). A capture gap is a `deferred` entry for something she DID
    answer and we could not record, so the question is owed to her again: the
    opposite obligation to an ordinary deferral. GP's two readers (#971) and
    the client's four already read `origin`; the prompt is the irreversible
    half and goes last, tolerate before emit.

    Presence first, because the absence of "goes NOWHERE" is true of text that
    never had the rule and proves nothing alone. Seen red against v31 on the
    first assertion before it was trusted green against v32."""
    tail = _output_schema(cfg)
    # The schema line declares the key and its only two values.
    assert '"origin": "applicant" or "capture_gap"}' in tail
    # Absent means applicant, stated, so a shipped client and GP's readers
    # (which both default an absent key to applicant) match the prompt.
    assert 'Omit `origin` or write "applicant" for the ordinary deferral' in tail
    assert "Never any other string" in tail

    sp = cfg["systemPrompt"]
    assert sp.count("A PASSING MENTION OF A FACT YOU CANNOT RECORD") == 1
    assert "A capture gap NEVER closes a slot" in sp
    assert "said earlier, not yet recorded, ask again" in sp
    # v30's disclosure rule covers the new origin too, not only applicant.
    assert "a capture gap in `deferred` is named in the reply, unconditionally" in sp
    assert sp.count("AND THE CLAUSE MUST NOT BE CONDITIONAL") == 1

    # The v31 passage v32 re-rules must be GONE, or two sentences in one
    # paragraph disagree about "I've had my green card since 2019", the shape
    # that came back as 11 of 39 unstable turns.
    assert "goes NOWHERE" not in sp
    assert "It is never for a fact the applicant stated plainly" not in sp
    # The one clause of it that was never about capture survives. v33 extends
    # it with a clause after a semicolon, so the sentence opening is pinned.
    assert "A deferral is never for a decision about whether something should be disclosed" in sp


def test_the_capture_gap_names_part_9_and_the_entry_is_the_record(cfg):
    """v33. v32's capture gap measured 0 of 4 on the live lane (auditor,
    2026-09-15): a DUI, Selective Service twice and a speeding ticket said in
    Part 1 each got "noted" in the reply and `deferred: []`, and GP read the
    model's RAW output to confirm the entry was never generated. v33 names Part
    9 as the only fields out of view before Part 9, gives real ids from the
    client's FormTemplates, replaces the in-window green card example, and says
    the reply is not the record.

    Presence first, seen red against v32 on the first assertion before trusted
    green against v33. A green test here says the TEXT is in the prompt, not
    that the lane obeys it; the pre-ship probe is the evidence for that."""
    sp = cfg["systemPrompt"]
    assert sp.count("The entry is the record and the reply is not") == 1
    assert "beside an empty `deferred` is a fact lost" in sp
    assert "Never say you noted something you did not put in `deferred`" in sp
    # Part 9 named, with the ids the lane must use.
    assert "The fields that are never in front of you before Part 9 are Part 9's own" in sp
    for pid in ("p9.arrested_ever", "p9.selective_service_registered",
                "p9.selective_service_date", "p9.overdue_taxes", "p9.registered_or_voted"):
        assert f"`{pid}`" in sp, pid
    # Both worked examples are OUT of window before Part 9 by construction.
    assert 'field_id "p9.selective_service_registered", origin "capture_gap"' in sp
    assert 'field_id "p9.arrested_ever", origin "capture_gap", partial_value "speeding ticket in 2019, paid"' in sp
    # The attorney boundary never empties deferred.
    assert "the boundary never empties `deferred`" in sp
    # Edit B: a capture gap is not a disclosure decision.
    assert "a capture gap is not that decision, it is the record that she disclosed" in sp
    # The fallback moved rather than vanished.
    assert sp.count("the nearest field id you can name") == 1
    # The retired example and line are gone.
    assert "had my green card since 2019" not in sp
    assert "I will ask about your address when we get to that part" not in sp


def test_a_plain_answer_gets_no_echo_and_no_opener(cfg):
    """v34, in four cuts. Scott, from his phone 2026-09-15: six replies in a
    row opened "Got it, Mexico. / Got it, Mexico. / Got it, male. / Got it,
    January 1st, 2021. / Got it, no." "Got it" appeared nowhere in the served
    prompt; the model found one form that satisfies "acknowledge what landed,
    then ask the next thing" and repeated it. v34 keeps the echo where it has a
    job (a spoken app mishears names, dates, numbers and addresses) and drops
    it where it has none (a yes, a no, a sex, an offered choice).

    The cuts are why the asserts moved. v34b told the model to vary the opener
    and named the check ("look at the first word of your own previous line").
    v34c gave up on varying it and removed the opener after a plain answer
    outright. That worked and overshot: probe 3 killed the opener 3 of 3 and
    took the date read-back with it, so a misheard LPR date had nowhere to
    surface. v34d puts the read-back back, in front of the next question and
    with no opener word before it.

    So the vary-the-opener phrases this test used to assert PRESENT are now
    asserted ABSENT, and "Got it" is 3 rather than 1: the no-opener list, "No
    opener means no 'Got it'", and the ban on "Got it, no".

    Presence first, seen red against v33 on the first assertion before trusted
    green against v34. A green test proves the TEXT; probe 4 (v34d, 3 of 3,
    zero "Got it", the date leading the reply) is the evidence the lane obeys
    it."""
    sp = cfg["systemPrompt"]
    assert sp.count("A PLAIN ANSWER GETS NO ECHO") == 1
    assert sp.count("ECHO ONLY WHAT COULD HAVE BEEN MISHEARD") == 1
    # v34c: no opener at all after a plain answer, not a different one.
    assert "it begins with the next question itself" in sp
    # CASING, not presence: v34 Edit A had this lowercase inside a semicolon
    # list, and v34b/c rewrote the region into sentences, which made it
    # sentence-initial. The lowercase assert this test shipped with went red on
    # v34d and read as a missing rule. Counted, so a third casing cannot slip
    # in beside it.
    assert sp.count('Never "Got it, no"') == 1
    # v34d: no opener must not mean no read-back. This is the sentence that
    # cost a cut, so it is asserted by count, not by presence.
    assert sp.count("it never means no read-back") == 1
    # The echo's job, stated, so a later edit cannot drop dates and numbers
    # along with the plain answers.
    assert "a name of a person or a place, a date, a number, an address" in sp
    # #966's protection: an unechoed value is still minted.
    assert "a value you do not echo is still minted" in sp
    # The one-line rule the model was satisfying with "Got it" is gone, and so
    # are v34b's vary-the-opener sentences that v34c replaced.
    assert "acknowledge what landed, then ask the next thing" not in sp
    assert "Never the same opener two turns running" not in sp
    assert "look at the first word of your own previous line" not in sp
    # "Got it" appears only inside the rules that forbid its forms.
    assert sp.count("Got it") == 3


def test_the_deferral_hedge_must_attach_to_every_deferred_field(cfg):
    """v29. v28 wrote this rule in the SINGULAR and every worked example
    carried one field, so a reply hedging one deferred field and stating
    another flat satisfied it.

    The auditor found it while trying to bound their own count from above:
    their probe asked whether a hedge appeared ANYWHERE in the reply rather
    than whether it attached to the field that was deferred, and my rule had
    the identical hole. Three real turns, each hedging the ZIP correctly and
    then stating the deferred move-in date as settled.

    ⚠ Third time in one session that a rule was right for the case in front
    of me and wrong for the one beside it."""
    section = _section(cfg, "DEFERRALS")
    assert "EVERY DEFERRED FIELD, NAMED, NOT ONE OF THEM" in section
    # An operational step, not a distinction the model has to feel. Same
    # lesson as the v28 naming test in SECTION CHECKPOINTS.
    assert "Read your own `deferred` array back" in section
    # The worked example has to be the MULTI-field failure, or the rule is
    # illustrated by exactly the case it already handled.
    assert section.count("June 2020") >= 3


def test_the_retracted_deferral_rate_is_not_quoted_as_a_rate(cfg):
    """Both teams retracted their counts, so the prompt may not cite one as
    if it were established. It states a FLOOR, which is what is defensible:
    2 of the auditor's 24 were false positives on reading, and neither
    probe can bound the number from above at all."""
    section = _section(cfg, "DEFERRALS")
    assert "AT LEAST 22 of 225" in section
    assert "of 225 turns that deferred something, 24" not in section, (
        "the retracted rate is back in the served prompt")


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
