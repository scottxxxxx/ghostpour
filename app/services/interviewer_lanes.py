"""The interviewer lanes: one contract, one call type per form.

N-400 Helper's `n400_interviewer_turn` (contract: N400 App/contracts/
gp-interviewer-turn.md) and I-765 Helper's `i765_interviewer_turn`
(2026-10-09, the sibling app on the same engine) share the request
envelope and the strict JSON response. Until this module existed the route
keyed every lane behaviour on the N-400 literal, in six files, so a second
form would have had to be a second copy of each line. The set below is the
one place that says which call types ARE the lane; the behaviours that
belong to the CONTRACT (no generic streaming, the envelope backstop, the
sentence stream for speech, the one hour prompt cache, the PII leak
counter, the Spanish numeral hint) read it.

What stays keyed on N-400 alone, on purpose: the response guards in
app/services/n400_interviewer_guard.py and the evidence check. Every one of
them was tuned on N-400 receipts (oath fields, a thirteen part read-back,
Part 9 disclosures), and whether each applies to an I-765 interview is
decided per guard when the I-765 brief lands, not inherited by default.
"""

from __future__ import annotations

INTERVIEWER_CALL_TYPES: frozenset[str] = frozenset({
    "n400_interviewer_turn",
    "i765_interviewer_turn",
    "form_interviewer_turn",
})


def is_interviewer_turn(call_type: str | None) -> bool:
    return call_type in INTERVIEWER_CALL_TYPES


# The one form each lane serves, for the form_code guard below. N-400 is not
# listed on purpose: its client has sent form_code since before this guard
# existed and nobody has checked every build's value, so it is unguarded
# rather than guessed at. I-765's value is FormCatalog.swift:98, "I-765",
# which every saved case carries (I765 App 9028ecc).
LANE_FORM_CODES: dict[str, str] = {
    "i765_interviewer_turn": "I-765",
}


def _norm_form(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def same_form(expected: str, given: str | None) -> bool:
    """`I-765`, `i765` and `I765` are one form; `N-400` is not. Cosmetic
    variants pass because a client fixture already spells it `i765`, and a
    refusal over a hyphen would be our bug, not theirs."""
    return isinstance(given, str) and _norm_form(given) == _norm_form(expected)


def unknown_form_for_lane(call_type: str | None, form_code) -> str | None:
    """The expected form when a turn names a DIFFERENT one, else None.

    I-765 lead, 2026-10-21: the app becomes a multi-form app with the I-765
    first, and a turn for any other form must be refused loudly rather than
    served the I-765 interviewer. An ABSENT form_code passes: every I-765
    build sends one, and refusing its absence would turn a client bug into a
    dead interview mid-application.
    """
    expected = LANE_FORM_CODES.get(call_type or "")
    if expected is None or form_code is None or same_form(expected, form_code):
        return None
    return expected


# The generic lane (I-765 lead, 2026-10-29): one call type for every form
# after the I-765, with the form chosen by form_code. The prompt is the lane
# text, which names no form, followed by that form's FORM BLOCK, generated
# from the app's form definition (qa/form_blocks/). The value is the app that
# owns the call type, which is also the prefix its blocks live under, so a
# block is served to that app only. The I-765 itself stays on
# i765_interviewer_turn until a parity run moves it.
FORM_BLOCK_CALL_TYPES: dict[str, str] = {
    "form_interviewer_turn": "i765",
}


def form_block_slug(call_type: str | None, form_code) -> str | None:
    """`I-131`, `i131` and `I131` are one block: i765/form-block-i131."""
    app = FORM_BLOCK_CALL_TYPES.get(call_type or "")
    if app is None or not isinstance(form_code, str) or not _norm_form(form_code):
        return None
    return f"{app}/form-block-{_norm_form(form_code).lower()}"


def form_block_text(call_type: str | None, form_code, remote_configs: dict) -> str | None:
    """The block for this form, or None when none is published. None is a
    refusal, never a lane without a form: the lane text alone would
    interview against no fields and no stops."""
    slug = form_block_slug(call_type, form_code)
    cfg = (remote_configs or {}).get(slug) if slug else None
    text = (cfg or {}).get("block")
    return text if isinstance(text, str) and text.strip() else None
