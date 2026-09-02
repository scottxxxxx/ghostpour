"""Per-app budget gate for Tech Rehearsal (#249).

Tech Rehearsal's free/paid plan is a TR-side entitlement, sent per call as the
`X-TR-Entitlement: free|paid` header. It is INDEPENDENT of the user's
ShoulderSurf subscription tier, and TR users share one user row with SS (same
Apple-team `sub`), so TR spend cannot use the per-user `monthly_used_usd` bucket
the SS budget gate relies on.

Instead we cap TR spend per calendar month (UTC) by summing the user's
`techrehearsal` rows in `usage_log` (`estimated_cost_usd` holds the realized
cost, with `app_id` on each row), against an entitlement-keyed cap read from
`apps.yml` (`apps.techrehearsal.budget`).

DORMANT until `apps.techrehearsal.budget.enabled` is true. Enable only once
TR's entitlement-carrying build is live in the field (so real calls actually
send `X-TR-Entitlement`); until then this returns "no block" for everyone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

# Allowed overage above the cap before the next call is blocked. Matches
# app/services/budget_gate.py so TR and SS behave consistently at the boundary.
OVERAGE_TOLERANCE_USD = 0.05

logger = logging.getLogger("ghostpour.tr_budget")

_APP_ID = "techrehearsal"


def _month_start_iso() -> str:
    """First instant of the current UTC calendar month, ISO-8601."""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def tr_budget_config(apps_registry: dict) -> dict | None:
    """Return `apps.techrehearsal.budget` from a load_apps() registry, or None."""
    app = (apps_registry.get("apps", {}) or {}).get(_APP_ID, {}) or {}
    return app.get("budget")


def cap_for_entitlement(budget_cfg: dict | None, entitlement: str | None) -> float | None:
    """USD monthly cap for this entitlement, or None when the gate must not
    apply: config absent, `enabled` false, or the cap is -1 (unlimited).

    A MISSING OR UNRECOGNISED ENTITLEMENT NO LONGER MEANS NO CAP (2026-09-02,
    Scott). It used to, and that was survivable only because TR also charged
    the shared account meter, which eventually stopped a runaway. Once Scott
    ruled the apps multitenant and TR came off that meter, an absent
    `X-TR-Entitlement` would have left the call uncapped by anything at all:
    the header is client-supplied, so the least trustworthy input decided
    whether a budget existed.

    Unknown now resolves to the MOST RESTRICTIVE cap the config defines,
    which is `free` in practice. Deliberately not hardcoded to the string
    "free": a config that renames its plans would silently fail open again,
    and the property wanted here is "the smallest ceiling on offer", not "the
    plan we happen to call free". Unlimited entries (-1) are excluded from
    that choice, or the most restrictive cap would be no cap.

    A caller whose header went missing therefore gets the free ceiling and,
    past it, a visible budget_exhausted envelope with a CTA. That is worth
    more than unbounded spend nobody sees, and it is recoverable by sending
    the header.
    """
    if not budget_cfg or not budget_cfg.get("enabled"):
        return None
    caps = budget_cfg.get("monthly_cost_limit_usd") or {}
    key = (entitlement or "").strip().lower()
    if key in caps:
        cap = caps[key]
        return None if cap == -1 else float(cap)

    bounded = [float(v) for v in caps.values()
               if isinstance(v, (int, float)) and v != -1]
    if not bounded:
        logger.error(
            "tr_budget: entitlement %r unrecognised and no bounded cap exists; "
            "the gate cannot run", entitlement)
        return None
    fallback = min(bounded)
    logger.warning(
        "tr_budget: entitlement %r unrecognised, applying the most "
        "restrictive cap $%.2f rather than none", entitlement, fallback)
    return fallback


async def tr_month_spend_usd(db: aiosqlite.Connection, user_id: str) -> float:
    """Sum of the user's realized Tech Rehearsal spend this UTC month."""
    cur = await db.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log "
        "WHERE user_id = ? AND app_id = ? AND request_timestamp >= ?",
        (user_id, _APP_ID, _month_start_iso()),
    )
    row = await cur.fetchone()
    return float(row[0] or 0.0) if row else 0.0


def month_reset_iso() -> str:
    """First instant of the NEXT UTC calendar month: when the allowance
    renews. Sent as machine-readable ISO rather than a formatted date,
    because formatting a date is the client's job and we do not know the
    user's locale or calendar."""
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc).isoformat()


# Fallback so a caller never has to handle None, and so an unreachable or
# malformed config degrades to a sentence rather than to silence. A rejection
# with no reason reads as the app being broken, which is worse than the
# rejection itself.
_FALLBACK_EXHAUSTED: dict = {
    "kind": "budget_exhausted",
    "text": "You have reached this month's Tech Rehearsal usage limit.",
    "renews_prefix": "Renews",
    "action": None,
}


def exhausted_copy(remote_configs: dict | None, entitlement: str | None) -> dict:
    """The served explanation for a blocked TR call.

    Ours to author, not the client's to invent. A hard stop is one of the few
    places a user is told no, and it stays factual: what happened and when it
    renews. No apology, no hedging, and no comparison to what a paid plan
    would have allowed.

    Keyed by entitlement because free and paid hit the same gate for
    different reasons: free has spent an allowance, paid has hit a usage
    limit, and only one of those has an upgrade to offer.
    """
    doc = (remote_configs or {}).get("techrehearsal/budget") or {}
    table = doc.get("exhausted") or {}
    entry = table.get((entitlement or "").strip().lower())
    return dict(entry) if isinstance(entry, dict) else dict(_FALLBACK_EXHAUSTED)


async def would_exceed_tr_budget(
    db: aiosqlite.Connection,
    user_id: str,
    entitlement: str | None,
    estimated_cost_usd: float | None,
    budget_cfg: dict | None,
) -> tuple[bool, dict | None]:
    """Decide whether this TR call should be blocked on budget.

    Returns (block, info). `block` is False whenever the gate doesn't apply
    (dormant / no recognized entitlement / unlimited). Blocks when the month's
    spend already meets the cap, or when adding this call's estimate would push
    past cap + tolerance. A missing estimate only skips the marginal check; the
    already-over-cap check still fires.
    """
    cap = cap_for_entitlement(budget_cfg, entitlement)
    if cap is None:
        return False, None
    spent = await tr_month_spend_usd(db, user_id)
    block = spent >= cap
    if not block and estimated_cost_usd is not None:
        block = (spent + estimated_cost_usd) > cap + OVERAGE_TOLERANCE_USD
    return block, {"cap": cap, "spent": spent, "entitlement": (entitlement or "").strip().lower()}
