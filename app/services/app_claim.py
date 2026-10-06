"""The access token's `app` claim, and where a mismatch is refused.

Until 2026-10-05 the token carried no app, so X-App-ID was honor system: a
token minted for ShoulderSurf could call N-400 by sending `X-App-ID: n400`,
and N-400's lane has a -1 flat cap. Nobody had done it, but nothing stopped
it, and anonymous N-400 accounts with a real allowance make the claim matter.

Every token minted from now on carries a claim: the app it was minted for,
or UNSCOPED when the mint named no app. Tokens minted before carry none and
are served as before; they expire within jwt_access_token_expire_minutes (60)
of the deploy, and refresh never turns one into an N-400 token (below).

STRICT, not everywhere. A mismatch is REFUSED only when either side is a
strict app. Everywhere else it is logged (`token_app_mismatch_observed`) and
served, because nothing has measured whether a real client shares one token
across ShoulderSurf, Tech Rehearsal and the Companion, and turning that on
blind could sign real users out. The log line is how to find out before
widening the set. A request with no usable X-App-ID is not checked: it is
not attributed to any app, so no app's lane or allowance applies to it.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger("ghostpour.app_claim")

STRICT_APP_CLAIM_APPS = frozenset({"n400"})
UNSCOPED = "unscoped"
CODE = "token_app_mismatch"


def known(app_id: str | None) -> str | None:
    return app_id if app_id and app_id != "unknown" else None


def mint_claim(app_id: str | None) -> str:
    """The claim a freshly minted token carries."""
    return known(app_id) or UNSCOPED


def refuse_reason(claim: str | None, request_app: str | None) -> str | None:
    """Why this token may not call this app, or None to serve it."""
    req = known(request_app)
    if req is None or claim is None or claim == req:
        return None
    if claim in STRICT_APP_CLAIM_APPS or req in STRICT_APP_CLAIM_APPS:
        return "claim_differs_on_strict_app"
    logger.warning("token_app_mismatch_observed claim=%s request_app=%s", claim, req)
    return None


def enforce(payload: dict, request_app: str | None) -> None:
    reason = refuse_reason(payload.get("app"), request_app)
    if reason:
        logger.warning("token_app_mismatch_refused claim=%s request_app=%s reason=%s",
                       payload.get("app"), request_app, reason)
        raise HTTPException(status_code=401, detail={
            "code": CODE,
            "message": "This token was issued for a different app. Sign in again.",
        })


def refresh_scope(stored_app: str | None, header_app: str | None,
                  member_of_header_app: bool) -> tuple[str | None, str]:
    """(session app for the new refresh row, claim for the new access token).

    The stored session wins whenever it disagrees with the header and either
    is strict, so a ShoulderSurf session cannot be refreshed INTO an N-400
    token by changing a header. A legacy session with NO stored app reaches a
    strict app only when the account already belongs to it (a `user_apps`
    row, written at every mint that named the app); otherwise its session
    stays unattributed and its token UNSCOPED, so the next refresh cannot
    launder it either."""
    stored, header = known(stored_app), known(header_app)
    if stored and header and stored != header and (
            stored in STRICT_APP_CLAIM_APPS or header in STRICT_APP_CLAIM_APPS):
        return stored, stored
    if stored is None and header in STRICT_APP_CLAIM_APPS and not member_of_header_app:
        return None, UNSCOPED
    session = header or stored
    return session, mint_claim(session)
