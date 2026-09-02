"""Per-app flat spend caps, and the two meters they disentangle.

The bug this covers: `users.monthly_used_usd` is one row per ACCOUNT and
every app under this developer team shares it, so a Tech Rehearsal or N-400
call drew down the user's ShoulderSurf tier allowance. `record_cost` took no
app argument, one line above a `log_usage` call that did.

Direction matters in both fixes here, so the tests assert the direction and
not just the behaviour:

  - leaving the shared meter REQUIRES having a cap of your own, because an
    app that leaves without one becomes uncapped rather than double-metered;
  - an unreadable cap config falls back to the apps.yml floor, never to
    unlimited.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from app.routers.config import load_apps
from app.services import app_budget

FLAT_ON = {"enabled": True, "shape": "flat", "config_slug": "n400/budget",
           "monthly_cost_limit_usd": 5.0, "own_account_meter": True}


def _reg(budget: dict | None, app: str = "n400") -> dict:
    return {"apps": {app: ({"budget": budget} if budget is not None else {}),
                     "shouldersurf": {}}}


# --- who leaves the shared account meter ------------------------------------

def test_leaving_the_shared_meter_requires_an_enforced_cap():
    """own_account_meter alone must not be enough. An app that declares the
    intention but has no running cap would go from double-metered to
    uncapped, which is worse than the bug."""
    assert app_budget.meters_on_its_own(_reg(FLAT_ON), "n400") is True
    assert app_budget.meters_on_its_own(
        _reg({**FLAT_ON, "enabled": False}), "n400") is False
    assert app_budget.meters_on_its_own(
        _reg({**FLAT_ON, "own_account_meter": False}), "n400") is False
    assert app_budget.meters_on_its_own(_reg(None), "n400") is False


def test_unknown_and_absent_app_ids_stay_on_the_shared_meter():
    """Today's behaviour is the fallthrough. An app id we do not recognise
    must keep charging the account row, never silently become free."""
    for app_id in (None, "", "  ", "unknown", "not-an-app"):
        assert app_budget.meters_on_its_own(_reg(FLAT_ON), app_id) is False


def test_every_app_but_the_default_meters_itself():
    """Pins Scott's ruling of 2026-09-02, which is broader than the flip:
    the apps are MULTITENANT and a shared SIWA identity does not mean shared
    anything else. Tech Rehearsal came off the shared meter here; it had the
    same leak as N-400 and had been double-metered since 2026-07-05.

    ShoulderSurf stays False and that is not an oversight: it IS the account
    meter. users.monthly_used_usd is what the SS tier allowance is measured
    against, so SS metering itself separately would mean metering it twice
    against the same number.
    """
    reg = load_apps()
    assert app_budget.meters_on_its_own(reg, "n400") is True
    assert app_budget.meters_on_its_own(reg, "techrehearsal") is True
    assert app_budget.meters_on_its_own(reg, "shouldersurf") is False
    # And the direction of the rule, not just today's answer: anything that
    # left the shared meter must carry its own enabled budget.
    for app_id, entry in (reg["apps"] or {}).items():
        if app_budget.meters_on_its_own(reg, app_id):
            assert (entry.get("budget") or {}).get("enabled") is True, app_id


# --- resolving the cap ------------------------------------------------------

def test_served_value_is_the_dial_and_beats_the_floor():
    configs = {"n400/budget": {"version": 1, "monthly_cost_limit_usd": 2.0}}
    assert app_budget.flat_cap_usd(configs, _reg(FLAT_ON), "n400") == 2.0


@pytest.mark.parametrize("configs", [
    None,
    {},
    {"n400/budget": "not a document"},
    {"n400/budget": {"version": 1}},
    {"n400/budget": {"version": 1, "monthly_cost_limit_usd": "five dollars"}},
])
def test_an_unreadable_served_cap_falls_back_to_the_floor_not_to_unlimited(configs):
    assert app_budget.flat_cap_usd(configs, _reg(FLAT_ON), "n400") == 5.0


def test_only_an_explicit_minus_one_means_unlimited():
    configs = {"n400/budget": {"version": 1, "monthly_cost_limit_usd": -1}}
    assert app_budget.flat_cap_usd(configs, _reg(FLAT_ON), "n400") is None


def test_the_flat_gate_does_not_run_for_other_shapes_or_when_disabled():
    configs = {"n400/budget": {"version": 1, "monthly_cost_limit_usd": 2.0}}
    entitlement_shaped = {**FLAT_ON, "shape": "entitlement"}
    assert app_budget.flat_cap_usd(configs, _reg(entitlement_shaped), "n400") is None
    assert app_budget.flat_cap_usd(
        configs, _reg({**FLAT_ON, "enabled": False}), "n400") is None
    assert app_budget.flat_cap_usd(configs, _reg(None), "n400") is None


def test_enabled_flat_budget_with_no_number_anywhere_cannot_gate():
    """Not a silent unlimited: it returns None so the gate does not run, and
    the module logs an error saying so. Asserted here so the shipped registry
    below is the thing keeping this from happening in production."""
    cfg = {"enabled": True, "shape": "flat", "config_slug": "n400/budget"}
    assert app_budget.flat_cap_usd({}, _reg(cfg), "n400") is None


def test_shipped_n400_cap_resolves_without_any_served_config():
    """CI has no config overlay, so this is the honest test of the floor."""
    assert app_budget.flat_cap_usd({}, load_apps(), "n400") == 5.0


# --- spend accounting -------------------------------------------------------

async def _seed_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE usage_log (user_id TEXT, app_id TEXT, "
        "estimated_cost_usd REAL, request_timestamp TEXT)"
    )
    now = datetime.now(timezone.utc).isoformat()
    await db.executemany("INSERT INTO usage_log VALUES (?,?,?,?)", [
        ("u1", "n400", 2.0, now),
        ("u1", "n400", 1.5, now),                                   # u1 n400 = 3.5
        ("u1", "shouldersurf", 40.0, now),                          # other app
        ("u1", "techrehearsal", 9.0, now),                          # other app
        ("u2", "n400", 4.9, now),                                   # other user
        ("u1", "n400", 7.0, "2020-01-01T00:00:00+00:00"),           # other period
        ("u1", "n400", None, now),                                  # error row
    ])
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_spend_is_scoped_to_user_app_and_month():
    db = await _seed_db()
    assert await app_budget.app_month_spend_usd(db, "u1", "n400") == 3.5
    assert await app_budget.app_month_spend_usd(db, "u2", "n400") == 4.9
    assert await app_budget.app_month_spend_usd(db, "u1", "shouldersurf") == 40.0
    assert await app_budget.app_month_spend_usd(db, "nobody", "n400") == 0.0
    await db.close()


@pytest.mark.asyncio
async def test_under_cap_passes_and_a_crossing_estimate_blocks():
    db = await _seed_db()  # u1 n400 = 3.5, cap 5.0
    block, info = await app_budget.would_exceed_flat_budget(db, "u1", "n400", 0.5, 5.0)
    assert block is False and info["spent"] == 3.5 and info["cap"] == 5.0
    block, _ = await app_budget.would_exceed_flat_budget(db, "u1", "n400", 2.0, 5.0)
    assert block is True
    await db.close()


@pytest.mark.asyncio
async def test_an_unpriceable_call_cannot_rescue_someone_already_over():
    """estimate=None is allowed, because pricing is not always resolvable.
    It may let one final call cross the line; it must not let a user who is
    ALREADY past it keep going."""
    db = await _seed_db()
    assert (await app_budget.would_exceed_flat_budget(
        db, "u1", "n400", None, 5.0))[0] is False       # 3.5, still under
    assert (await app_budget.would_exceed_flat_budget(
        db, "u1", "n400", None, 3.0))[0] is True        # 3.5, already over
    await db.close()


@pytest.mark.asyncio
async def test_no_cap_never_blocks():
    db = await _seed_db()
    block, info = await app_budget.would_exceed_flat_budget(db, "u1", "n400", 99.0, None)
    assert block is False and info["cap"] is None
    await db.close()


# --- the stop always says something ----------------------------------------

def test_served_copy_wins():
    configs = {"n400/budget": {"version": 1, "exhausted": {
        "kind": "budget_exhausted", "text": {"en": "served"}}}}
    assert app_budget.exhausted_copy(
        configs, _reg(FLAT_ON), "n400")["text"]["en"] == "served"


def test_a_bare_string_is_not_mistaken_for_empty_copy():
    """Tech Rehearsal's copy is a bare string and N-400's is a locale map. A
    shape check that knew only one would call the other empty and replace
    real served copy with the fallback."""
    configs = {"n400/budget": {"version": 1, "exhausted": {"text": "a string"}}}
    assert app_budget.exhausted_copy(
        configs, _reg(FLAT_ON), "n400")["text"] == "a string"


@pytest.mark.parametrize("configs", [
    {}, {"n400/budget": {"version": 1}},
    {"n400/budget": {"version": 1, "exhausted": {"text": {}}}},
    {"n400/budget": {"version": 1, "exhausted": {"text": {"en": "   "}}}},
])
def test_missing_copy_falls_back_to_a_sentence_in_the_right_shape(configs):
    """A rejection with no reason reads as the app being broken. The fallback
    must also be a LOCALE MAP: a bare string here would decode-fail on the
    only client that reads it."""
    copy = app_budget.exhausted_copy(configs, _reg(FLAT_ON), "n400")
    assert isinstance(copy["text"], dict)
    for locale in ("en", "es", "pt"):
        assert copy["text"][locale].strip()


def test_shipped_n400_copy_covers_every_wire_locale():
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).parent.parent / "config" / "remote"
                      / "n400" / "budget.json").read_text())
    for locale in ("en", "es", "pt"):
        assert doc["exhausted"]["text"][locale].strip()
    assert doc["monthly_cost_limit_usd"] == 5.0


# --- record_cost: the account row itself ------------------------------------

_TIER = SimpleNamespace(monthly_cost_limit_usd=10.0, trial_cost_limit_usd=None)


async def _users_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE users (id TEXT, monthly_used_usd REAL)")
    await db.execute("INSERT INTO users VALUES ('u1', 0.0)")
    await db.commit()
    return db


async def _used(db) -> float:
    cur = await db.execute("SELECT monthly_used_usd FROM users WHERE id = 'u1'")
    return float((await cur.fetchone())["monthly_used_usd"])


@pytest.mark.asyncio
@pytest.mark.parametrize("app_id", ["shouldersurf", None, "unknown", "not-an-app"])
async def test_apps_on_the_shared_meter_still_charge_the_account_row(app_id):
    from app.services.usage_tracker import UsageTracker
    db = await _users_db()
    await UsageTracker().record_cost(db, "u1", 1.25, _TIER, app_id=app_id)
    assert await _used(db) == 1.25
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("app_id", ["n400", "techrehearsal"])
async def test_a_self_metering_app_does_not_charge_the_shouldersurf_allowance(app_id):
    """The leak, stated as a test, for both apps that carry their own cap.

    Tech Rehearsal joined this list on 2026-09-02 by Scott's ruling. It had
    been double-metered since its own gate went live on 2026-07-05: once
    against its $5/$25 cap, once against an allowance belonging to an app its
    users may never have opened.
    """
    from app.services.usage_tracker import UsageTracker
    db = await _users_db()
    await UsageTracker().record_cost(db, "u1", 1.25, _TIER, app_id=app_id)
    assert await _used(db) == 0.0
    await db.close()


# --- the gate on the actual route -------------------------------------------
#
# Everything above tests the module. None of it would notice if the gate were
# never wired into the chat handler, which is the state Tech Rehearsal's
# equivalent gate is in today: it has unit tests and no route test, so the
# only thing standing between it and dead code is that someone read the diff.
# These two exercise the wire.

def _seed_spend(db_path: str, user_id: str, app_id: str, amount: float) -> None:
    import sqlite3
    import uuid
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO usage_log (id, user_id, app_id, provider, model, "
        "estimated_cost_usd, request_timestamp) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, app_id, "anthropic", "test-model",
         amount, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def _chat_body():
    from tests.conftest import chat_request
    return chat_request(user_content="When did you become a permanent resident?")


@pytest.mark.asyncio
async def test_an_over_cap_n400_call_is_stopped_on_the_route(
        client, free_user, tmp_db_path):
    """The whole point, end to end: over the cap, N-400 gets the stop
    envelope rather than a model call."""
    _seed_spend(tmp_db_path, free_user["user_id"], "n400", 99.0)
    r = client.post("/v1/chat", json=_chat_body(),
                    headers={**free_user["headers"], "X-App-ID": "n400"})
    assert r.status_code == 200, r.text
    state = r.json()["feature_state"]
    assert state["budget_exhausted"] is True
    assert state["app"] == "n400"
    assert state["resets_at"]
    # Locale map, not a bare string: this is the shape N-400's decode tests
    # pin, and getting it wrong fails on the device rather than here.
    assert isinstance(state["cta"]["text"], dict)
    assert state["cta"]["text"]["en"].strip()


@pytest.mark.asyncio
async def test_the_same_spend_does_not_stop_shouldersurf(
        client, free_user, tmp_db_path):
    """Proves the stop is scoped to the app rather than to the account.

    Without this, a gate that blocked EVERY app once any app went over would
    pass the test above and be a far worse bug than the one being fixed.
    """
    _seed_spend(tmp_db_path, free_user["user_id"], "n400", 99.0)
    r = client.post("/v1/chat", json=_chat_body(),
                    headers={**free_user["headers"], "X-App-ID": "shouldersurf"})
    assert (r.json().get("feature_state") or {}).get("budget_exhausted") is not True
