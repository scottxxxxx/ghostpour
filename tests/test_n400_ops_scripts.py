"""The two prod-config scripts, tested where they decide something.

These do admin writes against production, so the parts worth testing are the
parts that REFUSE: the deploy guard that keeps a sync from pushing stale text,
and the read-back that decides whether a number actually moved. Both were
previously prose in a chained shell command, which is the #948 shape, a check
that exists only in chat and is therefore never run.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from set_n400_cap import moved, next_document  # noqa: E402
from sync_n400_prompt import BLOCK_LINE, MUST_APPEAR_ONCE, deploy_has_landed, verify  # noqa: E402


# --- the deploy guard: the sync must refuse to run before the deploy lands ---

def test_a_matching_sha_is_the_only_thing_that_permits_a_sync():
    ok, why = deploy_has_landed("bc00140e9f", "bc00140e9f")
    assert ok and why == ""


def test_a_short_sha_matches_the_long_one_from_either_side():
    """`/health` returns the full sha and a human pastes the short one."""
    assert deploy_has_landed("bc00140e9fe58cb46983", "bc00140")[0]
    assert deploy_has_landed("bc00140", "bc00140e9fe58cb46983")[0]


def test_the_deploy_that_has_not_landed_is_refused_and_says_why():
    """The whole reason the script exists: sync-from-bundle copies the bundle
    from inside the RUNNING container, so syncing against the old image pushes
    the old text and reports success doing it."""
    ok, why = deploy_has_landed("d99ab05aaa", "bc00140bbb")
    assert not ok
    assert "d99ab05" in why and "bc00140" in why
    assert "OLD bundle" in why


def test_no_expected_sha_refuses_rather_than_defaulting_to_yes():
    """A guard whose default is permissive is not a guard."""
    ok, why = deploy_has_landed("bc00140", "")
    assert not ok and "blind" in why


def test_an_unreachable_health_is_not_treated_as_a_match():
    """#962's distinction: unreachable and unknown are different from equal.
    An empty sha must not read as agreement with whatever was expected."""
    ok, why = deploy_has_landed("", "bc00140")
    assert not ok and "git_sha" in why


# --- the served-prompt read-back -------------------------------------------

def _prompt(rule_line: str, extras=(), lines_before=BLOCK_LINE):
    from sync_n400_prompt import PHRASES
    body = ["filler"] * lines_before + [rule_line]
    body += list(extras) if extras else [" ".join(PHRASES)]
    return "\n".join(body)


def test_a_correct_served_prompt_verifies():
    assert verify(_prompt("... %s ..." % MUST_APPEAR_ONCE)) is True


def test_the_rule_missing_entirely_fails():
    assert verify(_prompt("a deferrals block with no such rule")) is False


def test_the_rule_in_the_wrong_section_fails():
    """Present SOMEWHERE in the document is not the claim. This is the check
    that separates 'the string is in the file' from 'the rule is where the
    lane reads it', and a substring search alone cannot tell them apart."""
    sp = _prompt("an unremarkable deferrals block")
    sp = sp + "\n" + MUST_APPEAR_ONCE  # right string, wrong place
    assert verify(sp) is False


def test_the_rule_twice_fails_rather_than_passing_twice_as_hard():
    """A duplicated rule means a sync appended instead of replacing."""
    sp = _prompt("%s and again %s" % (MUST_APPEAR_ONCE, MUST_APPEAR_ONCE))
    assert verify(sp) is False


def test_a_counterweight_phrase_missing_fails():
    """#966 carries its own counterweight: a condition on HOW she gets the
    answer stays allowed. Losing it scores better on every readability metric
    and makes the product worse, so its absence has to fail."""
    from sync_n400_prompt import PHRASES
    kept = [p for p in PHRASES if p != "if you can find it"]
    assert verify(_prompt("... %s ..." % MUST_APPEAR_ONCE, extras=[" ".join(kept)])) is False


def test_a_prompt_shorter_than_the_block_reports_rather_than_raising():
    """A verification that dies halfway leaves the operator with no verdict,
    which reads like a tooling problem rather than a wrong prompt."""
    assert verify(MUST_APPEAR_ONCE) is False


# --- the cap read-back ------------------------------------------------------

def test_the_number_moving_on_the_server_is_the_only_success():
    assert moved({"version": 2, "monthly_cost_limit_usd": 20.0},
                 {"version": 3, "monthly_cost_limit_usd": 500.0}, 500.0) is True


def test_a_version_bump_with_the_old_number_is_not_success():
    assert moved({"version": 2, "monthly_cost_limit_usd": 20.0},
                 {"version": 3, "monthly_cost_limit_usd": 20.0}, 500.0) is False


def test_the_right_number_without_a_version_bump_is_not_success():
    """The #962 shape: a write that reported success to a server nobody meant
    to talk to. If the document did not advance, nothing was written here."""
    assert moved({"version": 2, "monthly_cost_limit_usd": 500.0},
                 {"version": 2, "monthly_cost_limit_usd": 500.0}, 500.0) is False


def test_the_replacement_document_keeps_every_other_key():
    """PUT is a FULL DOCUMENT REPLACE, so anything dropped here is deleted from
    the served config silently."""
    before = {"version": 2, "monthly_cost_limit_usd": 20.0,
              "per_call_ceiling_usd": 0.5, "enabled": True,
              "_comment": ["an earlier note"]}
    after = next_document(before, 500.0, "2026-09-11: Scott ruled 500")
    assert after["version"] == 3
    assert after["monthly_cost_limit_usd"] == 500.0
    assert after["per_call_ceiling_usd"] == 0.5 and after["enabled"] is True
    assert after["_comment"][0] == "an earlier note"
    assert after["_comment"][-1] == "2026-09-11: Scott ruled 500"


def test_no_note_appends_nothing():
    after = next_document({"version": 1, "_comment": ["only this"]}, 50.0, "")
    assert after["_comment"] == ["only this"]


def test_the_source_document_is_not_mutated():
    """It is read back and compared against the result; sharing a dict would
    make the comparison trivially true."""
    before = {"version": 2, "monthly_cost_limit_usd": 20.0, "_comment": ["x"]}
    snapshot = json.loads(json.dumps(before))
    next_document(before, 500.0, "note")
    assert before == snapshot
