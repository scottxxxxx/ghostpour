"""Do her words SUPPORT the value, not merely contain the quote.

The evidence floor (`drop_facts_without_current_evidence`) checks that a
fact's cited words are IN what she said. It cannot check that they establish
the value. Haiku replay, production turn t_008, 2026-09-19: asked "how long
have you had your green card, and did you get it through a U.S. citizen
spouse or on your own?", she said "Well, I've had it for 5 years and a lawyer
helped me get it." The model minted `p1.eligibility_basis =
general_provision` citing "I've had it for 5 years". The quote is real and
sits in the utterance, so the floor passed it. She had answered half of a
two part question and never said which path. Sonnet asked her instead.

WHY NO RULE CATCHES IT. The node WAS the standing node and she WAS answering
it, so nothing about agenda position separates this from a good mint. And an
enum value is a token (`general_provision`) that can never appear in her
words, so "value inside the cited words", which carries names and numbers,
has nothing to match. Whether words establish an option is a judgment, and
it is asked of TypeSafe (Jev) as one: a `choice`, with a confidence floor.

SCOPE, deliberately narrow:
  * only facts whose value is one of the declared options for the field.
    The client's `metadata.choice_fields` (field_id -> option ids, every
    choice field of the form, 2026-09-22) is the catalogue when it is sent;
    it names the options PER FIELD, so a folded gate (has_middle_name under
    the full-name node) or a value volunteered for a node the agenda has not
    reached yet is checked like any other, and a node that declares a UNION
    of options over several fields no longer sends the union. Without it the
    catalogue is the agenda's own `options:` segments, which only see fields
    on a listed line: the auditor's labelled set (qa/labelled-option-mints)
    had three real mints that scope could not see. And
  * only when that value does NOT literally appear in the cited words (a
    "yes" quoted for a yes/no fact is already carried by the floor).
  Dates, names and numbers are never sent: Jev's own notes say it is weak at
  date comparison, and the floor's substring test already covers them.

IT MARKS, IT NEVER DROPS. `facts_unsupported` is added beside the facts and
the facts stay. Dropping would silently undo a mint the reply has already
acknowledged out loud, on the word of a model that is sometimes wrong, and
the first version of any guard here marks and is counted before it is
trusted to drop (`mark_values_outside_declared_options` went the same way).
The marker is what lets the client put the field on the review card.

FAIL OPEN. No key, mode off, an open breaker, an error, a timeout, a parse
problem or an unsure Jev all leave the response byte for byte as it was,
which is today's behaviour.

NARROWEST STATE: the question, what she just said, the cited words, the
value and the options. Never the case, the known facts or the conversation.
"""

from __future__ import annotations

import json
import logging

from app.services import typesafe_judge
from app.services.n400_interviewer_guard import (
    _norm, agenda_field_ids, agenda_options, agenda_questions,
)

logger = logging.getLogger("ghostpour.n400_evidence_support")

JUDGMENT = "n400_evidence_support"
UNSUPPORTED_REASON = "her words do not establish this option"
# A turn mints a handful of facts. This bounds the request if one ever does not.
MAX_FACTS_PER_TURN = 8


def choice_catalogue(choice_fields) -> dict[str, list[str]]:
    """field_id -> its declared option ids, from the client's per-field map.

    Lower-cased like the agenda's options. Anything that is not a non-empty
    list of strings under a string key is skipped, never guessed, so a
    malformed map degrades to the agenda-only scope rather than to a wrong
    question.
    """
    out: dict[str, list[str]] = {}
    if not isinstance(choice_fields, dict):
        return out
    for fid, opts in choice_fields.items():
        if not isinstance(fid, str) or not isinstance(opts, list):
            continue
        clean = sorted({o.strip().lower() for o in opts if isinstance(o, str) and o.strip()})
        if clean:
            out[fid] = clean
    return out


def enum_facts_to_check(turn: dict, agenda: str | None, user_content: str | None,
                        choice_fields=None) -> list[dict]:
    """The minted facts worth a judgment, each with what Jev needs to make it."""
    facts = turn.get("facts")
    if not isinstance(facts, list) or not facts:
        return []
    catalogue = choice_catalogue(choice_fields)
    options = agenda_options(agenda)
    if not options and not catalogue:
        return []
    fields = agenda_field_ids(agenda)
    questions = agenda_questions(agenda)
    # The first agenda line is the standing node, the question she was
    # actually answering. A fact minted for a field on no line (a folded
    # gate, a volunteered value) is judged against THAT question: she was
    # asked about her marital status and the system recorded how her spouse
    # became a citizen, and Jev's `unrelated` and `insufficient` are built
    # for exactly that reading.
    standing_question = next(iter(questions.values()), "")
    out = []
    for f in facts:
        if not isinstance(f, dict) or not isinstance(f.get("value"), str):
            continue
        fid = f.get("field_id")
        value = f["value"].strip().lower()
        node = next((n for n, ids in fields.items() if fid in ids), None)
        if catalogue:
            declared = catalogue.get(fid) if isinstance(fid, str) else None
            if not declared or value not in declared:
                # Not a choice field, or a value outside its options: the
                # latter is `mark_values_outside_declared_options`' business.
                continue
            opts = declared
            question = questions.get(node, "") if node is not None else standing_question
        else:
            node = next((n for n, opts in options.items()
                         if fid in fields.get(n, set()) and value in opts), None)
            if node is None:
                continue
            opts = sorted(options[node])
            question = questions.get(node, "")
        cited = ((f.get("provenance") or {}).get("utterance")) or ""
        if _norm(value) and _norm(value) in _norm(cited):
            continue
        out.append({"field_id": fid, "question": question,
                    "applicant_said": user_content or "", "cited_words": cited,
                    "recorded_answer": f["value"], "options": list(opts)})
    return out[:MAX_FACTS_PER_TURN]


# THE CRITERIA (2026-09-24). The first wording caught 1 unsupported mint in 4
# on the auditor's REAL labelled turns (qa/labelled-option-mints.json), and
# the misses shared a mechanism: asked the OPEN eligibility question ("tell me
# why you believe you are eligible"), "I don't know, my daughter said I can
# apply, I have the green card five years" reads as a complete answer, so the
# verdict followed which question she had been asked rather than what she
# said. Naming the hedge (she does not know, she repeats someone else) and the
# inference (an option worked out from a number of years) moved it to 3 in 4
# with every good mint still left alone, and left the 32 synthetic cases where
# they were (qa/runs/*-2026-09-24-hedge2.json). The criteria are structured
# (what / not_for / examples) per TypeSafe's choice guidance, and every
# example comes from a field that is NOT in the labelled set, so the wording
# cannot win by quoting its own grading cases. Every clause of `insufficient`
# is load bearing: the first rewrite dropped "a guess from something she said
# about a different matter" and the synthetic "my kids live with me" -> married
# went from 5 of 5 marked to 0 of 5. Candidates live in
# qa/jev_support_variants.py; one that loses a case there does not ship.
SUPPORTS = {
    "what": ("She says which option applies to her, in her own words or an unmistakable "
             "equivalent, so that none of the other options could be what she meant."),
    "not_for": ("A detail from which the option might be worked out, when she has not said "
                "which option applies and another option is still possible: that is "
                "insufficient."),
    "examples": [
        "Asked about her father's citizenship, she says 'he was naturalized in 1990' and "
        "the recorded answer is naturalized.",
        "Asked whether she has ever used another name, she says 'no, only this one' and the "
        "recorded answer is no.",
    ],
}
INSUFFICIENT = {
    "what": ("Her words are about the question but do not settle which option applies. "
             "This covers: she answered only part of a question with several parts; what "
             "she said fits more than one option; the recorded option was worked out from a "
             "detail (a number of years, a place, a date) rather than from her saying which "
             "option applies; the recorded answer is a guess from something she said about a "
             "different matter; she says she does not know or is not sure; or she only "
             "repeats what someone else told her."),
    "not_for": "Words that name a different option in the list: that is contradicts.",
    "examples": [
        "Asked how she entered the country, she says 'I'm not sure, my uncle handled "
        "everything' and the recorded answer is with_inspection.",
        "Asked how her mother became a citizen, she says 'she's been here thirty years' and "
        "the recorded answer is naturalized.",
    ],
}


def support_questions(n: int) -> dict:
    """One choice per fact, all in one request. They run in parallel and
    cannot see each other, so each names its own slice of the state."""
    qs = {}
    for i in range(n):
        f = f"`facts[{i}]"
        qs[f"f{i}"] = {
            "type": "choice",
            "instructions": (
                f"An interviewer filling in a form asked {f}.question`. The applicant "
                f"answered {f}.applicant_said`. The system recorded the answer "
                f"{f}.recorded_answer`, chosen from {f}.options`, and quoted "
                f"{f}.cited_words` as its evidence. Do the applicant's own words "
                "establish that recorded answer?"),
            "criteria": {
                "supports": SUPPORTS,
                "insufficient": INSUFFICIENT,
                "contradicts": "Her words point to a different option than the recorded one.",
                "unrelated": "Her words do not address this question at all.",
            },
        }
    return qs


def read_support(body: dict, checked: list[dict]) -> list[dict]:
    """The facts Jev is CONFIDENT are not established. An unsure answer marks
    nothing: a near tie is not evidence against a mint."""
    answers = (body or {}).get("answers") or {}
    out = []
    for i, c in enumerate(checked):
        a = answers.get(f"f{i}") or {}
        verdict, conf = a.get("choice"), float(a.get("confidence") or 0.0)
        if verdict in ("insufficient", "contradicts", "unrelated") \
                and conf >= typesafe_judge.CONFIDENCE_FLOOR:
            out.append({"field_id": c["field_id"], "value": c["recorded_answer"],
                        "verdict": verdict, "confidence": round(conf, 3),
                        "reason": UNSUPPORTED_REASON})
    return out


async def _judge(api_key: str, checked: list[dict], mode: str, app_id, turn_id) -> list[dict]:
    body, row = await typesafe_judge.guarded_ask(
        api_key, {"facts": checked}, support_questions(len(checked)),
        judgment=JUDGMENT, mode=mode)
    unsupported = read_support(body, checked) if body is not None else []
    # What the dashboard's "marks per turn" is made of: one row per turn,
    # how many facts were asked about, how many came back marked. Marked is
    # None when Jev did not answer, so a failed call is not a turn with zero
    # marks.
    row["facts_checked"] = len(checked)
    row["facts_marked"] = len(unsupported) if body is not None else None
    typesafe_judge.record_later(row, app_id)
    for u in unsupported:
        logger.warning("n400_fact_unsupported turn_id=%s field_id=%s value=%s verdict=%s "
                       "confidence=%s mode=%s", turn_id, u["field_id"], u["value"],
                       u["verdict"], u["confidence"], mode)
    return unsupported


async def mark_unsupported_enum_facts(text: str, agenda: str | None, user_content: str | None,
                                      turn_id: str | None, app_id: str | None,
                                      mode: str, api_key: str, choice_fields=None) -> str:
    """The response text, with `facts_unsupported` added in primary mode when
    Jev is confident a minted option is not established. Every other path
    returns `text` byte for byte. Never raises."""
    try:
        if mode not in ("shadow", "primary") or not api_key:
            return text
        turn = json.loads(text)
        if not isinstance(turn, dict):
            return text
        checked = enum_facts_to_check(turn, agenda, user_content, choice_fields)
        if not checked:
            return text
        if mode == "shadow":
            # Counted, never applied, never waited on.
            typesafe_judge._hold(_judge(api_key, checked, mode, app_id, turn_id))
            return text
        unsupported = await _judge(api_key, checked, mode, app_id, turn_id)
        if not unsupported:
            return text
        turn["facts_unsupported"] = unsupported
        return json.dumps(turn, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("n400 evidence support check failed open: %s: %s", type(e).__name__, e)
        return text
