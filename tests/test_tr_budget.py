"""Tech Rehearsal per-app budget gate (#249, app/services/tr_budget.py).

Caps TR spend per UTC month from usage_log, keyed on the X-TR-Entitlement
plan, independent of the SS tier. Dormant until apps.techrehearsal.budget
.enabled is true.
"""

from datetime import datetime, timezone

import aiosqlite
import pytest

from app.services import tr_budget

CFG_ON = {"enabled": True, "monthly_cost_limit_usd": {"free": 5.0, "paid": 25.0}}
CFG_OFF = {"enabled": False, "monthly_cost_limit_usd": {"free": 5.0, "paid": 25.0}}


# --- pure helpers -----------------------------------------------------------

def test_tr_budget_config_extraction():
    reg = {"apps": {"techrehearsal": {"budget": CFG_ON}, "shouldersurf": {}}}
    assert tr_budget.tr_budget_config(reg) == CFG_ON
    assert tr_budget.tr_budget_config({"apps": {"shouldersurf": {}}}) is None
    assert tr_budget.tr_budget_config({}) is None


def test_cap_for_entitlement():
    # gate off / absent config → None (no cap, fail open)
    assert tr_budget.cap_for_entitlement(None, "free") is None
    assert tr_budget.cap_for_entitlement(CFG_OFF, "free") is None
    # enabled → per-plan caps, case-insensitive
    assert tr_budget.cap_for_entitlement(CFG_ON, "free") == 5.0
    assert tr_budget.cap_for_entitlement(CFG_ON, "PAID") == 25.0
    # MISSING / UNRECOGNISED entitlement → the MOST RESTRICTIVE cap, not None.
    # Changed 2026-09-02 (Scott). It used to mean "no cap", which was
    # survivable only while TR also charged the shared account meter: that
    # eventually stopped a runaway. Once TR came off that meter, an absent
    # header would have left the call capped by nothing at all, and the
    # header is client-supplied, so the least trustworthy input decided
    # whether a budget existed.
    assert tr_budget.cap_for_entitlement(CFG_ON, None) == 5.0
    assert tr_budget.cap_for_entitlement(CFG_ON, "enterprise") == 5.0
    assert tr_budget.cap_for_entitlement(CFG_ON, "") == 5.0
    # Not hardcoded to the string "free": it is the smallest bounded ceiling
    # on offer, so renaming the plans cannot reopen the hole.
    assert tr_budget.cap_for_entitlement(
        {"enabled": True, "monthly_cost_limit_usd": {"starter": 2.0, "pro": 30.0}},
        "who-knows") == 2.0
    # Unlimited entries are excluded from that choice, or "most restrictive"
    # would resolve to no cap, which is the bug inverted.
    assert tr_budget.cap_for_entitlement(
        {"enabled": True, "monthly_cost_limit_usd": {"free": 5.0, "pro": -1}},
        "who-knows") == 5.0
    # Nothing bounded anywhere: the gate genuinely cannot run, and says so.
    assert tr_budget.cap_for_entitlement(
        {"enabled": True, "monthly_cost_limit_usd": {"pro": -1}}, "x") is None
    # explicit unlimited → None
    assert tr_budget.cap_for_entitlement(
        {"enabled": True, "monthly_cost_limit_usd": {"free": -1}}, "free") is None


# --- spend accounting + decision (async db) ---------------------------------

async def _seed_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE usage_log (user_id TEXT, app_id TEXT, "
        "estimated_cost_usd REAL, request_timestamp TEXT)"
    )
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("u1", "techrehearsal", 2.5, now),     # counted
        ("u1", "techrehearsal", 1.5, now),     # counted  → u1 TR = 4.0
        ("u1", "shouldersurf", 10.0, now),     # other app, excluded
        ("u2", "techrehearsal", 9.0, now),     # other user, excluded
        ("u1", "techrehearsal", 7.0, "2020-01-01T00:00:00+00:00"),  # last period, excluded
        ("u1", "techrehearsal", None, now),    # error row (NULL cost), ignored by SUM
    ]
    await db.executemany("INSERT INTO usage_log VALUES (?,?,?,?)", rows)
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_month_spend_scopes_user_app_and_period():
    db = await _seed_db()
    assert await tr_budget.tr_month_spend_usd(db, "u1") == 4.0
    assert await tr_budget.tr_month_spend_usd(db, "u2") == 9.0
    assert await tr_budget.tr_month_spend_usd(db, "nobody") == 0.0
    await db.close()


@pytest.mark.asyncio
async def test_would_exceed_under_and_over_cap():
    db = await _seed_db()  # u1 TR spend = 4.0, free cap = 5.0
    # under cap, small marginal → no block
    block, info = await tr_budget.would_exceed_tr_budget(db, "u1", "free", 0.5, CFG_ON)
    assert block is False and info["spent"] == 4.0 and info["cap"] == 5.0
    # marginal would push past cap + tolerance → block
    block, _ = await tr_budget.would_exceed_tr_budget(db, "u1", "free", 1.5, CFG_ON)
    assert block is True
    # paid cap is higher → same spend is fine
    block, _ = await tr_budget.would_exceed_tr_budget(db, "u1", "paid", 1.5, CFG_ON)
    assert block is False
    await db.close()


@pytest.mark.asyncio
async def test_already_over_cap_blocks_without_estimate():
    db = await _seed_db()  # u2 TR spend = 9.0 > free cap 5.0
    block, info = await tr_budget.would_exceed_tr_budget(db, "u2", "free", None, CFG_ON)
    assert block is True and info["spent"] == 9.0
    await db.close()


@pytest.mark.asyncio
async def test_a_dormant_gate_never_blocks_but_an_unknown_entitlement_now_does():
    """Two cases that used to share an answer and no longer do.

    DORMANT is a deliberate operator decision: `enabled: false` means the
    gate is switched off and must never block, even for a user far over any
    cap. Unchanged.

    UNKNOWN ENTITLEMENT was treated the same way and should not have been.
    An absent or unrecognised header is not a decision, it is a missing
    input, and the input is client-supplied. Since 2026-09-02 it resolves to
    the most restrictive cap, so a user over that cap is blocked and sees the
    budget_exhausted envelope rather than spending without a ceiling.
    """
    db = await _seed_db()  # u2 TR spend 9.0, over the 5.0 free cap
    block, info = await tr_budget.would_exceed_tr_budget(db, "u2", "free", 5.0, CFG_OFF)
    assert block is False and info is None, "a dormant gate must stay dormant"

    block, info = await tr_budget.would_exceed_tr_budget(db, "u2", "enterprise", 5.0, CFG_ON)
    assert block is True, "an unknown entitlement must not buy an unlimited budget"
    assert info["cap"] == 5.0 and info["spent"] == 9.0

    # And a user UNDER the fallback cap is still served: failing closed must
    # not mean refusing everyone whose header went missing.
    block, _ = await tr_budget.would_exceed_tr_budget(db, "u1", None, 0.1, CFG_ON)
    assert block is False
    await db.close()


def test_apps_yml_enabled_with_caps():
    # Budget gate ENABLED at the 2026-07-05 cutover flip (TR entitlement build
    # verified live in the field), with the agreed caps.
    from app.routers.config import load_apps
    cfg = tr_budget.tr_budget_config(load_apps(force=True))
    assert cfg is not None
    assert cfg["enabled"] is True
    assert cfg["monthly_cost_limit_usd"] == {"free": 5.0, "paid": 25.0}
