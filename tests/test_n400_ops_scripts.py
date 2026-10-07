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


def test_the_v35_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v36, so v35's list keeps being
    exercised the way v31 to v34's are rather than going quiet the moment its
    version shipped."""
    assert verify(_spec_prompt(35), 35) is True
    for phrase in VERSIONS[35]["phrases"]:
        assert verify(_spec_prompt(35, drop=phrase), 35) is False, phrase


def test_each_retired_passage_coming_back_fails_the_v35_read_back():
    """v35b replaced "A partial date is always a deferral", which sat beside the
    ask-once rule and contradicted it; that contradiction is why v35's first cut
    probed 0 of 3. v35e replaced v35d's moved-out-day passage. A served copy
    carrying either is a stale cut, not a new version."""
    for phrase in VERSIONS[35]["absent"]:
        assert verify(_spec_prompt(35, extra="\n" + phrase), 35) is False, phrase


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


def test_the_v36_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v38, so v36's list keeps being
    exercised the way v31 to v35's are rather than going quiet."""
    assert verify(_spec_prompt(36), 36) is True
    for phrase in VERSIONS[36]["phrases"]:
        assert verify(_spec_prompt(36, drop=phrase), 36) is False, phrase


def test_the_verdict_coming_back_fails_the_v36_read_back():
    """v36 exists to remove one phrase, so its absence IS the version. "that
    fits" and its Spanish twin "encaja" were taught by our own worked example
    in both languages; Edit A rewrote the example and Edit B the defect
    sentence so no older line keeps teaching it. Either returning must fail."""
    for phrase in VERSIONS[36]["absent"]:
        assert verify(_spec_prompt(36, extra="\n" + phrase), 36) is False, phrase


def test_the_mint_rule_survives_the_edit_that_targets_the_verdict():
    """The one that would be easy to lose. v36 rewrites the sentence that
    carries MINT THAT BASIS IN THIS SAME RESPONSE, and that rule exists because
    the eligibility box came back BLANK (conf-v20): v4 made the model treat the
    basis as pending the applicant's yes, so it minted nothing on the turn she
    said it. Removing a verdict word must not reopen that. Checked on v38's
    real bundle now, since v36 is cut into it."""
    assert "MINT THAT BASIS IN THIS SAME RESPONSE" in VERSIONS[36]["phrases"]
    assert "MINT THAT BASIS IN THIS SAME RESPONSE" in _bundle_prompt(51)


def test_the_v38_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v39, so v38's list keeps being
    exercised the way v36's is rather than going quiet."""
    assert verify(_spec_prompt(38), 38) is True
    for phrase in VERSIONS[38]["phrases"]:
        assert verify(_spec_prompt(38, drop=phrase), 38) is False, phrase


def test_the_v39_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v40, so v39's list keeps being
    exercised the way v38's is rather than going quiet."""
    assert verify(_spec_prompt(39), 39) is True
    for phrase in VERSIONS[39]["phrases"]:
        assert verify(_spec_prompt(39, drop=phrase), 39) is False, phrase


def test_the_retired_per_part_confirmation_stays_gone_in_v40():
    """Scott retired the confirmation after every part (2026-09-26). A served
    copy carrying any of its lines is a stale cut, and v40 inherits the list."""
    sp = _bundle_prompt(51)
    for phrase in VERSIONS[39]["absent"]:
        assert phrase in VERSIONS[40]["absent"], phrase
        assert phrase not in sp, phrase
        assert verify(sp + "\n" + phrase, 51) is False, phrase


def test_every_v38_phrase_rides_into_v39():
    for phrase in VERSIONS[38]["phrases"]:
        assert phrase in VERSIONS[39]["phrases"], phrase


def test_the_original_reason_for_the_order_survives_v38():
    """v38 EXTENDS the sentence that argues the order from field dependencies;
    it must not replace that argument with the streaming one. Both reasons,
    word for word, on the real bundle."""
    sp = _bundle_prompt(51)
    assert sp.count("the reply LAST, because each later field must follow the earlier ones") == 1
    assert sp.count("`asking` written after `facts` can never name a node you just minted") == 1
    assert sp.count("THE ORDER IS A REQUIREMENT, NOT A PREFERENCE") == 1


def test_v30_to_v36_rules_must_survive_into_v38():
    phrases = VERSIONS[38]["phrases"]
    for must in ("AND THE CLAUSE MUST NOT BE CONDITIONAL",
                 '"origin": "applicant" or "capture_gap"',
                 "The entry is the record and the reply is not",
                 "A PLAIN ANSWER GETS NO ECHO", "it never means no read-back",
                 "A MONTH AND A YEAR IS ASKED, NOT DEFERRED", "ONE DAY PER QUESTION",
                 "AND THE DAY SHE GAVE IS SAID BACK FIRST",
                 "NAME THE BASIS, NEVER JUDGE IT", "MINT THAT BASIS IN THIS SAME RESPONSE"):
        assert must in phrases, must
    assert any('"intent": an OBJECT' in p for p in phrases)


def test_v30_to_v35_rules_must_survive_into_v36():
    phrases = VERSIONS[36]["phrases"]
    assert "AND THE CLAUSE MUST NOT BE CONDITIONAL" in phrases
    assert any('"intent": an OBJECT' in p for p in phrases)
    assert '"origin": "applicant" or "capture_gap"' in phrases
    assert "The entry is the record and the reply is not" in phrases
    assert "A PLAIN ANSWER GETS NO ECHO" in phrases
    assert "it never means no read-back" in phrases
    # v35's three, since v36 is cut from it.
    assert "A MONTH AND A YEAR IS ASKED, NOT DEFERRED" in phrases
    assert "ONE DAY PER QUESTION" in phrases
    assert "AND THE DAY SHE GAVE IS SAID BACK FIRST" in phrases


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


# --- a value sync (2026-09-27, maxTokens 2048 -> 6144) ------------------------

def test_the_max_tokens_read_back_passes_only_on_the_exact_number():
    from sync_n400_prompt import max_tokens_ok
    assert max_tokens_ok({"maxTokens": 6144}, 6144)
    assert not max_tokens_ok({"maxTokens": 2048}, 6144), "the old value still serving"
    assert not max_tokens_ok({}, 6144), "absent is not a pass"
    assert max_tokens_ok({"maxTokens": 2048}, None), "a prompt-only sync checks nothing here"


def test_the_keys_sent_are_the_ones_the_operator_asked_for():
    from sync_n400_prompt import sync_keys
    assert sync_keys(None) == ["/systemPrompt", "/version"]
    assert sync_keys(6144) == ["/systemPrompt", "/version", "/maxTokens"]


# --- v40: spectrum round 1 (the auditor, 2026-09-27) ---------------------------

def test_the_v40_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v41."""
    assert verify(_spec_prompt(40), 40) is True
    for phrase in VERSIONS[40]["phrases"]:
        assert verify(_spec_prompt(40, drop=phrase), 40) is False, phrase


def test_v39s_name_rule_and_offer_trigger_coming_back_fail_v40():
    """v39 split "Ernesto Delgado Ruiz" into a middle and a last name
    (spectrum-rosa-r1 t30) and made the offer the moment the agenda emptied,
    so eight deferrals were never asked again (rosa t71). A served copy with
    either sentence is a stale cut."""
    sp = _bundle_prompt(51)
    for phrase in ("If the answer has three words, that is first, middle and last.",
                   "the reply that finds the agenda empty MAKES the offer"):
        assert phrase not in sp, phrase
        assert verify(sp + "\n" + phrase, 51) is False, phrase


def test_every_v39_phrase_rides_into_v40():
    for phrase in VERSIONS[39]["phrases"]:
        assert phrase in VERSIONS[40]["phrases"], phrase


# --- v41: spectrum round 3 (the auditor, 2026-09-28) ---------------------------

def test_the_v41_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v42."""
    assert verify(_spec_prompt(41), 41) is True
    for phrase in VERSIONS[41]["phrases"]:
        assert verify(_spec_prompt(41, drop=phrase), 41) is False, phrase


def test_the_idle_heading_scott_reworded_stays_gone_in_v41():
    """Scott, 2026-09-28: an idle stretch's place is filed when she volunteers
    it, so "NO WORKPLACE" is no longer the rule. A served copy carrying the old
    heading is a stale cut."""
    old = "A RETIRED OR UNEMPLOYED STRETCH HAS NO EMPLOYER AND NO WORKPLACE"
    sp = _bundle_prompt(51)
    assert old not in sp
    assert verify(sp + "\n" + old, 45) is False


def test_every_v40_phrase_but_the_reworded_one_rides_into_v41():
    for phrase in VERSIONS[40]["phrases"]:
        if phrase == "A RETIRED OR UNEMPLOYED STRETCH HAS NO EMPLOYER AND NO WORKPLACE":
            continue
        assert phrase in VERSIONS[41]["phrases"], phrase


# --- v42: round 4 (the auditor, 2026-09-28) ------------------------------------

def test_the_v42_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v43."""
    assert verify(_spec_prompt(42), 42) is True
    for phrase in VERSIONS[42]["phrases"]:
        assert verify(_spec_prompt(42, drop=phrase), 42) is False, phrase


def test_every_v41_phrase_rides_into_v42():
    for phrase in VERSIONS[41]["phrases"]:
        assert phrase in VERSIONS[42]["phrases"], phrase


# --- v43: round 5 (the auditor, 2026-09-28) ------------------------------------

def test_the_v43_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v44."""
    assert verify(_spec_prompt(43), 43) is True
    for phrase in VERSIONS[43]["phrases"]:
        assert verify(_spec_prompt(43, drop=phrase), 43) is False, phrase


def test_every_v42_phrase_rides_into_v43():
    for phrase in VERSIONS[42]["phrases"]:
        assert phrase in VERSIONS[43]["phrases"], phrase


# --- v44: round 6 (the auditor, 2026-09-28) ------------------------------------

def test_the_v44_phrases_verify_and_each_is_load_bearing():
    """SYNTHETIC now: the bundle has moved to v45."""
    assert verify(_spec_prompt(44), 44) is True
    for phrase in VERSIONS[44]["phrases"]:
        assert verify(_spec_prompt(44, drop=phrase), 44) is False, phrase


def test_every_v43_phrase_rides_into_v44():
    for phrase in VERSIONS[43]["phrases"]:
        assert phrase in VERSIONS[44]["phrases"], phrase


# --- v45: round 7 (the auditor, 2026-09-28) ------------------------------------

def test_the_v51_list_verifies_the_real_v51_bundle():
    assert verify(_bundle_prompt(51), 51) is True


def test_each_v51_phrase_is_load_bearing_on_the_real_bundle():
    sp = _bundle_prompt(51)
    for phrase in [VERSIONS[51]["once"]] + VERSIONS[51]["phrases"]:
        assert phrase in sp, phrase
        assert verify(sp.replace(phrase, ""), 51) is False, phrase


def test_the_v50_list_still_verifies_the_v51_bundle():
    """v51 only adds, so v50's own read-back must pass on v51's bytes too."""
    assert verify(_bundle_prompt(51), 50) is True


def test_every_v50_phrase_and_its_once_line_ride_into_v51():
    for phrase in VERSIONS[50]["phrases"] + [VERSIONS[50]["once"]]:
        assert phrase in VERSIONS[51]["phrases"], phrase


def test_the_follow_up_rule_is_in_v51_exactly_once():
    """Build 128, case 88761ff1, 2026-10-07 ~15:32Z: "What month and day in
    2019 did you get your green card, if you know it offhand? Have you used any
    other names since birth?" The card showed the second, the voice asked both."""
    sp = _bundle_prompt(51)
    assert sp.count("A FOLLOW-UP KEEPS THE PART OPEN") == 1
    for phrase in ("that follow-up is the ONLY question in this reply, with `asking` naming the node it belongs to",
                   "no part summary and no next part's question, even when SECTION BOUNDARY is present",
                   "(a pending follow-up and the next part's first question are two)"):
        assert sp.count(phrase) == 1, phrase
        assert verify(sp.replace(phrase, ""), 51) is False, phrase


def test_the_on_file_address_rule_is_served_in_v51():
    """Same run, ~15:38Z: "what is your current street address and city?" with
    Austin, Texas on file from onboarding."""
    sp = _bundle_prompt(51)
    for phrase in ("A PIECE ALREADY ON FILE IS NEVER ASKED",
                   "the address question asks only for the pieces still missing and may read back what is on file"):
        assert sp.count(phrase) == 1, phrase
        assert verify(sp.replace(phrase, ""), 51) is False, phrase


def test_every_v49_phrase_and_its_once_line_ride_into_v50():
    """v50 checks one new `once`, so v49's own once line must ride in as a
    phrase or nothing would read it back any more."""
    for phrase in VERSIONS[49]["phrases"] + [VERSIONS[49]["once"]]:
        assert phrase in VERSIONS[50]["phrases"], phrase


def test_the_row_order_rule_is_in_v50_exactly_once():
    """Run 3, 2026-10-07: a prior address filed from 2019-03-03 to
    2019-03-02. The rule tells the lane to ask instead of filing."""
    sp = _bundle_prompt(51)
    assert sp.count("A ROW NEVER ENDS BEFORE IT STARTS") == 1
    assert verify(sp.replace("A ROW NEVER ENDS BEFORE IT STARTS", ""), 51) is False


def test_the_spelling_rule_is_served_in_v50():
    """Run 3, 2026-10-07 02:59:38Z: "I'm married to Wenceslao YARBOROUGH
    D-A-V-I-D" filed spouse_middle_name = David. Spelled letters are the
    spelling of the name just said, and a mismatch is asked, never filed."""
    sp = _bundle_prompt(51)
    for phrase in ("LETTERS SPELLED AFTER A NAME ARE ITS SPELLING",
                   "those letters spell the name she just said, never another name and never a middle name",
                   "When they spell something else, file neither; say both and ask which one is right."):
        assert sp.count(phrase) == 1, phrase
        assert verify(sp.replace(phrase, ""), 51) is False, phrase


def test_every_v48_phrase_rides_into_v49():
    for phrase in VERSIONS[48]["phrases"]:
        assert phrase in VERSIONS[49]["phrases"], phrase


def test_the_old_fee_wording_stays_gone_in_v49():
    """The fee question changed MEANING (2026-09-30): a wish became a fact
    about her income. A served copy still asking the wish is a stale cut.
    The interpreter ruling ("an app is not an interpreter") was pulled
    before shipping: whether the app counts is Scott's question for an
    attorney, and the prompt must not decide it."""
    sp = _bundle_prompt(51)
    for retired in ("USCIS lowers the filing fee for lower household incomes",
                    "never with the income threshold recited at them",
                    "An app speaking her language is not an interpreter",
                    "and were never asked aloud, so the review is the only place she hears them"):
        assert retired not in sp, retired
        assert verify(sp + "\n" + retired, 51) is False, retired


def test_every_v47_phrase_rides_into_v48():
    for phrase in VERSIONS[47]["phrases"]:
        assert phrase in VERSIONS[48]["phrases"], phrase


def test_every_v46_phrase_rides_into_v47_but_the_group_line():
    for phrase in VERSIONS[46]["phrases"]:
        if phrase == "while Part 9 stays one group per turn":
            assert phrase in VERSIONS[47]["absent"] or "Part 9 stays one group per turn" in VERSIONS[47]["absent"]
            continue
        assert phrase in VERSIONS[47]["phrases"], phrase


def test_the_part_9_group_rules_stay_gone_in_v47():
    """Scott, 2026-09-29: Part 9 one question at a time. A served copy that
    still reads Part 9 as groups is a stale cut."""
    sp = _bundle_prompt(51)
    for retired in VERSIONS[47]["absent"][-5:]:
        assert retired not in sp, retired
        assert verify(sp + "\n" + retired, 51) is False, retired


def test_every_v45_phrase_rides_into_v46():
    for phrase in VERSIONS[45]["phrases"]:
        assert phrase in VERSIONS[46]["phrases"], phrase


def test_the_always_on_a_number_gloss_stays_gone_in_v46():
    """Scott's build-108 run: v45 told the model to describe the A-Number's
    place on the card on every ask, a 19-second line. A served copy carrying
    that sentence is a stale cut."""
    sp = _bundle_prompt(51)
    retired = "Ask for the A-Number as the number usually shown on the Green Card"
    assert retired in VERSIONS[50]["absent"]
    assert retired not in sp
    assert verify(sp + "\n" + retired, 51) is False


def test_every_v44_phrase_rides_into_v45():
    for phrase in VERSIONS[44]["phrases"]:
        assert phrase in VERSIONS[45]["phrases"], phrase
