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

from contextlib import contextmanager
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
    """CI has no config overlay, so this is the honest test of the floor.

    ⭐ UNCAPPED since 2026-09-08. Scott: "we don't want to have any budget
    limits on our development teams... There's no chance of a production user
    using the N-400 lane so we need to open it up." Was 5.0, which exhausted
    on ~60 test interviews and stopped the auditor's work entirely.

    `None` is the resolved form of the `-1` sentinel, and it means the gate
    imposes NO CEILING (`would_exceed_flat_budget` returns False immediately
    on a None cap). Pinned as a deliberate value, not left to drift: if this
    ever reads a number again, the dev lane has been silently re-capped.

    ⚠ Safe only while no real user can reach the app, which holds because
    `com.weirtech.n400helper` is absent from `CZ_APPLE_BUNDLE_ID`. If that
    bundle id is ever added, this assertion is the one to revisit FIRST.
    """
    assert app_budget.flat_cap_usd({}, load_apps(), "n400") is None


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
    # -1 = no ceiling (Scott 2026-09-08, dev teams are not budget limited).
    # The COPY is still pinned above and deliberately kept: uncapping is a
    # dial, and the sentence a user would see if it were ever re-capped must
    # not rot in the meantime.
    assert doc["monthly_cost_limit_usd"] == -1


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


@contextmanager
def _served_n400_cap(client, cap):
    """Give the running app a served n400 cap for the duration of a test.

    ⚠ Written 2026-09-08 because this test used to lean on the apps.yml FLOOR
    being a number. When the floor went to -1 (uncapped dev lane) the test
    broke, and it broke ONLY IN CI: a local config overlay supplies a served
    number, so the local suite stayed green while CI, which has no overlay,
    read the floor and failed. See [[reference_local_overlay_shadows_tests]].
    The invariant here is "a cap that EXISTS stops an over-cap call", which
    should not depend on what the shipped default happens to be.
    """
    prev = client.app.state.remote_configs
    client.app.state.remote_configs = {
        **prev,
        "n400/budget": {
            "version": 1,
            "monthly_cost_limit_usd": cap,
            "exhausted": {"kind": "budget_exhausted",
                          "text": {"en": "Allowance used.",
                                   "es": "Asignacion usada.",
                                   "pt": "Cota usada."}},
        },
    }
    try:
        yield
    finally:
        client.app.state.remote_configs = prev


@pytest.mark.asyncio
async def test_an_over_cap_n400_call_is_stopped_on_the_route(
        client, free_user, tmp_db_path):
    """The whole point, end to end: over the cap, N-400 gets the stop
    envelope rather than a model call."""
    _seed_spend(tmp_db_path, free_user["user_id"], "n400", 99.0)
    with _served_n400_cap(client, 50.0):
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


# --- uncapped AND reachable: the combination nobody should ship -------------
# 2026-09-08. Uncapping N-400 is safe only while no real user can authenticate
# to it, which is true only because com.weirtech.n400helper is absent from
# CZ_APPLE_BUNDLE_ID. The auditor pointed out that "move both together" is the
# WRONG instruction: they must move in ORDER, because -1 must never be live at
# the same moment the audience check starts passing.

_REACH = "com.shouldersurf.ShoulderSurf,com.weirtech.techrehearsal"
_REACH_N400 = _REACH + ",com.weirtech.n400helper"


def test_uncapped_but_unreachable_is_the_healthy_state():
    """Today: N-400 has no ceiling and no real user can sign in. Fine."""
    assert app_budget.audit_uncapped_reachable_apps(
        {}, load_apps(), _REACH) == []


def test_uncapped_and_reachable_is_reported():
    """The moment the bundle id lands while the cap is -1, every SIGNUP has an
    unlimited allowance, because the cap is per-user and this app is off the
    shared account meter."""
    found = app_budget.audit_uncapped_reachable_apps(
        {}, load_apps(), _REACH_N400)
    assert [v["app_id"] for v in found] == ["n400"]
    assert found[0]["bundle_id"] == "com.weirtech.n400helper"
    assert found[0]["own_account_meter"] is True


def test_a_capped_app_is_not_reported_even_when_reachable():
    """This is the SAFE ordering: cap first, then add the bundle id. If this
    ever fails, the audit is crying wolf and will be ignored when it matters."""
    served = {"n400/budget": {"version": 1, "monthly_cost_limit_usd": 50.0}}
    assert app_budget.audit_uncapped_reachable_apps(
        served, load_apps(), _REACH_N400) == []


def test_apps_without_a_flat_budget_are_not_reported():
    """ShoulderSurf is reachable and has no flat budget block. It is on the
    shared account meter, which is a different mechanism, and must not be
    swept up here."""
    found = app_budget.audit_uncapped_reachable_apps({}, load_apps(), _REACH)
    assert "shouldersurf" not in [v["app_id"] for v in found]


def test_no_allowlist_reports_nothing():
    """An empty CZ_APPLE_BUNDLE_ID means nothing can authenticate at all."""
    assert app_budget.audit_uncapped_reachable_apps({}, load_apps(), "") == []
    assert app_budget.audit_uncapped_reachable_apps({}, load_apps(), None) == []


@pytest.mark.asyncio
async def test_the_shipped_uncapped_lane_does_not_stop_anything(
        client, free_user, tmp_db_path):
    """The other half, and the one that pins Scott's 2026-09-08 ruling end to
    end: with the SHIPPED config (no served doc, floor -1) a spend that would
    blow past any sane cap sails through, because the dev lane has no ceiling.

    Paired with the test above deliberately. That one proves a cap works; this
    one proves the shipped default is not one. Either alone would let the lane
    be silently re-capped or silently ungateable without a test noticing.
    """
    _seed_spend(tmp_db_path, free_user["user_id"], "n400", 9999.0)
    rc = dict(client.app.state.remote_configs)
    rc.pop("n400/budget", None)
    prev = client.app.state.remote_configs
    client.app.state.remote_configs = rc
    try:
        r = client.post("/v1/chat", json=_chat_body(),
                        headers={**free_user["headers"], "X-App-ID": "n400"})
    finally:
        client.app.state.remote_configs = prev
    assert r.status_code == 200, r.text
    assert (r.json().get("feature_state") or {}).get("budget_exhausted") is not True


# --- the guard must fire on a CONFIG SAVE, not only at startup --------------

@pytest.mark.asyncio
async def test_the_audit_raises_an_incident_not_just_a_log_line(tmp_db_path):
    """A log line is not an alert. The first version of this only logged, and
    the auditor was right that nobody tails a log."""
    import aiosqlite
    from app.database import init_db
    await init_db(f"sqlite+aiosqlite:///{tmp_db_path}")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        found = await app_budget.report_uncapped_reachable(
            db, {}, load_apps(), _REACH_N400)
        assert [v["app_id"] for v in found] == ["n400"]
        rows = await (await db.execute(
            "SELECT category, subject FROM alert_incidents")).fetchall()
    assert [(r["category"], r["subject"]) for r in rows] == [
        ("uncapped_and_reachable", "n400")]


@pytest.mark.asyncio
async def test_the_safe_state_raises_nothing(tmp_db_path):
    """Today's state: uncapped but unreachable. An alert here would train
    everyone to ignore the one that matters."""
    import aiosqlite
    from app.database import init_db
    await init_db(f"sqlite+aiosqlite:///{tmp_db_path}")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        assert await app_budget.report_uncapped_reachable(
            db, {}, load_apps(), _REACH) == []
        rows = await (await db.execute(
            "SELECT * FROM alert_incidents")).fetchall()
    assert rows == []


# --- refusing the forbidden pair, rather than only alerting on it -----------
#
# 2026-09-10. `report_uncapped_reachable` already raises an incident from boot
# and from the admin config write, so the pair is not silent. But an alert
# tells an operator to go and fix something; it does nothing about the calls
# served in the minutes before anyone reads it, and the cap is PER SIGNUP.
# These pin the refusal, and just as importantly they pin that it stays OFF in
# the state we are actually shipping tonight (uncapped and unreachable).

def test_refusal_fires_on_the_forbidden_pair():
    assert app_budget.refuse_uncapped_reachable(
        {}, load_apps(), "n400", _REACH_N400)["app_id"] == "n400"


def test_refusal_stays_off_while_the_app_is_unreachable():
    """TONIGHT'S SHIPPING STATE. The dev lane is uncapped on purpose and the
    bundle id is absent, so nothing may be refused. If this ever fails, the
    guard has taken the dev lane down for the reason it was built to avoid."""
    assert app_budget.refuse_uncapped_reachable(
        {}, load_apps(), "n400", _REACH) is None


def test_refusal_stays_off_when_a_cap_exists():
    """The SAFE ordering, cap first then bundle id, must not be refused."""
    served = {"n400/budget": {"version": 1, "monthly_cost_limit_usd": 50.0}}
    assert app_budget.refuse_uncapped_reachable(
        served, load_apps(), "n400", _REACH_N400) is None


def test_refusal_is_scoped_to_the_offending_app():
    """ShoulderSurf is reachable and must keep serving while N-400 is refused.
    A guard that took down every app would pass the first test here and be far
    worse than the state it protects against."""
    assert app_budget.refuse_uncapped_reachable(
        {}, load_apps(), "shouldersurf", _REACH_N400) is None


@pytest.mark.parametrize("app_id", ["N400", " n400 ", "N400 "])
def test_refusal_normalises_the_app_id_the_way_the_registry_does(app_id):
    """The header is client-supplied. A guard that missed "N400" would be a
    guard anyone could walk past by changing one character."""
    assert app_budget.refuse_uncapped_reachable(
        {}, load_apps(), app_id, _REACH_N400) is not None


@pytest.mark.parametrize("app_id", [None, ""])
def test_no_app_id_is_not_refused(app_id):
    assert app_budget.refuse_uncapped_reachable(
        {}, load_apps(), app_id, _REACH_N400) is None


@pytest.mark.asyncio
async def test_the_route_refuses_the_forbidden_pair(client, free_user, monkeypatch):
    """On the wire, because the module test above would not notice if the
    guard were never wired into the handler."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "apple_bundle_id", _REACH_N400, raising=False)
    prev = client.app.state.remote_configs
    client.app.state.remote_configs = {**prev, "n400/budget":
                                       {"version": 1, "monthly_cost_limit_usd": -1}}
    try:
        r = client.post("/v1/chat", json=_chat_body(),
                        headers={**free_user["headers"], "X-App-ID": "n400"})
    finally:
        client.app.state.remote_configs = prev
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "app_spend_gate_misconfigured"


@pytest.mark.asyncio
async def test_the_route_still_serves_the_uncapped_unreachable_lane(
        client, free_user, monkeypatch):
    """The pair with the test above, and the one that protects the dev lane:
    uncapped is fine while unreachable, so this must NOT be a 503."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "apple_bundle_id", _REACH, raising=False)
    prev = client.app.state.remote_configs
    client.app.state.remote_configs = {**prev, "n400/budget":
                                       {"version": 1, "monthly_cost_limit_usd": -1}}
    try:
        r = client.post("/v1/chat", json=_chat_body(),
                        headers={**free_user["headers"], "X-App-ID": "n400"})
    finally:
        client.app.state.remote_configs = prev
    assert r.status_code != 503, r.text
