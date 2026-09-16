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
from sync_n400_prompt import BLOCK_LINE, MUST_APPEAR_ONCE, VERSIONS, deploy_has_landed, verify  # noqa: E402


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
    # Both directions are named, because a NEWER running image is also a
    # mismatch and the first wording called it "the OLD bundle" when #977
    # deployed on top of the sha being waited for.
    assert "OLDER" in why and "NEWER" in why
    assert "--expect-sha d99ab05" in why


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
    """A v30-shaped prompt: filler, then the DEFERRALS block carrying the rule
    line, then the counterweight phrases. The block is located by the word
    DEFERRALS, so the rule must come after it to count as inside the block."""
    from sync_n400_prompt import PHRASES
    body = ["filler"] * lines_before + ["DEFERRALS " + rule_line]
    body += list(extras) if extras else [" ".join(PHRASES)]
    return "\n".join(body)


def test_a_correct_served_prompt_verifies():
    assert verify(_prompt("... %s ..." % MUST_APPEAR_ONCE), 30) is True


def test_the_rule_missing_entirely_fails():
    assert verify(_prompt("a deferrals block with no such rule"), 30) is False


def test_the_rule_in_the_wrong_section_fails():
    """Present SOMEWHERE in the document is not the claim. This is the check
    that separates 'the string is in the file' from 'the rule is where the
    lane reads it', and a substring search alone cannot tell them apart."""
    sp = MUST_APPEAR_ONCE + "\n" + _prompt("an unremarkable deferrals block")  # right string, BEFORE the block
    assert verify(sp, 30) is False


def test_the_rule_twice_fails_rather_than_passing_twice_as_hard():
    """A duplicated rule means a sync appended instead of replacing."""
    sp = _prompt("%s and again %s" % (MUST_APPEAR_ONCE, MUST_APPEAR_ONCE))
    assert verify(sp, 30) is False


def test_a_counterweight_phrase_missing_fails():
    """#966 carries its own counterweight: a condition on HOW she gets the
    answer stays allowed. Losing it scores better on every readability metric
    and makes the product worse, so its absence has to fail."""
    from sync_n400_prompt import PHRASES
    kept = [p for p in PHRASES if p != "if you can find it"]
    assert verify(_prompt("... %s ..." % MUST_APPEAR_ONCE, extras=[" ".join(kept)]), 30) is False


def test_a_prompt_with_no_block_reports_rather_than_raising():
    """A verification that dies halfway leaves the operator with no verdict,
    which reads like a tooling problem rather than a wrong prompt. The rule
    alone, with no DEFERRALS block anywhere, is BLOCK NOT FOUND, not a crash."""
    assert verify(MUST_APPEAR_ONCE, 30) is False


# --- per-version phrase lists ------------------------------------------------

def test_an_unlisted_version_refuses_rather_than_passing():
    """Verifying a new prompt against strings nobody changed is a check that
    cannot fail. A version with no phrase list is a refusal."""
    assert verify("anything at all", 99) is False


def _v31_prompt(drop=None):
    spec = VERSIONS[31]
    lines = ["preamble", spec["block_anchor"], "  " + spec["once"] + ","]
    lines += [p for p in spec["phrases"] if p != drop]
    return "\n".join(lines)


def test_the_v31_phrases_verify_and_each_is_load_bearing():
    assert verify(_v31_prompt(), 31) is True
    for phrase in VERSIONS[31]["phrases"]:
        assert verify(_v31_prompt(drop=phrase), 31) is False, phrase


def test_v30s_rule_must_survive_into_v31():
    """#972 edits the schema block; the #966 rule lives elsewhere and must not
    be lost by the edit. Its phrase is in v31's list for exactly that reason."""
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in VERSIONS[31]["phrases"]


def _spec_prompt(version, drop=None, extra=""):
    """A synthetic prompt that satisfies one version's list. Used once the
    bundle has moved past that version, so its list stays tested."""
    spec = VERSIONS[version]
    lines = ["preamble", spec["block_anchor"], "  " + spec["once"] + ","]
    lines += [p for p in spec["phrases"] if p != drop]
    return "\n".join(lines) + extra


def test_the_v32_phrases_verify_and_each_is_load_bearing():
    assert verify(_spec_prompt(32), 32) is True
    for phrase in VERSIONS[32]["phrases"]:
        assert verify(_spec_prompt(32, drop=phrase), 32) is False, phrase


def test_v31s_rule_coming_back_fails_the_v32_read_back():
    """v32's Edit B REPLACED v31's passage. A served copy that still carries
    it has two sentences disagreeing about the same case, and every presence
    phrase would still pass, so the absence is checked explicitly."""
    assert verify(_spec_prompt(32, extra="\n2019 goes NOWHERE, not into `deferred`"), 32) is False


def test_v30_and_v31_rules_must_survive_into_v32():
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in VERSIONS[32]["phrases"]
    assert any('"intent": an OBJECT' in p for p in VERSIONS[32]["phrases"])


def _bundle_prompt(version: int) -> str:
    """The REAL repo bundle, not a synthetic string: the sync verifies the
    served copy against this list, so the list has to pass on the bytes that
    will be synced."""
    root = Path(__file__).resolve().parent.parent
    doc = json.loads((root / "config/remote/n400/interviewer-turn.json").read_text())
    assert doc["version"] == version, "these tests pin v%d's list to v%d's bundle" % (version, version)
    return doc["systemPrompt"]


def test_the_v33_phrases_verify_and_each_is_load_bearing():
    assert verify(_spec_prompt(33), 33) is True
    for phrase in VERSIONS[33]["phrases"]:
        assert verify(_spec_prompt(33, drop=phrase), 33) is False, phrase


def test_each_retired_passage_coming_back_fails_the_v33_read_back():
    """v33's Edit A replaced the green card example and the address line; v32
    had already removed "goes NOWHERE". Each one reappearing must fail even
    though every presence phrase would still pass."""
    for phrase in VERSIONS[33]["absent"]:
        assert verify(_spec_prompt(33, extra="\n" + phrase), 33) is False, phrase


def test_v30_v31_and_v32_rules_must_survive_into_v33():
    phrases = VERSIONS[33]["phrases"]
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in phrases
    assert any('"intent": an OBJECT' in p for p in phrases)
    assert '"origin": "applicant" or "capture_gap"' in phrases


def test_the_v34_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v35, so v34's list is kept tested
    the way v31, v32 and v33's are rather than deleted. A list that stops being
    exercised the moment its version ships is a list nobody would notice
    rotting."""
    assert verify(_spec_prompt(34), 34) is True
    for phrase in VERSIONS[34]["phrases"]:
        assert verify(_spec_prompt(34, drop=phrase), 34) is False, phrase


def test_each_retired_passage_coming_back_fails_the_v34_read_back():
    """v34's Edit A replaced "acknowledge what landed, then ask the next
    thing", the one-line rule the model satisfied with "Got it, <answer>" on
    every turn; v34c then replaced v34b's vary-the-opener sentences. Any of them
    returning must fail even though every presence phrase still passes."""
    for phrase in VERSIONS[34]["absent"]:
        assert verify(_spec_prompt(34, extra="\n" + phrase), 34) is False, phrase


def test_v30_to_v33_rules_must_survive_into_v34():
    phrases = VERSIONS[34]["phrases"]
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in phrases
    assert any('"intent": an OBJECT' in p for p in phrases)
    assert '"origin": "applicant" or "capture_gap"' in phrases
    assert "The entry is the record and the reply is not" in phrases


def test_the_v35_list_verifies_the_real_v35_bundle():
    assert verify(_bundle_prompt(35), 35) is True


def test_each_v35_phrase_is_load_bearing_on_the_real_bundle():
    sp = _bundle_prompt(35)
    for phrase in [VERSIONS[35]["once"]] + VERSIONS[35]["phrases"]:
        assert phrase in sp, phrase
        assert verify(sp.replace(phrase, ""), 35) is False, phrase


def test_each_retired_passage_coming_back_fails_the_v35_read_back():
    """v35b replaced "A partial date is always a deferral", which sat beside the
    ask-once rule and contradicted it; that contradiction is why v35's first cut
    probed 0 of 3. v35e replaced v35d's moved-out-day passage. A served copy
    carrying either is a stale cut, not a new version."""
    sp = _bundle_prompt(35)
    for phrase in VERSIONS[35]["absent"]:
        assert phrase not in sp, phrase
        assert verify(sp + "\n" + phrase, 35) is False, phrase


def test_v30_to_v34_rules_must_survive_into_v35():
    phrases = VERSIONS[35]["phrases"]
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in phrases
    assert any('"intent": an OBJECT' in p for p in phrases)
    assert '"origin": "applicant" or "capture_gap"' in phrases
    assert "The entry is the record and the reply is not" in phrases
    # v35 is cut FROM v34d, so the reply-shape rules ride along and a v35 sync
    # that lost them would be a silent regression of the cut before it.
    assert "A PLAIN ANSWER GETS NO ECHO" in phrases
    assert "it never means no read-back" in phrases


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
