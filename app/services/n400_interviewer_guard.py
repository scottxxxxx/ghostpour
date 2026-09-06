"""A visible backstop for one defect of the N-400 interviewer lane.

Across the Spanish runs on v13 and v14 (2026-09-05) the model kept naming
in `asking` the node whose answer had just arrived, while the reply asked
the next question. The phone's cursor follows `asking`, so the standing
question would lag the spoken one by a turn. Two prompt wordings and one
field-order change did not close it; v15 defines it mechanically and
bilingually, and this guard sits behind that definition.

The rule, agreed with the auditor: drop `asking` ONLY when every field id
of the named node (read off the agenda line the client sent) has a fact
in this same response. The two legitimate cases where a node gets a fact
AND stays in `asking` are untouched: a partial answer (some fields
minted, the reply asks for the rest) keeps a node whose ids are not all
filled, and a batch drill-in names a node the graph routes on. With
`asking` gone the client's fallback picks the standing node while it is
unsatisfied, or the first agenda line once it is, which is the right
cursor either way.

Every drop is VISIBLE, which was the auditor's one condition: the
response carries `asking_dropped` with the node and the reason, and the
route logs it with the turn id, so guard hits can be counted per run and
we know whether the definition works or the guard is carrying it. The
goal is that this fires zero times.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

logger = logging.getLogger("ghostpour.n400_interviewer_guard")

CALL_TYPE = "n400_interviewer_turn"
DROP_REASON = "every field of this node was filled by a fact in this response"
OFF_AGENDA_REASON = "this node is not on the agenda"


def agenda_field_ids(agenda: str | None) -> dict[str, set[str]]:
    """node_id -> set of field ids, from the client's agenda lines.

    Line shape: `node_id | Part N: title | field_ids comma-joined | question
    [| options: ...]`. A line that does not parse is skipped rather than
    guessed, so a malformed agenda disables the guard for that node instead
    of producing a wrong drop.
    """
    out: dict[str, set[str]] = {}
    for raw in (agenda or "").splitlines():
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 4 or not parts[0]:
            continue
        ids = {f.strip() for f in parts[2].split(",") if f.strip()}
        if ids:
            out[parts[0]] = ids
    return out


def agenda_questions(agenda: str | None) -> dict[str, str]:
    """node_id -> the question text the client sent for that line.

    Same parse as `agenda_field_ids`, keeping segment four. Used to compare
    what the lane SPOKE against what the line it minted from says, in the
    applicant's own language, since both sides are the same locale.
    """
    out: dict[str, str] = {}
    for raw in (agenda or "").splitlines():
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 4 or not parts[0]:
            continue
        out[parts[0]] = parts[3]
    return out


# A battery (a line with several field ids) may be asked as one question and
# a "no" may mint every field, but only if the spoken question named every
# item. conf-v21 turn 64 spoke seven of eight items and minted eight;
# conf-v22 turn 67 spoke one clause and minted ten. This MARKS that shape
# rather than dropping it: which items were spoken cannot be told
# mechanically across four locales, and a wrong drop would destroy real
# answers. The comparison is length against the line's own question text,
# so it is locale-safe (both sides are the applicant's language).
BATTERY_MIN_FIELDS = 3
BATTERY_SPOKEN_RATIO = 0.55


def mark_battery_shortfall(text: str, agenda: str | None) -> tuple[str, dict | None]:
    """(possibly marked text, info or None) for a battery minted from a
    question far shorter than the line it came from."""
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, None
    if not isinstance(turn, dict) or not isinstance(turn.get("facts"), list):
        return text, None
    by_node_ids = agenda_field_ids(agenda)
    questions = agenda_questions(agenda)
    minted = {f.get("field_id") for f in turn["facts"] if isinstance(f, dict)}
    if not minted:
        return text, None
    # `reply` is a locale-keyed object in the contract, but a model that
    # returns a bare string here must not take the turn down: this guard
    # runs on every response and has no caller-side try/except. Found when
    # the non-answer rule stopped emptying `facts`, which had been hiding
    # this line behind an early return.
    reply = turn.get("reply") or {}
    if isinstance(reply, str):
        spoken = len(reply)
    elif isinstance(reply, dict):
        spoken = max((len(v) for v in reply.values() if isinstance(v, str)), default=0)
    else:
        spoken = 0
    for node_id, ids in by_node_ids.items():
        if len(ids) < BATTERY_MIN_FIELDS:
            continue
        hit = ids & minted
        if len(hit) < BATTERY_MIN_FIELDS:
            continue
        # A battery answered in one word mints ONE value many times ("no",
        # "no", "no"); a composite ask like a full name mints three
        # DIFFERENT values from an equally short reply and is not a battery
        # at all. conf-v23 marked q_p1_full_name twice for exactly that.
        # Locale-safe: it compares the model's own values to each other.
        values = {str(f.get("value")) for f in turn["facts"]
                  if isinstance(f, dict) and f.get("field_id") in hit}
        if len(values) != 1:
            continue
        question = questions.get(node_id) or ""
        if not question or spoken >= BATTERY_SPOKEN_RATIO * len(question):
            continue
        info = {"node_id": node_id, "minted": sorted(hit),
                "question_chars": len(question), "spoken_chars": spoken,
                "reason": "the spoken question was far shorter than the line these fields came from"}
        turn["battery_unspoken"] = info
        return json.dumps(turn, ensure_ascii=False), info
    return text, None


def drop_stale_asking(text: str, agenda: str | None) -> tuple[str, dict | None]:
    """Return (possibly rewritten text, drop info or None).

    Anything that is not the lane's JSON object, or has no object-shaped
    `asking`, passes through byte-for-byte: the guard must never turn a
    parse problem of its own into a changed response.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, None
    if not isinstance(turn, dict):
        return text, None
    asking = turn.get("asking")
    if not isinstance(asking, dict) or not asking.get("node_id"):
        return text, None
    node_id = str(asking["node_id"])
    by_node = agenda_field_ids(agenda)
    if by_node and node_id not in by_node:
        # conf-v19 English 60: asking named a node the agenda did not list
        # (Selective Service, answered by a known fact). Nothing was minted
        # for it, so the all-fields rule below could not see it. The client
        # falls back to its own cursor on a node off the agenda; making the
        # drop visible is what lets the audit count it.
        info = {"node_id": node_id, "field_ids": [], "reason": OFF_AGENDA_REASON}
        turn["asking"] = None
        turn["asking_dropped"] = info
        return json.dumps(turn, ensure_ascii=False), info
    node_ids = by_node.get(node_id)
    if not node_ids:
        return text, None
    facts = turn.get("facts") or []
    filled = {f.get("field_id") for f in facts if isinstance(f, dict)}
    if not node_ids <= filled:
        return text, None
    info = {"node_id": node_id, "field_ids": sorted(node_ids), "reason": DROP_REASON}
    turn["asking"] = None
    turn["asking_dropped"] = info
    return json.dumps(turn, ensure_ascii=False), info


# --- the evidence floor, server side --------------------------------------
#
# conf-v18 turn 47: the applicant said "yes" to a section summary and the
# lane minted the one empty id on the standing line (p6.child1.supported =
# yes) with a PRIOR turn's words as provenance. The phone's floor drops any
# fact whose cited words are not in the current utterance, but by then the
# wrong value had ridden into the spoken read-back. Applying the same floor
# here, before the reply leaves, keeps the transcript and the record in
# step, and every drop is visible so the audit can count them.

EVIDENCE_DROP_REASON = "provenance.utterance is not in what the applicant just said"


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split()).strip("\"'“”‘’ ")


def drop_facts_without_current_evidence(text: str, user_content: str | None) -> tuple[str, list[dict]]:
    """Return (possibly rewritten text, list of dropped fact summaries).

    A fact stays when its `provenance.utterance` is a substring of the
    current utterance after whitespace and case folding. A fact with no
    utterance at all is dropped too: the contract requires one. Anything
    that is not the lane's object passes through byte for byte.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, []
    if not isinstance(turn, dict) or not isinstance(turn.get("facts"), list):
        return text, []
    said = _norm(user_content or "")
    kept, dropped = [], []
    for f in turn["facts"]:
        utt = ((f.get("provenance") or {}).get("utterance") if isinstance(f, dict) else None) or ""
        if utt and _norm(utt) in said:
            kept.append(f)
        else:
            dropped.append({"field_id": f.get("field_id") if isinstance(f, dict) else None,
                            "utterance": utt, "reason": EVIDENCE_DROP_REASON})
    if not dropped:
        return text, []
    turn["facts"] = kept
    turn["facts_dropped"] = dropped
    return json.dumps(turn, ensure_ascii=False), dropped


BOTH_REASON = "the same field was also deferred in this response; the deferral stands"


def drop_facts_that_are_also_deferred(text: str) -> tuple[str, list[dict]]:
    """One field is a fact or a deferral in one response, never both.

    conf-v20 turn 38: facts p4.prior_address1.from = 2017-10-01 and .to =
    2020-06-01 rode alongside deferrals for the same fields with partials
    2017-10 and 2020-06, and the reply said "to confirm the exact days".
    The client keeps the fact, so two invented days stood. The deferral is
    the honest one of the pair; the fact is dropped and the drop is marked.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, []
    if not isinstance(turn, dict):
        return text, []
    deferred_ids = {d.get("field_id") for d in (turn.get("deferred") or []) if isinstance(d, dict)}
    facts = turn.get("facts")
    if not deferred_ids or not isinstance(facts, list):
        return text, []
    kept, dropped = [], []
    for f in facts:
        fid = f.get("field_id") if isinstance(f, dict) else None
        if fid in deferred_ids:
            dropped.append({"field_id": fid, "value": f.get("value"), "reason": BOTH_REASON})
        else:
            kept.append(f)
    if not dropped:
        return text, []
    turn["facts"] = kept
    turn["facts_dropped"] = (turn.get("facts_dropped") or []) + dropped
    return json.dumps(turn, ensure_ascii=False), dropped


def guard_response_text(text: str, agenda: str | None, turn_id: str | None,
                        user_content: str | None = None) -> str:
    new_text, info = drop_stale_asking(text, agenda)
    if info is not None:
        logger.warning(
            "n400_asking_dropped turn_id=%s node_id=%s field_ids=%s",
            turn_id, info["node_id"], ",".join(info["field_ids"]),
        )
    if user_content is not None:
        new_text, dropped = drop_facts_without_current_evidence(new_text, user_content)
        for d in dropped:
            logger.warning("n400_fact_dropped_no_evidence turn_id=%s field_id=%s", turn_id, d["field_id"])
    new_text, both = drop_facts_that_are_also_deferred(new_text)
    for d in both:
        logger.warning("n400_fact_dropped_also_deferred turn_id=%s field_id=%s", turn_id, d["field_id"])
    # Marks and does not drop, so ordering is free; it sits after the evidence
    # floor so `minted` names only facts that survived it.
    new_text, not_answer = mark_facts_minted_on_a_non_answer(new_text)
    if not_answer is not None:
        logger.warning(
            "n400_minted_on_non_answer turn_id=%s intent=%s minted=%s",
            turn_id, not_answer["intent"], ",".join(not_answer["minted"]))
    if user_content is not None:
        new_text, days = defer_dates_with_unspoken_day(new_text, user_content)
        for d in days:
            logger.warning(
                "n400_date_day_unspoken turn_id=%s field_id=%s claimed=%s deferred_as=%s",
                turn_id, d["field_id"], d["value"], d["partial_value"])
    # Before the battery marker, which reads reply.values().
    new_text, reply_shape = normalize_reply_shape(new_text)
    if reply_shape is not None:
        logger.warning(
            "n400_reply_shape_normalized turn_id=%s chars=%d",
            turn_id, reply_shape["chars"])
    new_text, battery = mark_battery_shortfall(new_text, agenda)
    if battery is not None:
        logger.warning(
            "n400_battery_unspoken turn_id=%s node_id=%s minted=%s spoken_chars=%d question_chars=%d",
            turn_id, battery["node_id"], ",".join(battery["minted"]),
            battery["spoken_chars"], battery["question_chars"])
    return new_text


# --- a day nobody spoke is not a date, server side -------------------------
#
# conf-v24 turn 35: facts p4.prior_address1.from = 2017-10-19 and .to =
# 2020-06-01 from an utterance that gave a month and a year and no day at
# all. The prompt has said "a month and a year is a month and a year, never
# 2017-10-01" since v13 and the lane broke it anyway, which is why this is
# a guard and not another sentence. `drop_facts_that_are_also_deferred`
# already catches the version of this where the SAME response also defers
# the field (conf-v20 turn 38, the same two dates); it cannot see this one,
# because here nothing was deferred and the invented day stood alone.
#
# The fix is the prompt's own prescription applied mechanically: the fact
# becomes the deferral it should always have been, carrying the month and
# year it really had as `partial_value`. Nothing true is lost, and the day
# stops being asserted on a federal form.

INVENTED_DAY_REASON = "the day is not in what the applicant just said; deferring to the month and year"

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Day words, English and Spanish, cardinal and ordinal, folded. Only 1..31
# matter, so this is the whole space rather than a parser.
_DAY_WORDS: dict[str, int] = {}
for _i, _en in enumerate(
    ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
     "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
     "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
     "twentieth", "twentyfirst", "twentysecond", "twentythird", "twentyfourth",
     "twentyfifth", "twentysixth", "twentyseventh", "twentyeighth",
     "twentyninth", "thirtieth", "thirtyfirst"], start=1):
    _DAY_WORDS[_en] = _i
for _i, _en in enumerate(
    ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
     "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
     "seventeen", "eighteen", "nineteen", "twenty"], start=1):
    _DAY_WORDS[_en] = _i
for _i, _es in enumerate(
    ["primero", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
     "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
     "dieciseis", "diecisiete", "dieciocho", "diecinueve", "veinte",
     "veintiuno", "veintidos", "veintitres", "veinticuatro", "veinticinco",
     "veintiseis", "veintisiete", "veintiocho", "veintinueve", "treinta"],
        start=1):
    _DAY_WORDS[_es] = _i
_DAY_WORDS["uno"] = 1
_DAY_WORDS["primer"] = 1
# Apocopated Spanish, which is how these are actually spoken before a noun.
# "veintiun" is what "veintiún" folds to once the accent comes off, and
# without it a day she really said would be deferred as invented.
_DAY_WORDS["veintiun"] = 21
_DAY_WORDS["veintidos"] = 22
_DAY_WORDS["veintitres"] = 23
_DAY_WORDS["veintiseis"] = 26


def _fold_words(s: str) -> set[str]:
    """Lowercase, strip accents, drop punctuation and hyphens, split.

    "twenty-first" and "veintiún" have to match "twentyfirst" and
    "veintiuno", so hyphens close up and accents come off.
    """
    t = unicodedata.normalize("NFD", (s or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("-", "").replace("‑", "")
    return set(re.findall(r"[a-z]+", t))


def _day_was_spoken(day: int, said: str) -> bool:
    """True when the utterance actually contains this day of the month.

    Digits first: a bare 1..31 anywhere, with or without a leading zero,
    and ordinal suffixes ("19th", "1st"). Then the word forms in both
    languages. A four digit run is a year, not a day, so `\\b` alone is not
    enough and the digit search excludes longer numbers explicitly.
    """
    for m in re.finditer(r"\d+", said):
        tok = m.group(0)
        if len(tok) <= 2 and tok.lstrip("0").isdigit() and int(tok) == day:
            return True
    return any(_DAY_WORDS.get(w) == day for w in _fold_words(said))


def defer_dates_with_unspoken_day(text: str, user_content: str | None) -> tuple[str, list[dict]]:
    """Turn a day-precision date the applicant never spoke into a deferral.

    Only touches facts whose value is exactly YYYY-MM-DD. A fact whose day
    appears in the current utterance is left alone, in any of the forms a
    person actually says it. Everything else in the response passes through
    untouched, and a field that is already deferred is not deferred twice.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, []
    if not isinstance(turn, dict) or not isinstance(turn.get("facts"), list):
        return text, []
    said = user_content or ""
    if not said:
        return text, []
    already = {d.get("field_id") for d in (turn.get("deferred") or []) if isinstance(d, dict)}
    kept, moved = [], []
    for f in turn["facts"]:
        m = _ISO_DAY.match(str(f.get("value") or "")) if isinstance(f, dict) else None
        if not m or _day_was_spoken(int(m.group(3)), said):
            kept.append(f)
            continue
        fid = f.get("field_id")
        moved.append({"field_id": fid, "value": f.get("value"),
                      "partial_value": f"{m.group(1)}-{m.group(2)}",
                      "reason": INVENTED_DAY_REASON})
        if fid not in already:
            turn.setdefault("deferred", []).append(
                {"field_id": fid, "partial_value": f"{m.group(1)}-{m.group(2)}",
                 "reason": INVENTED_DAY_REASON})
    if not moved:
        return text, []
    turn["facts"] = kept
    turn["facts_dropped"] = (turn.get("facts_dropped") or []) + moved
    return json.dumps(turn, ensure_ascii=False), moved


# --- an utterance the lane itself called a non-answer -----------------------
#
# conf-v24 turn 80: six oath fields minted yes right after she said she did
# not understand the bearing-arms part. That is consent, not data, and the
# first version of this DROPPED every fact on such a turn.
#
# The auditor then measured it against nine graded runs before it shipped,
# and the number killed the design: THIRTEEN facts would have been dropped
# and THIRTEEN of them quote her current utterance, which the evidence
# floor above had already vouched for. Zero true drops. Every one is the
# same shape, and it is what a confused first-timer sounds like: she
# answers and then checks whether the answer counts. "just Mariana, she's
# grown, she lives with me, does she count" is question_back AND three
# real facts in one breath. Re-asking her is the moment she decides the
# machine is not listening.
#
# So a wrong label is a signal about the LABEL, not about the facts. This
# marks and counts; it does not drop. Same posture as
# `mark_battery_shortfall`, and for the same reason: a real signal you
# must not act on is still worth counting.
#
# Note what this deliberately does NOT do. Dropping only the facts that
# fail to quote her would be a pure no-op, because
# `drop_facts_without_current_evidence` already drops exactly those, for
# every intent, before this runs.

NOT_AN_ANSWER_INTENTS = frozenset({
    "help_explain",   # they did not understand the question
    "dont_know",      # they cannot answer it
    "repeat",         # they asked to hear it again
    "question_back",  # they asked why it is needed
    "legal_question", # they asked for advice
    "off_topic",
    "small_talk",
    "noise",
})
NOT_AN_ANSWER_REASON = "the response's own intent says this was not an answer, yet it minted"


def mark_facts_minted_on_a_non_answer(text: str) -> tuple[str, dict | None]:
    """Mark, do not drop, facts minted on a turn labelled a non-answer.

    `control`, `correction`, `answer`, `partial_answer` and
    `volunteered_extra` all legitimately carry facts and are not marked.
    """
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, None
    if not isinstance(turn, dict):
        return text, None
    intent = turn.get("intent")
    facts = turn.get("facts")
    if intent not in NOT_AN_ANSWER_INTENTS or not isinstance(facts, list) or not facts:
        return text, None
    info = {"intent": intent,
            "minted": sorted(str(f.get("field_id")) for f in facts if isinstance(f, dict)),
            "reason": NOT_AN_ANSWER_REASON}
    turn["minted_on_non_answer"] = info
    return json.dumps(turn, ensure_ascii=False), info

# --- the reply shape, fixed where the contract lives -----------------------
#
# 2026-09-06: a model returning `reply` as a bare string instead of the
# locale-keyed object crashed `mark_battery_shortfall` here, and the N-400
# client's LocalizedText decoder threw typeMismatch on the same wire and
# surfaced it as a NON-RETRYABLE malformedResponse: a terminal error
# mid-interview with no way forward. Hardening both sides was necessary and
# is not sufficient, because the next client would have to discover this
# for itself.
#
# GP owns this contract, so GP normalises it. A bare string becomes the
# object it should always have been, under "en", which every locale falls
# back to, so a Spanish interview still speaks the line it was sent.
# Anything that is neither a string nor an object is left exactly as it is
# and only counted: tolerating a string must not slide into inventing a
# reply out of an integer.

REPLY_SHAPE_REASON = "reply arrived as a bare string; wrapped under 'en', which every locale falls back to"


def normalize_reply_shape(text: str) -> tuple[str, dict | None]:
    """Turn a bare-string `reply` into the locale-keyed object."""
    try:
        turn = json.loads(text)
    except (TypeError, ValueError):
        return text, None
    if not isinstance(turn, dict) or not isinstance(turn.get("reply"), str):
        return text, None
    info = {"chars": len(turn["reply"]), "reason": REPLY_SHAPE_REASON}
    turn["reply"] = {"en": turn["reply"]}
    turn["reply_shape_normalized"] = info
    return json.dumps(turn, ensure_ascii=False), info
