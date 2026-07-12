"""Live generation offers (conversational confirmation, handoff Part 1 v2).

When GP detects a file intent it returns the offer as an assistant chat
message and remembers it here for exactly one reply: the client echoes the
offer_id on the next send in that conversation, GP interprets the reply
against the remembered offer, and the offer dies — confirmed, declined, or
ignored. In-memory by design (same argument as the in-flight generation
registry): a GP restart kills pending offers, the echo finds nothing, and
the turn proceeds as normal chat — the user just asks again, or uses the
manual generate-as-file path.
"""

from __future__ import annotations

import time
import uuid

OFFER_TTL_S = 600  # an offer nobody replies to dies quietly

# (user_id, offer_id) -> {"format": str, "gist": str, "expires": float}
_OFFERS: dict[tuple[str, str], dict] = {}


def create(user_id: str, fmt: str, gist: str) -> str:
    """Remember a live offer; returns its offer_id (rides the envelope)."""
    offer_id = uuid.uuid4().hex[:12]
    _OFFERS[(user_id, offer_id)] = {
        "format": fmt, "gist": gist, "expires": time.monotonic() + OFFER_TTL_S,
    }
    # opportunistic sweep — the map only ever holds in-flight conversations
    now = time.monotonic()
    for k in [k for k, v in _OFFERS.items() if v["expires"] < now]:
        _OFFERS.pop(k, None)
    return offer_id


def take(user_id: str, offer_id: str) -> dict | None:
    """One-shot claim: returns the offer and removes it (an offer lives for
    exactly one reply), or None for unknown / expired / not-yours."""
    offer = _OFFERS.pop((user_id, offer_id), None)
    if offer is None or offer["expires"] < time.monotonic():
        return None
    return offer
