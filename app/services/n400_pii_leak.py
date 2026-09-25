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

SAME-SHAPED FAKES (Scott, 2026-09-25). The phone does not send `[[TYPE_n]]`;
it swaps each identifier for a fake of the same shape and swaps it back in the
reply, so a masked turn is full of nine-digit runs. The phone lists them in
`metadata.surrogates` and `surrogate_keys` skips them; without that list every
masked turn would read as a leak.

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


def surrogate_keys(surrogates) -> tuple[set[str], set[str]]:
    """(digit sequences, lowercased emails) of the phone's fakes.

    Scott ruled on 2026-09-25 that the phone sends SAME-SHAPED FAKES
    (555-00-5555 for a real SSN), not `[[TYPE_n]]` placeholders, so a masked
    turn carries nine-digit runs that are NOT leaks. The phone names them in
    `metadata.surrogates`, an array of every fake exactly as written in the
    body. Numbers are matched by DIGITS, so a fake written with dashes still
    skips where the body spaces it; emails case-insensitively. Anything that
    is not a list of strings is ignored, never guessed: a malformed field
    means nothing is skipped, which over-counts rather than hides a leak.
    """
    digits, emails = set(), set()
    if not isinstance(surrogates, list):
        return digits, emails
    for v in surrogates:
        if not isinstance(v, str) or not v.strip():
            continue
        if "@" in v:
            emails.add(v.strip().lower())
        d = "".join(c for c in v if c.isdigit())
        if d:
            digits.add(d)
    return digits, emails


def shapes(text: str | None, surrogates=None) -> dict[str, int]:
    """{kind: count} of unmasked identifiers in `text`, fakes excepted.
    Empty when clean."""
    if not text:
        return {}
    fake_digits, fake_emails = surrogate_keys(surrogates)
    out: dict[str, int] = {}
    n_email = sum(1 for e in _EMAIL.findall(text) if e.lower() not in fake_emails)
    if n_email:
        out["email"] = n_email
    for m in _RUN.finditer(written(_EMAIL.sub(" ", text))):
        run_digits = "".join(c for c in m.group(0) if c.isdigit())
        kind = _KINDS.get(len(run_digits))
        if kind and run_digits not in fake_digits:
            out[kind] = out.get(kind, 0) + 1
    return out


def report(call_type: str | None, sources: dict[str, str | None],
           turn_id: str | None = None, surrogates=None) -> list[dict]:
    """Rows of {source, kind, count}, logged one warning each. [] and no log
    when the turn is clean or is not the interviewer lane. Never raises."""
    try:
        if call_type != INTERVIEWER_CALL_TYPE:
            return []
        rows = []
        for source in SOURCES:
            value = sources.get(source)
            found = shapes(value if isinstance(value, str) else None, surrogates)
            for kind, count in sorted(found.items()):
                rows.append({"source": source, "kind": kind, "count": count})
                logger.warning("n400_pii_unmasked source=%s kind=%s count=%d turn_id=%s",
                               source, kind, count, turn_id)
        return rows
    except Exception as e:  # noqa: BLE001
        logger.warning("n400_pii_leak check failed open: %s", type(e).__name__)
        return []
