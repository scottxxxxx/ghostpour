"""Count identifiers that reached GhostPour UNMASKED on an N-400 interviewer turn.

The placeholder contract (N400 App contracts/pii-placeholders-2026-09-24.md)
has the phone swap SSN, A-Number, phone and email for `[[TYPE_n]]` before a
turn leaves it. A miss is silent on the phone by construction: the redactor
cannot report what it did not recognise. It is only visible HERE, on the
hop the words cross, so this counts it. The auditor ran the first client
build and it masked nothing on "seis dos siete, cuarenta y cuatro, noventa
dieciocho" (compounds unread) nor on a bare "627449018" (no SSN phrase in
her own turn), which is the failure this exists to make countable.

WHAT IT COUNTS, per source (`user_content`, `conversation`, `known_facts`,
the three the contract masks): after spoken runs are written as digits
(`spoken_numbers.written`, the client's own arithmetic), a run of exactly NINE digits (SSN, A-Number, or the client's
NUM; an A glued in front is allowed) or TEN (a US phone), with up to two
spaces, commas, hyphens, dots or parentheses between digits, and any email
address. An eight digit run is NOT counted: an ISO date is eight digits and
dates stay unmasked in stage one, so counting them would bury every real leak.

IT LOGS SHAPES, NEVER VALUES. The warning carries the source, the kind and
a count. A detector that wrote the digits it found would be the leak.

It changes nothing on the wire and never raises. Before the client ships
masking every dictated identifier counts, which is the baseline; after, the
count should fall to the misses.
"""

from __future__ import annotations

import logging
import re

from app.services.spoken_numbers import written

logger = logging.getLogger("ghostpour.n400_pii_leak")

INTERVIEWER_CALL_TYPE = "n400_interviewer_turn"
SOURCES = ("user_content", "conversation", "known_facts")

# A digit run: digits with up to two separators between any two (space,
# comma, hyphen, dot, parentheses: "627-44-9018", "(512) 555-0100"),
# optionally led by a glued A (an A-Number), never inside a longer word or
# number. Two separators, not one, is what lets ") " through.
_RUN = re.compile(r"(?<![\w])A?\d(?:[ ,\-().]{0,2}\d)*(?![\w])", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_KINDS = {9: "nine_digits", 10: "ten_digits"}


def shapes(text: str | None) -> dict[str, int]:
    """{kind: count} of unmasked identifiers in `text`. Empty when clean."""
    if not text:
        return {}
    # Placeholders need no stripping: an index is one or two digits, never
    # nine or ten, and "[[EMAIL_1]]" has no @. A strip was written first and
    # removing it turned no test red, so it was dead code and went.
    out: dict[str, int] = {}
    n_email = len(_EMAIL.findall(text))
    if n_email:
        out["email"] = n_email
    for m in _RUN.finditer(written(_EMAIL.sub(" ", text))):
        kind = _KINDS.get(sum(c.isdigit() for c in m.group(0)))
        if kind:
            out[kind] = out.get(kind, 0) + 1
    return out


def report(call_type: str | None, sources: dict[str, str | None],
           turn_id: str | None = None) -> list[dict]:
    """Rows of {source, kind, count}, logged one warning each. [] and no log
    when the turn is clean or is not the interviewer lane. Never raises."""
    try:
        if call_type != INTERVIEWER_CALL_TYPE:
            return []
        rows = []
        for source in SOURCES:
            value = sources.get(source)
            for kind, count in sorted(shapes(value if isinstance(value, str) else None).items()):
                rows.append({"source": source, "kind": kind, "count": count})
                logger.warning("n400_pii_unmasked source=%s kind=%s count=%d turn_id=%s",
                               source, kind, count, turn_id)
        return rows
    except Exception as e:  # noqa: BLE001
        logger.warning("n400_pii_leak check failed open: %s", type(e).__name__)
        return []
