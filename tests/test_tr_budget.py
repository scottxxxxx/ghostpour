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
    # missing / unrecognized entitlement → None (don't block)
    assert tr_budget.cap_for_entitlement(CFG_ON, None) is None
    assert tr_budget.cap_for_entitlement(CFG_ON, "enterprise") is None
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
async def test_dormant_and_unknown_entitlement_never_block():
    db = await _seed_db()  # u2 way over cap
    # gate disabled → never blocks, even over cap
    block, info = await tr_budget.would_exceed_tr_budget(db, "u2", "free", 5.0, CFG_OFF)
    assert block is False and info is None
    # unrecognized entitlement → no cap → no block
    block, info = await tr_budget.would_exceed_tr_budget(db, "u2", "enterprise", 5.0, CFG_ON)
    assert block is False and info is None
    await db.close()


def test_apps_yml_enabled_with_caps():
    # Budget gate ENABLED at the 2026-07-05 cutover flip (TR entitlement build
    # verified live in the field), with the agreed caps.
    from app.routers.config import load_apps
    cfg = tr_budget.tr_budget_config(load_apps(force=True))
    assert cfg is not None
    assert cfg["enabled"] is True
    assert cfg["monthly_cost_limit_usd"] == {"free": 5.0, "paid": 25.0}
