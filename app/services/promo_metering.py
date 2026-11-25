"""Promo calls that are served off the allowance (Scott, 2026-11-25).

The first one is `compare_summary`: a Shoulder Surf user running the
on-device Apple model sometimes gets "See what Shoulder Surf AI would write"
on a meeting Apple summarized. The tap sends SS's normal Generate summary
request (call_type summary, prompt_mode ReanalyzeSummary) plus
metadata.promo = "compare_summary". That call:

  * is refused for anonymous accounts (403 promo_requires_sign_in). A
    signed-out client cannot reach /v1/chat at all (401 from the auth
    dependency), so anonymous is the only signed-out shape that gets here;
  * is served free once per account per UTC calendar month. It does not move
    users.monthly_used_usd and the budget gate does not stop it;
  * answers 429 promo_exhausted on the second try in the month, so SS hides
    the offer. It is never quietly served as a normal metered call;
  * is honored only with call_type summary. Any other call_type, or a promo
    value we do not know, is ignored, logged, and metered normally.

COUNTING IS A CLAIM, NOT A COUNT. A row in promo_redemptions keyed on
(user_id, promo, month) is claimed with one INSERT before the model call.
SQLite applies that INSERT atomically, so two simultaneous taps cannot both
win: the loser sees zero changed rows and gets 429. Counting served
usage_log rows instead would let both taps pass the check, because neither
has been served when the other one looks. The claim becomes `served` the
moment a provider response exists. If the request leaves without one (an
upstream failure, a gate that refused it), the chat route wrapper deletes
the claim so the promo is still there for the next tap.

A claim left behind by a process that died mid call would otherwise eat the
month's promo. A `claimed` row older than CLAIM_STALE_SECONDS can be taken
over by the next tap, in the same single statement.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("cloudzap.promo_metering")

COMPARE_SUMMARY = "compare_summary"

# Each promo, with the only call_type it is honored on and how many per month.
PROMOS: dict[str, dict] = {
    COMPARE_SUMMARY: {"call_type": "summary", "per_month": 1},
}

# The usage_log column that marks a promo row. NULL on every metered row.
# The allowance excludes rows where it is set; see app/services/allowance.py.
USAGE_LOG_COLUMN = "promo"

# Server-stamped metadata key carrying the HONORED promo to log_usage. The
# client's own value rides under `promo`; this one is written by GP only and
# is cleared on arrival so a client cannot mark its own rows.
APPLIED_META_KEY = "promo_applied"

CLAIM_STALE_SECONDS = 600

CODE_REQUIRES_SIGN_IN = "promo_requires_sign_in"
CODE_EXHAUSTED = "promo_exhausted"


def _now() -> datetime:
    """The clock, in one place so tests can move it across a month."""
    return datetime.now(timezone.utc)


def month_key(now: datetime | None = None) -> str:
    """UTC calendar month, "YYYY-MM"."""
    return (now or _now()).astimezone(timezone.utc).strftime("%Y-%m")


def next_month_iso(now: datetime | None = None) -> str:
    n = (now or _now()).astimezone(timezone.utc)
    first = n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return nxt.isoformat()


def resolve(promo: object, call_type: object, *, app_meters_on_its_own: bool) -> str | None:
    """The promo to honor for this request, or None.

    Every refusal to honor a promo the client DID send is logged, because the
    client believes it asked for a free call and the row will say metered.
    """
    if promo is None or promo == "":
        return None
    if not isinstance(promo, str) or promo not in PROMOS:
        logger.info("promo_ignored promo=%r call_type=%s reason=unknown_promo",
                    promo, call_type)
        return None
    if call_type != PROMOS[promo]["call_type"]:
        logger.info("promo_ignored promo=%s call_type=%s reason=wrong_call_type "
                    "(metered normally)", promo, call_type)
        return None
    if app_meters_on_its_own:
        # The promo is defined against the shared account allowance. An app
        # that meters itself has its own cap and its own gates, and a promo
        # must not walk past those.
        logger.info("promo_ignored promo=%s call_type=%s reason=app_meters_on_its_own "
                    "(metered normally)", promo, call_type)
        return None
    return promo


async def claim(db: aiosqlite.Connection, user_id: str, promo: str,
                app_id: str | None, now: datetime | None = None) -> str | None:
    """Claim this month's promo for the user. Returns a claim token, or None
    when the month's promo is already claimed or served.

    One statement, so it is atomic against a concurrent claim: an INSERT that
    either creates the row or takes over a STALE unserved claim, and changes
    nothing otherwise.
    """
    n = now or _now()
    token = uuid.uuid4().hex
    stale_before = (n - timedelta(seconds=CLAIM_STALE_SECONDS)).isoformat()
    cur = await db.execute(
        """INSERT INTO promo_redemptions
             (user_id, promo, month, status, claim_token, claimed_at, app_id)
           VALUES (?, ?, ?, 'claimed', ?, ?, ?)
           ON CONFLICT(user_id, promo, month) DO UPDATE SET
             claim_token = excluded.claim_token,
             claimed_at = excluded.claimed_at,
             app_id = excluded.app_id
           WHERE promo_redemptions.status = 'claimed'
             AND promo_redemptions.claimed_at < ?""",
        (user_id, promo, month_key(n), token, n.isoformat(), app_id, stale_before),
    )
    await db.commit()
    return token if cur.rowcount == 1 else None


async def mark_served(db: aiosqlite.Connection, user_id: str, promo: str,
                      token: str, now: datetime | None = None) -> None:
    n = now or _now()
    await db.execute(
        """UPDATE promo_redemptions SET status = 'served', served_at = ?
           WHERE user_id = ? AND promo = ? AND claim_token = ?""",
        (n.isoformat(), user_id, promo, token),
    )
    await db.commit()


async def release(db: aiosqlite.Connection, user_id: str, promo: str, token: str) -> None:
    """Give the promo back: this request claimed it and never got a response.
    Deletes only this request's own unserved claim."""
    await db.execute(
        """DELETE FROM promo_redemptions
           WHERE user_id = ? AND promo = ? AND claim_token = ? AND status = 'claimed'""",
        (user_id, promo, token),
    )
    await db.commit()


def remaining_after_use(promo: str) -> int:
    """What the echo reports after a served promo call: per_month minus the
    one just used. Always 0 for compare_summary."""
    return max(0, int(PROMOS[promo]["per_month"]) - 1)


def echo_header(promo: str) -> str:
    """X-Promo response header value, e.g. "compare_summary; remaining=0"."""
    return f"{promo}; remaining={remaining_after_use(promo)}"


def echo_body(promo: str) -> dict:
    """`promo` member of the JSON response body."""
    return {"code": promo, "applied": True, "remaining": remaining_after_use(promo)}
