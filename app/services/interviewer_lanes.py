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
