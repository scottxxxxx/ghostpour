"""Which lane builds the file.

Three lanes, decided in a fixed order because the cheap certain gates
have to run before the expensive uncertain ones:

  provider  hand it to the model's own code execution sandbox, which is
            what we do today for everything
  contract  a column contract we own, rendered here
  plan      our generic sheet plan, rendered here, for tabular asks that
            match no contract

The ordering matters more than the scoring. Computation is checked FIRST
because it is cheap to decide and expensive to get wrong: a sandbox that
sums a column is correct, and a model that sums it in its head is
unverifiable and we have no way to catch it. Everything else can degrade
to the provider lane safely, so it does.

WHY SCORING AND NOT FIRST MATCH. `doc_templates.match_template` returns
the first registry entry whose hint appears, which is invisible with two
entries and dangerous with ten. "What do we need to do", "where are we"
and "what is still open" plausibly touch actions, topics and open
questions at once; with first-match, dict order silently picks, nobody
sees the near miss, and the user gets a confidently wrong file. Here
every candidate is scored, and a close second is a QUESTION, not a
coin flip.

This module decides. It does not generate: nothing here calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.artifact_types import CONTRACTS

# We render xlsx and nothing else. docx, pptx and pdf are the provider's
# until we have a renderer for them, which is coverage rather than a
# capability we lack.
RENDERABLE_FORMATS = ("xlsx",)

# Verbs that mean "do arithmetic or restructuring on data I gave you".
# They only count WITH an attachment: "chart" also lives inside "gantt
# chart", and a Gantt ask is authoring, not computing.
_TRANSFORM = re.compile(
    r"\b(pivot|re[- ]?pivot|chart|graph|plot|reconcile|de[- ]?dupe|"
    r"deduplicate|clean (?:up|this)|normali[sz]e|cross[- ]?reference|"
    r"vlookup|merge (?:these|this|the)|join (?:these|this|the)|"
    r"recalculate|re[- ]?calculate|sum (?:up|the)|total (?:up|the)|"
    r"average|convert (?:this|these|it)|reformat|transpose|"
    # Probing caught "turn the attached csv into a summary table" leaking
    # to our lane: restating supplied data IS aggregation, and the model
    # would have done it in its head.
    r"turn (?:this|these|the)\b[^.]{0,40}\binto|summari[sz]e (?:this|the)|"
    r"break (?:this|the)\b[^.]{0,20}\bdown|extract .{0,20}from (?:this|the))\b",
    re.I)

# Volume that has to be generated rather than written out. Structured
# output must emit every row in tokens; a loop does not.
_VOLUME = re.compile(
    r"\b(every (?:combination|permutation|pairing)|all combinations|"
    r"cartesian|exhaustive(?:ly)? (?:list|enumerate)|"
    r"(?:\d{3,})\s+(?:rows|cases|scenarios|records|items|combinations))\b",
    re.I)

# An ask can be tabular without naming an artifact we know.
_TABULAR = re.compile(
    r"\b(spreadsheet|excel|xlsx|workbook|worksheet|table|matrix|grid|"
    r"list of|register|log|tracker|breakdown|roster|inventory|"
    r"hoja de c[aá]lculo|tableur|スプレッドシート)\b", re.I)

# How far ahead the winner must be to route without asking. Below this
# the two readings are genuinely close and the user gets one question.
DECISIVE_RATIO = 1.8
DECISIVE_MARGIN = 4


def artifact_classifier_system() -> str:
    """Built from the registry, so a new contract teaches the classifier
    about itself instead of needing a prompt edit in another file.

    This exists because the lexical scorer below is not enough on its
    own. Measured 2026-08-15 on 108 generated user asks, blind to our
    hint vocabulary: lexical matching got 21% acceptable and missed 73%.
    Real people ask for an action register by typing "who's on the hook
    for stuff" and for a risk register with "everything that could blow
    up in our faces". Those share no words with any hint list anyone
    could write, and never will.
    """
    lines = []
    for name, c in CONTRACTS.items():
        noun = c.offer_noun or c.label
        note = getattr(c, "classifier_note", "")
        lines.append(f'  "{name}": {noun}'
                     + (f"\n      Boundary: {note}" if note else ""))
    catalog = "\n".join(lines)
    return (
        "You label what KIND of document a person is asking for after a "
        "meeting. Pick from this catalog:\n\n" + catalog + "\n\n"
        'Reply with ONLY this JSON: {"artifact": "<key>"|null, '
        '"confidence": "high"|"low"}. Use the key whose description '
        "matches what they actually want, even when they never name the "
        "document type and describe it in their own words instead. Use "
        "null when they want a document that is none of these, or when "
        "you cannot tell. Use low confidence when two of these fit "
        "roughly equally, and we will ask them. Judge the request, not "
        "the meeting's subject matter."
    )


@dataclass
class Route:
    lane: str                      # "provider" | "contract" | "plan"
    reason: str                    # stable token, for logs and telemetry
    contract: str | None = None
    candidates: list[str] = field(default_factory=list)
    offer_noun: str = ""
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def needs_question(self) -> bool:
        return len(self.candidates) > 1


def score_contracts(text: str) -> dict[str, int]:
    """Weighted hint score per contract. Never returns zero entries."""
    hay = (text or "").lower()
    out: dict[str, int] = {}
    for name, c in CONTRACTS.items():
        total = 0
        for phrase, weight in c.hints:
            if phrase.lower() in hay:
                total += weight
        if total:
            out[name] = total
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def requires_provider(text: str, has_attachment: bool = False) -> str | None:
    """A reason string when only the sandbox can do this, else None."""
    hay = (text or "")
    if _VOLUME.search(hay):
        return "generated_volume"
    if has_attachment and _TRANSFORM.search(hay):
        return "computation_over_attachment"
    return None


def route(text: str, fmt: str | None = None,
          has_attachment: bool = False,
          model_artifact: str | None = None,
          model_confidence: str = "high",
          is_file_request: bool | None = None) -> Route:
    """Pick the lane. Anything uncertain lands on the provider lane.

    `text` should be the QUESTION PORTION, not assembled history: a
    template word carried in history must not re-decide every later ask
    (the #420 lesson, applied here too).

    `model_artifact` is the classifier's read, passed IN rather than
    fetched here so this stays pure and synchronous and the caller owns
    the one network call. The deterministic gates below still run first
    and still win: the classifier decides WHICH artifact, never whether
    the sandbox is required, because a model asked to judge its own
    arithmetic is not a control.
    """
    if not (text or "").strip():
        return Route(lane="provider", reason="no_artifact_match")

    if is_file_request is False:
        return Route(lane="none", reason="not_a_file_request")

    blocked = requires_provider(text, has_attachment)
    if blocked:
        return Route(lane="provider", reason=blocked)

    # We only render xlsx. A stated wish for anything else is not ours.
    if fmt is not None and fmt not in RENDERABLE_FORMATS:
        return Route(lane="provider", reason="format_not_renderable")

    # The Gantt registry got here first and builds a better artifact than
    # anything generic. Probing found "make me a gantt chart" falling to
    # the generic plan lane, which would have quietly regressed a shipped
    # feature, and "can you build a project plan" bypassing the
    # simple-versus-detailed question entirely (Scott's 2026-08-11
    # ruling). Both are still OUR renderer, so this is a fourth lane, not
    # a handoff.
    from app.services.doc_templates import ambiguous_plan_ask, match_template
    tid = match_template(text, format=fmt)
    if tid:
        return Route(lane="template", reason="existing_template",
                     contract=tid)
    if ambiguous_plan_ask(text, format=fmt):
        if model_artifact in CONTRACTS and model_confidence == "high":
            return Route(lane="mixed", reason="ambiguous_plan_or_artifact",
                         contract=model_artifact,
                         candidates=["gantt", model_artifact],
                         offer_noun=CONTRACTS[model_artifact].offer_noun)
        return Route(lane="template", reason="ambiguous_plan_version")

    scores = score_contracts(text)

    # The classifier reads paraphrase; the hint list reads vocabulary.
    # Where they agree, route. Where the classifier alone has a read,
    # trust it, because on blind utterances it is right far more often
    # than the hints are. Where they disagree outright, ask.
    if model_artifact in CONTRACTS:
        lexical_top = next(iter(scores), None)
        disagrees = (lexical_top is not None
                     and lexical_top != model_artifact
                     and scores[lexical_top] >= 9)
        if disagrees or model_confidence == "low":
            pair = [model_artifact] + [
                n for n in scores if n != model_artifact][:2]
            return Route(lane="contract", reason="ambiguous_artifact",
                         candidates=pair[:3], scores=scores)
        return Route(lane="contract", reason="contract_match",
                     contract=model_artifact,
                     offer_noun=CONTRACTS[model_artifact].offer_noun,
                     scores=scores)

    if not scores:
        if fmt == "xlsx" or _TABULAR.search(text or ""):
            return Route(lane="plan", reason="tabular_no_contract",
                         scores=scores)
        return Route(lane="provider", reason="no_artifact_match",
                     scores=scores)

    ranked = list(scores.items())
    top_name, top_score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0

    decisive = (top_score >= runner * DECISIVE_RATIO
                or top_score - runner >= DECISIVE_MARGIN)
    if decisive:
        return Route(lane="contract", reason="contract_match",
                     contract=top_name,
                     offer_noun=CONTRACTS[top_name].offer_noun,
                     scores=scores)

    # Close call. Offer the ones that are actually close, capped at
    # three: a longer list is a form, not a question, and nobody reads it.
    close = [n for n, s in ranked if s >= runner][:3]
    return Route(lane="contract", reason="ambiguous_artifact",
                 candidates=close, scores=scores)


def question_for(route_: Route) -> str:
    """The one disambiguating question, in the user's terms.

    Names what each option would contain rather than its internal type,
    because "action_register or topic_tracker" means nothing to anyone.
    """
    if not route_.needs_question:
        return ""
    nouns = [
        ("a project schedule with the timeline and progress"
         if n == "gantt"
         else (CONTRACTS[n].offer_noun or CONTRACTS[n].label))
        for n in route_.candidates]
    if len(nouns) == 2:
        return f"I can build {nouns[0]}, or {nouns[1]}. Which would help?"
    joined = ", ".join(nouns[:-1])
    return f"I can build {joined}, or {nouns[-1]}. Which would help?"


def contract_candidate(lane_on: bool, intent: dict | None,
                       skip: bool = False) -> str | None:
    """Which contract, if any, this offer should remember.

    Only a HIGH confidence read counts. A low confidence one means the
    classifier saw two artifacts fitting equally, and silently picking
    one at offer time would hide a question we should have asked.
    `skip` carries the precedence rule: a Gantt template match or an
    ambiguous plan ask wins, because that registry is purpose built and
    its version question has to come before any build.
    """
    if skip or not lane_on or not intent:
        return None
    name = intent.get("artifact")
    if name in CONTRACTS and intent.get("artifact_confidence", "high") == "high":
        return name
    return None


def reroute_on_model_signal(plan: dict) -> Route | None:
    """Last backstop: the model says it needs to compute after all.

    The contract and plan schemas both carry `needs_computation`, so a
    plan that comes back with it set is discarded and the turn goes to
    the sandbox. Costs one cheap call on a rare path, and is the only
    gate that sees the actual content rather than the request.
    """
    if plan and plan.get("needs_computation"):
        return Route(lane="provider", reason="model_declared_computation")
    return None
