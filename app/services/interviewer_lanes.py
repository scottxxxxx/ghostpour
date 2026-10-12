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
