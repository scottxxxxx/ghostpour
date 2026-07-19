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


_ASK_CONTENT_CAP = 200_000
# Stored originating images, total base64 chars (~11MB binary). Same
# argument as ask_content: reply sends carry chat history only, so an
# image-sourced build must run against the photo the user actually sent
# (2026-07-19: an image-less confirmed turn invented a spreadsheet).
_IMAGES_CAP_CHARS = 15_000_000


def create(user_id: str, fmt: str, gist: str, template_id: str | None = None,
           ask_content: str = "", images: list[str] | None = None) -> str:
    """Remember a live offer; returns its offer_id (rides the envelope).
    template_id marks a registry-matched offer: a confirm routes to the
    deterministic template lane instead of ad-hoc sandbox generation.
    ask_content is the ORIGINATING send's user_content — reply sends carry
    chat history only (client assembly), so the confirmed turn must run
    against the content the user was actually asking about (first live
    template run got 410 chars of Q/A and asked the user for the plan).
    images is the ORIGINATING send's base64 images, same reasoning; over
    the cap they are dropped and images_dropped marks the loss so the
    arming path can refuse to generate blind."""
    offer_id = uuid.uuid4().hex[:12]
    imgs = list(images or [])
    dropped = False
    if imgs and sum(len(i) for i in imgs) > _IMAGES_CAP_CHARS:
        imgs, dropped = [], True
    _OFFERS[(user_id, offer_id)] = {
        "format": fmt, "gist": gist, "template_id": template_id,
        "ask_content": (ask_content or "")[:_ASK_CONTENT_CAP],
        "images": imgs, "images_dropped": dropped,
        "expires": time.monotonic() + OFFER_TTL_S,
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
