"""Spend caps for anonymous accounts of an app that gives them an allowance.

ShoulderSurf's anonymous accounts (2026-09-26) get NOTHING until a purchase
binds a plan (app/services/allowance.py), because minting one is a single
unauthenticated call. N-400 is different (Scott, 2026-10-05): the interview,
which is all of its GP spend, is free, and the user pays only at print. So an
anonymous N-400 install needs a real allowance BEFORE any purchase, and that
allowance is only safe with two ceilings behind it:

    per_install_lifetime_usd   what ONE install may ever spend in this app.
                               Lifetime rather than monthly because one
                               install is one application (the purchase is a
                               consumable, one per application), so a monthly
                               reset would hand the same install a second
                               interview for free every month.
    daily_all_installs_usd     what ALL anonymous accounts together may spend
                               in this app per UTC day. Minting is free and
                               rate limited only per IP, so the per-install
                               cap alone is "cap x installs a scripter
                               bothers to mint". This is the number that
                               bounds a bad day.

Both are dials in the app's served budget doc (`n400/budget`, key
`anonymous`), next to the flat cap. A missing, unreadable or -1 per-install
value means NOT CAPPED, and an anonymous account then falls back to the
ShoulderSurf rule (no allowance at all), so a config failure here fails
closed. The uncapped-and-reachable audit (app_budget) also reads this: an app
in ANONYMOUS_APPS whose anonymous cap is not set is refused and alerted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.models.user import ANONYMOUS_SUB_PREFIX

logger = logging.getLogger("ghostpour.anonymous_budget")

CODE_APPLICATION = "n400_application_allowance_used"
CODE_INSTALL = "n400_install_allowance_used"
CODE_DAILY = "n400_daily_capacity_reached"

# Covered by idx_usage_user_app_date_cost (user_id, app_id, ...): a prefix
# match on the first two columns, so the table is never read. Planned in
# tests/test_anonymous_budget.py.
# Per application (Scott, 2026-10-06: "do the limit per application"). One
# person may file their own N-400 and then a spouse's on the same phone, so
# the allowance follows the application (metadata.case_id), not the install.
# Calls with no usable case_id share one bucket per install (case_id IS NULL).
APPLICATION_SPEND_SQL = (
    "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log "
    "WHERE user_id = ? AND app_id = ? AND case_id IS ?"
)

LIFETIME_SPEND_SQL = (
    "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log "
    "WHERE user_id = ? AND app_id = ?"
)

# Every anonymous account's spend in this app since the start of the UTC
# day. idx_usage_app_date_user_cost covers the usage_log side, and users is
# read by primary key. Runs on every anonymous turn, so it is planned too.
DAILY_SPEND_SQL = (
    "SELECT COALESCE(SUM(l.estimated_cost_usd), 0) FROM usage_log l "
    "JOIN users u ON u.id = l.user_id "
    "WHERE l.app_id = ? AND l.request_timestamp >= ? "
    "AND substr(u.apple_sub, 1, " + str(len(ANONYMOUS_SUB_PREFIX)) + ") = ?"
)


def _doc(remote_configs: dict | None, apps_registry: dict, app_id: str | None) -> dict:
    from app.services.app_budget import budget_config
    slug = budget_config(apps_registry, app_id).get("config_slug")
    doc = (remote_configs or {}).get(slug) if slug else None
    anon = doc.get("anonymous") if isinstance(doc, dict) else None
    return anon if isinstance(anon, dict) else {}


def _usd(value) -> float | None:
    """A positive dollar amount, or None for absent, -1, or unreadable."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 and v != -1 else None


def caps(remote_configs: dict | None, apps_registry: dict,
         app_id: str | None) -> dict | None:
    """{'per_application': $ or None, 'per_install': $, 'daily': $ or None}
    when this app gives anonymous accounts an allowance, else None (no
    allowance, the ShoulderSurf rule). per_install stays REQUIRED: a client
    picks its own case_id, so a fresh id on every call would dodge the
    per-application cap, and the install's lifetime ceiling is what bounds
    that."""
    doc = _doc(remote_configs, apps_registry, app_id)
    per_install = _usd(doc.get("per_install_lifetime_usd"))
    if per_install is None:
        return None
    return {"per_application": _usd(doc.get("per_application_usd")),
            "per_install": per_install, "daily": _usd(doc.get("daily_all_installs_usd"))}


def case_id(value) -> str | None:
    """The request's metadata.case_id as stored in usage_log (a lowercased
    UUID or 8 hex characters), or None when absent or malformed, so the gate and the meter agree
    on which bucket a call belongs to."""
    from app.services.usage_tracker import normalize_case_id
    return normalize_case_id(value)


def _day_start_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def next_day_iso(now: datetime | None = None) -> str:
    """First instant of the next UTC day, ISO (formatting is the client's job)."""
    now = now or datetime.now(timezone.utc)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)).isoformat()


async def check(db: aiosqlite.Connection, user_id: str, app_id: str,
                estimate_usd: float | None, caps_: dict,
                case: str | None = None) -> tuple[str | None, dict]:
    """(code, info): code is CODE_APPLICATION, CODE_INSTALL or CODE_DAILY when
    this call must be refused, else None. Checked narrowest first (this
    application, then this install, then everyone today), so a user hears
    about their own allowance rather than "try tomorrow".

    An unpriceable call (estimate None) still cannot get an install that is
    ALREADY at its cap through; it can only let one final call cross it, the
    same rule as the flat app budget.
    """
    est = estimate_usd or 0.0
    info = {"app_id": app_id, "case_id": case, "estimate": estimate_usd,
            "per_application": caps_.get("per_application"), "application_spent": None,
            "per_install": caps_["per_install"], "daily": caps_.get("daily"),
            "daily_spent": None}
    per_app = caps_.get("per_application")
    if per_app is not None:
        row = await (await db.execute(APPLICATION_SPEND_SQL, (user_id, app_id, case))).fetchone()
        info["application_spent"] = a = float(row[0] or 0.0) if row else 0.0
        if a >= per_app or a + est > per_app:
            info["spent"] = a
            return CODE_APPLICATION, info
    row = await (await db.execute(LIFETIME_SPEND_SQL, (user_id, app_id))).fetchone()
    spent = float(row[0] or 0.0) if row else 0.0
    info["spent"] = spent
    if spent >= caps_["per_install"] or spent + est > caps_["per_install"]:
        return CODE_INSTALL, info
    daily = caps_.get("daily")
    if daily is not None:
        row = await (await db.execute(
            DAILY_SPEND_SQL, (app_id, _day_start_iso(), ANONYMOUS_SUB_PREFIX))).fetchone()
        info["daily_spent"] = day = float(row[0] or 0.0) if row else 0.0
        if day >= daily or day + est > daily:
            return CODE_DAILY, info
    return None, info


def refusal_copy(remote_configs: dict | None, apps_registry: dict,
                 app_id: str | None, code: str) -> dict | None:
    """The served words for this refusal ({text: {locale: str}}), or None."""
    doc = _doc(remote_configs, apps_registry, app_id)
    key = {CODE_APPLICATION: "application_allowance_used",
           CODE_INSTALL: "install_allowance_used"}.get(code, "daily_capacity_reached")
    copy = doc.get(key)
    return copy if isinstance(copy, dict) else None


async def report_daily_cap(db, app_id: str, info: dict, *,
                           from_addr: str = "alerts@noreply.invalid") -> None:
    """Raise ONE incident per app per UTC day when the daily ceiling refuses a
    call, so Scott hears about a bad day while it is happening rather than
    from the dashboard later. Never raises: an alert must not fail the turn."""
    try:
        from app.services.alerting import report_incident
        await report_incident(
            db, category="anonymous_daily_cap",
            subject=f"{app_id}:{_day_start_iso()[:10]}",
            details={"app_id": app_id, "daily_cap_usd": info.get("daily"),
                     "spent_today_usd": round(info.get("daily_spent") or 0.0, 4)},
            from_addr=from_addr)
    except Exception as e:  # noqa: BLE001
        logger.warning("anonymous_daily_cap report failed (non-fatal): %s", e)
