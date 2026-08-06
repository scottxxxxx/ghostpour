"""The sentence a Tech Rehearsal user reads when they are out of allowance.

The gate itself has been live since 2026-07-05 (free $5, paid $25 per UTC
month, enforced on /v1/chat and the report route, the only two paths that
spend). What was missing is the reason: the block carried a flag and an
empty string, and TR wrote its own limit-reached copy.

That is backwards. A hard stop is one of the few moments the product tells
a user no, so the wording is ours, and it is served rather than compiled in
so it can be fixed without a client build.

Two rules the copy has to keep:

- Factual. What happened, and when it renews. No apology, no hedging, and
  no comparison to what a paid plan would have allowed. A user who has hit
  a wall is not the audience for a pitch about the wall.
- Free and paid are different sentences. They hit the same gate for
  different reasons (an allowance spent versus a usage limit reached), and
  only one of them has an upgrade to offer.
"""

import json
import pathlib
from datetime import datetime, timezone

import pytest

from app.services import tr_budget

CONFIG = pathlib.Path("config/remote/techrehearsal/budget.json")


@pytest.fixture(scope="module")
def served():
    return {"techrehearsal/budget": json.loads(CONFIG.read_text())}


# --- the copy ---------------------------------------------------------


@pytest.mark.parametrize("entitlement", ["free", "paid"])
def test_both_entitlements_get_a_sentence(served, entitlement):
    cta = tr_budget.exhausted_copy(served, entitlement)
    assert cta["text"].strip()
    assert cta["kind"] == "budget_exhausted"


def test_free_and_paid_do_not_read_the_same(served):
    """Same gate, different reasons. Collapsing them would tell a paying
    user they have used their 'free allowance', which is wrong and reads as
    the app not knowing what they bought."""
    free = tr_budget.exhausted_copy(served, "free")
    paid = tr_budget.exhausted_copy(served, "paid")
    assert free["text"] != paid["text"]
    assert "free" in free["text"].lower()
    assert "free" not in paid["text"].lower()


def test_only_the_free_sentence_offers_an_upgrade(served):
    """There is nothing to sell someone who already paid, and an upgrade
    button that leads nowhere is worse than no button."""
    assert tr_budget.exhausted_copy(served, "free")["action"] == "open_paywall"
    assert tr_budget.exhausted_copy(served, "paid")["action"] is None


@pytest.mark.parametrize("entitlement", ["free", "paid"])
def test_the_copy_carries_no_dashes(served, entitlement):
    text = tr_budget.exhausted_copy(served, entitlement)["text"]
    for dash in ("—", "–"):
        assert dash not in text


# --- degrading ---------------------------------------------------------


@pytest.mark.parametrize("configs", [None, {}, {"techrehearsal/budget": {}}])
def test_an_unreachable_config_still_says_something(configs):
    """A rejection with no reason reads as the app being broken, which is
    worse than the rejection. So the fallback is a sentence, never silence."""
    cta = tr_budget.exhausted_copy(configs, "free")
    assert cta["text"].strip()


def test_an_unknown_entitlement_falls_back_rather_than_crashing(served):
    """X-TR-Entitlement is a client header. A typo or a future value must
    not turn a budget block into a 500."""
    cta = tr_budget.exhausted_copy(served, "enterprise")
    assert cta["text"].strip()
    assert tr_budget.exhausted_copy(served, None)["text"].strip()


def test_the_caller_cannot_mutate_the_served_config(served):
    """exhausted_copy hands back a copy. Returning the live dict would let
    one request edit the string every later request reads."""
    first = tr_budget.exhausted_copy(served, "free")
    first["text"] = "mutated"
    assert tr_budget.exhausted_copy(served, "free")["text"] != "mutated"


# --- when it comes back ------------------------------------------------


def test_the_reset_is_the_first_instant_of_next_month():
    """The gate sums spend from the start of the UTC calendar month, so the
    allowance returns at the start of the next one. If these two ever
    disagree, a user is told a date on which nothing happens."""
    reset = datetime.fromisoformat(tr_budget.month_reset_iso())
    now = datetime.now(timezone.utc)
    assert reset > now
    assert (reset.day, reset.hour, reset.minute, reset.second) == (1, 0, 0, 0)
    assert reset.tzinfo is not None
    if now.month == 12:
        assert (reset.year, reset.month) == (now.year + 1, 1)
    else:
        assert (reset.year, reset.month) == (now.year, now.month + 1)


def test_the_reset_agrees_with_the_window_the_gate_measures():
    """Same boundary, opposite ends: spend counts from month_start, the
    allowance returns at month_reset."""
    start = datetime.fromisoformat(tr_budget._month_start_iso())
    reset = datetime.fromisoformat(tr_budget.month_reset_iso())
    assert start < reset
    assert start.day == reset.day == 1


def test_the_date_is_machine_readable_not_prose():
    """We send ISO and let the client format it. We do not know their locale
    or calendar, and a date we format is a date we have to localise."""
    iso = tr_budget.month_reset_iso()
    assert datetime.fromisoformat(iso)
    assert "T" in iso
