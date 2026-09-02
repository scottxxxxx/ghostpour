"""Two counters live on the ACCOUNT row and every app shares it.

`users.memory_used_this_period` and `users.generations_used` are per-user,
and SIWA issues subject ids per developer TEAM, so one row serves all three
apps. Under Scott's multitenancy ruling (2026-09-02) that is exactly the
shape the apps must not have: a user does not share amongst apps because
their identity is shared.

MEASURED THE SAME DAY, BEFORE WRITING THIS: the leak is LATENT, not live.
`artifact_generation` is ShoulderSurf's alone (6 calls ever) and no other
app produces capture traffic. Unlike the spend meter, where Tech Rehearsal
really was being double-metered since July, nothing is currently sharing
these.

That is the argument FOR the guard rather than against it. A latent leak
produces no signal on the day it stops being latent: the second app's first
file generation would simply spend the first app's allowance, with a 200 and
a rendered result and nothing anywhere to notice. The guard turns that
silent moment into a named error.

The permanent fix is a per-app counter table. Deliberately not built here:
the two counters use DIFFERENT period models (memory carries its own
`memory_period`; generations rides the allocation cycle), so reconciling
them is a migration with a backfill, not a guard.
"""

from __future__ import annotations

import aiosqlite
import pytest

from app.services import memory_capture_quota as mq


async def _db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE users (id TEXT, memory_used_this_period "
                     "INTEGER DEFAULT 0, memory_period TEXT)")
    await db.execute("INSERT INTO users VALUES ('u1', 0, NULL)")
    await db.commit()
    return db


async def _used(db) -> int:
    cur = await db.execute("SELECT memory_used_this_period FROM users WHERE id='u1'")
    return int((await cur.fetchone())["memory_used_this_period"] or 0)


@pytest.mark.asyncio
async def test_the_owning_app_still_charges_the_counter():
    db = await _db()
    await mq.decrement_memory_quota(db, "u1", app_id=mq.OWNING_APP)
    assert await _used(db) == 1
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("app_id", ["techrehearsal", "n400"])
async def test_another_registered_app_is_refused_rather_than_charged(app_id):
    """Declining to charge is the safe direction: sharing is the bug, so the
    failure mode should be 'this app got no quota decrement' rather than
    'this app spent ShoulderSurf's captures'."""
    db = await _db()
    await mq.decrement_memory_quota(db, "u1", app_id=app_id)
    assert await _used(db) == 0, (
        f"{app_id} charged a counter belonging to {mq.OWNING_APP}")
    await db.close()


@pytest.mark.asyncio
async def test_an_absent_app_id_still_charges():
    """Fails open to today's behaviour on purpose, matching resolve_app_dir:
    an older build that sends no header must not lose its quota accounting
    over app identity."""
    db = await _db()
    await mq.decrement_memory_quota(db, "u1", app_id=None)
    assert await _used(db) == 1
    await db.close()


def test_the_owner_is_named_in_exactly_one_place():
    """A second copy of "who owns this counter" is how the two halves of a
    rule drift apart."""
    from app.services.app_budget import SHARED_ACCOUNT_COUNTERS
    assert mq.OWNING_APP == SHARED_ACCOUNT_COUNTERS["memory_used_this_period"]
    body = open("app/services/memory_capture_quota.py").read()
    body = body[body.index("async def decrement_memory_quota"):]
    assert '"shouldersurf"' not in body


# --- the predicate itself, tested BEHAVIOURALLY -----------------------------
#
# The first version of the generation test grepped chat.py for `_gen_app` and
# `generation_quota_refused` near the write. Under sabotage, replacing the
# condition with `if False:` left both strings exactly where they were and
# THE TEST STAYED GREEN. It asserted that the guard had been TYPED, not that
# it RAN. Extracting the predicate is what made it testable at all.

from app.services.app_budget import (  # noqa: E402
    SHARED_ACCOUNT_COUNTERS,
    may_charge_shared_counter,
)


@pytest.mark.parametrize("counter", sorted(SHARED_ACCOUNT_COUNTERS))
def test_the_owning_app_may_charge(counter):
    assert may_charge_shared_counter(counter, SHARED_ACCOUNT_COUNTERS[counter])


@pytest.mark.parametrize("counter", sorted(SHARED_ACCOUNT_COUNTERS))
@pytest.mark.parametrize("app_id", ["techrehearsal", "n400"])
def test_no_other_registered_app_may_charge(counter, app_id):
    assert not may_charge_shared_counter(counter, app_id)


@pytest.mark.parametrize("counter", sorted(SHARED_ACCOUNT_COUNTERS))
def test_an_unregistered_app_charges_and_that_is_deliberate(counter):
    """Worth stating rather than leaving as a surprise: an id that is not in
    apps.yml resolves to the DEFAULT app and therefore charges.

    That is resolve_app_dir's fail-open, and agreeing with it is the point
    of resolving rather than string-comparing. It does mean the guard only
    protects against REGISTERED tenants, which is the real case: an app
    reaches this lane by being registered and shipping a build, not by
    inventing a header. If that ever stops being true, the fix belongs in
    resolve_app_dir so every gate moves together, not here alone.
    """
    assert may_charge_shared_counter(counter, "some-app-nobody-registered")


@pytest.mark.parametrize("counter", sorted(SHARED_ACCOUNT_COUNTERS))
@pytest.mark.parametrize("app_id", [None, "", "   ", "unknown", "UNKNOWN",
                                    "not-an-app", "shouldersurf"])
def test_anything_resolving_to_the_default_app_charges(counter, app_id):
    """Fails open to today's behaviour, matching resolve_app_dir.

    THE LIST IS THE POINT. The first version compared raw strings and only
    special-cased None, so the LITERAL "unknown" was refused. That string is
    what several call sites use for a missing header
    (`getattr(request.state, "app_id", "unknown")`), and resolve_app_dir maps
    it to the default app. Two real tests went red for users whose requests
    carry no app id at all, which is most older field builds. Comparing
    strings was a SECOND IMPLEMENTATION of a rule that already existed, and
    it drifted immediately.
    """
    assert may_charge_shared_counter(counter, app_id)


@pytest.mark.parametrize("counter", sorted(SHARED_ACCOUNT_COUNTERS))
def test_the_predicate_agrees_with_config_resolution(counter):
    """Re-derived a second way: whatever resolve_app_dir calls the default
    app is what may charge, so the two cannot drift apart if the default
    changes."""
    from app.routers.config import resolve_app_dir
    default = resolve_app_dir(None)
    assert may_charge_shared_counter(counter, default)
    assert SHARED_ACCOUNT_COUNTERS[counter] == default


def test_an_unregistered_counter_is_not_silently_gated():
    """Otherwise adding a guard call for a counter nobody registered here
    would quietly stop charging it for every app."""
    assert may_charge_shared_counter("some_counter_nobody_registered", "n400")


def test_both_known_counters_are_registered():
    """Names the two, so removing one from the map is a decision rather than
    an omission that silently ungates it."""
    assert set(SHARED_ACCOUNT_COUNTERS) == {
        "memory_used_this_period", "generations_used"}


def test_the_generation_counter_has_exactly_one_write():
    """Structural, and honest about what it can prove: it pins that there is
    ONE place to guard, not that the guard runs. The predicate tests above
    cover the behaviour."""
    import re
    src = open("app/routers/chat.py").read()
    assert len(re.findall(r"UPDATE users SET generations_used", src)) == 1
    assert "may_charge_shared_counter(\"generations_used\"" in src
