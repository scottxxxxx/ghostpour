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
           ask_content: str = "", images: list[str] | None = None,
           lane_choice: str | None = None,
           artifact_id: str | None = None,
           search_enabled: bool = False) -> str:
    """Remember a live offer; returns its offer_id (rides the envelope).
    template_id marks a registry-matched offer: a confirm routes to the
    deterministic template lane instead of ad-hoc sandbox generation.
    ask_content is the ORIGINATING send's user_content — reply sends carry
    chat history only (client assembly), so the confirmed turn must run
    against the content the user was actually asking about (first live
    template run got 410 chars of Q/A and asked the user for the plan).
    images is the ORIGINATING send's base64 images, same reasoning; over
    the cap they are dropped and images_dropped marks the loss so the
    arming path can refuse to generate blind.
    artifact_id marks a CONTRACT-matched offer: the classifier resolved
    which artifact the user wants, so a confirm routes to the contract
    lane (our columns, our renderer) instead of the sandbox. Resolved on
    the OFFER turn because that is where the classifier ran; the confirm
    send carries chat history, not the original ask.
    search_enabled remembers that the ORIGINATING ask opted into web
    research. The build happens on the confirm turn, and the confirm is a
    different send: if the flag rides only the first one, a user who
    asked for a researched file gets one with no research in it and no
    sign anything was dropped. That is the worst shape of failure here,
    because it looks like success. Same reasoning as ask_content and
    images above — the offer remembers what the request WAS. The tier and
    monthly cap gate still runs on the confirm turn, so remembering the
    intent never bypasses the cap; it only stops us forgetting it.
    lane_choice marks an AMBIGUOUS plan/progress ask (Scott's ruling
    2026-08-11): "pending" means the version question has not been asked
    yet (teaser offers), "asked" means this offer IS the question and
    template_id holds the workbook default a custom reply overrides."""
    offer_id = uuid.uuid4().hex[:12]
    imgs = list(images or [])
    dropped = False
    if imgs and sum(len(i) for i in imgs) > _IMAGES_CAP_CHARS:
        imgs, dropped = [], True
    _OFFERS[(user_id, offer_id)] = {
        "format": fmt, "gist": gist, "template_id": template_id,
        "artifact_id": artifact_id,
        "ask_content": (ask_content or "")[:_ASK_CONTENT_CAP],
        "images": imgs, "images_dropped": dropped,
        "lane_choice": lane_choice,
        "search_enabled": bool(search_enabled),
        "expires": time.monotonic() + OFFER_TTL_S,
    }
    # opportunistic sweep — the map only ever holds in-flight conversations
    now = time.monotonic()
    for k in [k for k, v in _OFFERS.items() if v["expires"] < now]:
        _OFFERS.pop(k, None)
    return offer_id


def attach_answer(user_id: str, offer_id: str, answer: str) -> None:
    """Give a TEASER offer the answer it is offering to turn into a file.

    A teaser says "want this as a real file?", where `this` is the answer
    the user is reading. But the offer is minted BEFORE that answer
    exists, and it stores the originating QUESTION as `ask_content`. So a
    tap used to re-run the question with a build armed, and for a
    question like "are there any documents I need to update?" the model
    correctly answered it again in prose and produced no file at all.
    The user watched a progress card turn into a paragraph.

    Observed live 2026-08-17. The fix is to remember what was actually
    offered, which is only knowable once the turn has produced it.

    Silent no-op on an expired or unknown offer: the answer is a nicety
    on a card that has already gone.
    """
    offer = _OFFERS.get((user_id, offer_id))
    if offer is not None:
        offer["answer_content"] = (answer or "")[:_ASK_CONTENT_CAP]


def take(user_id: str, offer_id: str) -> dict | None:
    """One-shot claim: returns the offer and removes it (an offer lives for
    exactly one reply), or None for unknown / expired / not-yours."""
    offer = _OFFERS.pop((user_id, offer_id), None)
    if offer is None or offer["expires"] < time.monotonic():
        return None
    return offer


def peek(user_id: str, offer_id: str) -> dict | None:
    """Read a live offer WITHOUT claiming it.

    `take` is one-shot by design — an offer lives for exactly one reply
    — so anything that needs to know about the offer before the reply is
    interpreted has to look without consuming. The search gate is the
    case: it decides tier and monthly cap early in the request, well
    before the offer is claimed, and research intent stored on the offer
    has to be visible to it or the gate never sees the flag it is
    supposed to rule on.
    """
    offer = _OFFERS.get((user_id, offer_id))
    if offer is None or offer["expires"] < time.monotonic():
        return None
    return offer
