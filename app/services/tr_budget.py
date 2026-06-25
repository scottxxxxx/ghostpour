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

from datetime import datetime, timezone

import aiosqlite

# Allowed overage above the cap before the next call is blocked. Matches
# app/services/budget_gate.py so TR and SS behave consistently at the boundary.
OVERAGE_TOLERANCE_USD = 0.05

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
    apply: config absent, `enabled` false, entitlement missing/unrecognized, or
    the cap is -1 (unlimited). None → caller does not block (fail open)."""
    if not budget_cfg or not budget_cfg.get("enabled"):
        return None
    caps = budget_cfg.get("monthly_cost_limit_usd") or {}
    cap = caps.get((entitlement or "").strip().lower())
    if cap is None or cap == -1:
        return None
    return float(cap)


async def tr_month_spend_usd(db: aiosqlite.Connection, user_id: str) -> float:
    """Sum of the user's realized Tech Rehearsal spend this UTC month."""
    cur = await db.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log "
        "WHERE user_id = ? AND app_id = ? AND request_timestamp >= ?",
        (user_id, _APP_ID, _month_start_iso()),
    )
    row = await cur.fetchone()
    return float(row[0] or 0.0) if row else 0.0


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
