"""N-400 policy engine: fail-closed evaluation, and the shipped matrix itself.

Two halves, and they are testing different things on purpose.

The ENGINE tests build their own tiny documents inline, so they say what the
code does regardless of what we happen to ship today. The SHIPPED MATRIX
tests read `config/remote/n400/policy-matrix.json` out of the repo bundle
(NOT out of the overlay, which does not exist in CI and would make a local
pass mean nothing) and pin the rows N-400's own fixture depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.services.n400_policy as pol

_BUNDLED = (Path(__file__).parent.parent / "config" / "remote" / "n400"
            / "policy-matrix.json")


def _doc(*rules, copy: dict | None = None, review: dict | None = None) -> dict:
    return {
        "version": 1,
        "capabilities": list(rules),
        "outcome_copy": copy if copy is not None else {
            "RESTRICT": {"message": {"en": "restricted"}},
            "ESCALATE": {"message": {"en": "escalated"}, "cta": {"type": "find_attorney"}},
            "BLOCK": {"message": {"en": "blocked"}, "cta": {"type": "find_attorney"}},
        },
        "legal_review": review or {"status": "PENDING"},
    }


def _configs(doc: dict | None) -> dict:
    return {} if doc is None else {pol.policy_slug(): doc}


# --- the slug is built from the registry, never from a header ---------------

def test_policy_slug_uses_the_registered_n400_dir():
    assert pol.policy_slug() == "n400/policy-matrix"


def test_document_is_not_read_from_the_flat_name():
    """A flat `policy-matrix` must never satisfy N-400's lookup.

    resolve_app_dir fails open to ShoulderSurf and candidate_slugs falls back
    to the bare name, both correct for served copy. Inheriting a jurisdiction
    matrix that way would be a compliance decision made by a filename.
    """
    doc = _doc({"capability": "transcribe",
                "matrix": [{"state": "*", "form": "N-400", "outcome": "ALLOW"}]})
    assert pol.policy_document({"policy-matrix": doc}) is None
    assert pol.policy_document({"shouldersurf/policy-matrix": doc}) is None


# --- fail closed ------------------------------------------------------------

@pytest.mark.parametrize("configs,expected_reason", [
    (None, pol.REASON_DOCUMENT_MISSING),
    ({}, pol.REASON_DOCUMENT_MISSING),
])
def test_missing_document_blocks(configs, expected_reason):
    d = pol.evaluate(configs, "transcribe", "TX")
    assert d["outcome"] == "BLOCK"
    assert d["reason_code"] == expected_reason


def test_malformed_document_blocks():
    configs = _configs({"version": 1, "capabilities": "not a list"})
    d = pol.evaluate(configs, "transcribe", "TX")
    assert d["outcome"] == "BLOCK"
    assert d["reason_code"] == pol.REASON_DOCUMENT_MISSING


def test_capability_nobody_wrote_a_row_for_blocks():
    configs = _configs(_doc({"capability": "transcribe",
                             "matrix": [{"state": "*", "form": "N-400",
                                         "outcome": "ALLOW"}]}))
    d = pol.evaluate(configs, "read_the_applicants_mind", "TX")
    assert d["outcome"] == "BLOCK"
    assert d["reason_code"] == pol.REASON_UNKNOWN_CAPABILITY


def test_no_row_for_the_form_blocks_even_when_the_state_matches():
    configs = _configs(_doc({"capability": "populate_field",
                             "matrix": [{"state": "TX", "form": "N-600",
                                         "outcome": "ALLOW"}]}))
    d = pol.evaluate(configs, "populate_field", "TX", "N-400")
    assert d["outcome"] == "BLOCK"
    assert d["reason_code"] == pol.REASON_NO_MATCHING_ROW


def test_an_outcome_string_with_a_typo_blocks():
    """A row saying ALOW must not read as ALLOW, and must not read as
    'unrecognised, so ignore this row and try the next one' either."""
    configs = _configs(_doc({"capability": "populate_field",
                             "matrix": [{"state": "TX", "form": "N-400",
                                         "outcome": "ALOW"},
                                        {"state": "*", "form": "N-400",
                                         "outcome": "ALLOW"}]}))
    d = pol.evaluate(configs, "populate_field", "TX")
    assert d["outcome"] == "BLOCK"
    assert d["reason_code"] == pol.REASON_INVALID_OUTCOME


# --- exact beats wildcard, in either authoring order ------------------------

@pytest.mark.parametrize("rows", [
    [{"state": "TX", "form": "N-400", "outcome": "ESCALATE"},
     {"state": "*", "form": "N-400", "outcome": "BLOCK"}],
    [{"state": "*", "form": "N-400", "outcome": "BLOCK"},
     {"state": "TX", "form": "N-400", "outcome": "ESCALATE"}],
])
def test_exact_state_wins_regardless_of_row_order(rows):
    configs = _configs(_doc({"capability": "assess_moral_character_impact",
                             "matrix": rows}))
    assert pol.evaluate(configs, "assess_moral_character_impact",
                        "TX")["outcome"] == "ESCALATE"
    assert pol.evaluate(configs, "assess_moral_character_impact",
                        "CA")["outcome"] == "BLOCK"


def test_state_matching_is_case_and_whitespace_insensitive():
    configs = _configs(_doc({"capability": "populate_field",
                             "matrix": [{"state": "TX", "form": "N-400",
                                         "outcome": "ALLOW"},
                                        {"state": "*", "form": "N-400",
                                         "outcome": "RESTRICT"}]}))
    for asked in ("tx", " TX ", "Tx"):
        assert pol.evaluate(configs, "populate_field", asked)["outcome"] == "ALLOW"


# --- what rides on a decision ----------------------------------------------

def test_allow_carries_no_copy_and_no_halt_nodes():
    configs = _configs(_doc({"capability": "transcribe",
                             "halt_branch_nodes": ["q_should_not_appear"],
                             "matrix": [{"state": "*", "form": "N-400",
                                         "outcome": "ALLOW"}]}))
    d = pol.evaluate(configs, "transcribe", "TX")
    assert d["outcome"] == "ALLOW"
    assert "message" not in d
    assert d["halt_branch_nodes"] == []


def test_restrict_keeps_the_interview_moving():
    """RESTRICT means proceed with a notice, so it carries copy but halts
    nothing. A restrict that halted a branch would silently truncate the
    interview in 49 states."""
    configs = _configs(_doc({"capability": "populate_field",
                             "halt_branch_nodes": ["q_p9_arrested_explain"],
                             "matrix": [{"state": "*", "form": "N-400",
                                         "outcome": "RESTRICT"}]}))
    d = pol.evaluate(configs, "populate_field", "CA")
    assert d["outcome"] == "RESTRICT"
    assert d["message"]["en"] == "restricted"
    assert d["halt_branch_nodes"] == []


def test_escalate_carries_copy_cta_and_halt_nodes():
    configs = _configs(_doc({"capability": "assess_moral_character_impact",
                             "reason_code": "part9_arrest_disclosure",
                             "halt_branch_nodes": ["q_p9_arrested_explain"],
                             "matrix": [{"state": "TX", "form": "N-400",
                                         "outcome": "ESCALATE"}]}))
    d = pol.evaluate(configs, "assess_moral_character_impact", "TX")
    assert d["outcome"] == "ESCALATE"
    assert d["reason_code"] == "part9_arrest_disclosure"
    assert d["cta"]["type"] == "find_attorney"
    assert d["halt_branch_nodes"] == ["q_p9_arrested_explain"]


def test_a_row_reason_code_beats_the_rules_default():
    configs = _configs(_doc({"capability": "populate_field",
                             "reason_code": "rule_level",
                             "matrix": [{"state": "TX", "form": "N-400",
                                         "outcome": "BLOCK",
                                         "reason_code": "row_level"}]}))
    assert pol.evaluate(configs, "populate_field",
                        "TX")["reason_code"] == "row_level"


# --- effective matrix -------------------------------------------------------

def test_effective_matrix_agrees_with_per_turn_evaluation():
    """The picker and the turn must not be able to disagree."""
    configs = _configs(_doc(
        {"capability": "populate_field",
         "matrix": [{"state": "TX", "form": "N-400", "outcome": "ALLOW"},
                    {"state": "*", "form": "N-400", "outcome": "RESTRICT"}]},
        {"capability": "determine_eligibility",
         "matrix": [{"state": "*", "form": "N-400", "outcome": "BLOCK"}]},
    ))
    for state in ("TX", "CA"):
        eff = pol.effective_matrix(configs, state)
        for row in eff["capabilities"]:
            assert row["outcome"] == pol.evaluate(
                configs, row["capability"], state)["outcome"]


def test_effective_matrix_reports_the_review_status():
    configs = _configs(_doc({"capability": "transcribe",
                             "matrix": [{"state": "*", "form": "N-400",
                                         "outcome": "ALLOW"}]},
                            review={"status": "PENDING"}))
    assert pol.effective_matrix(configs, "TX")["legal_review_status"] == "PENDING"


# --- the shipped document ---------------------------------------------------

@pytest.fixture(scope="module")
def shipped() -> dict:
    return json.loads(_BUNDLED.read_text())


@pytest.fixture(scope="module")
def shipped_configs(shipped) -> dict:
    return {pol.policy_slug(): shipped}


def test_shipped_matrix_loads_and_declares_a_version(shipped):
    """`load_remote_configs` skips any file without a top-level version, so a
    missing one would make this document silently absent in production and
    every turn would fail closed."""
    assert isinstance(shipped.get("version"), int)
    assert pol.policy_document({pol.policy_slug(): shipped}) is not None


def test_shipped_moral_character_rows_match_n400s_own_fixture(shipped_configs):
    """Pinned against Sources/Resources/MockFixtures/policy_matrix.json on the
    client. If these two ever disagree the client's decode tests still pass
    and the served answer is different, which is invisible from either side."""
    assert pol.evaluate(shipped_configs, "assess_moral_character_impact",
                        "TX")["outcome"] == "ESCALATE"
    for state in ("CA", "NY", "FL"):
        assert pol.evaluate(shipped_configs, "assess_moral_character_impact",
                            state)["outcome"] == "BLOCK"


@pytest.mark.parametrize("capability", ["interpret_legally", "determine_eligibility"])
def test_no_state_unlocks_legal_judgment(capability, shipped_configs):
    for state in ("TX", "CA", "NY", "PR", "DC"):
        assert pol.evaluate(shipped_configs, capability, state)["outcome"] == "BLOCK"


def test_escalate_to_attorney_is_allowed_everywhere(shipped_configs):
    """Fail-closed only works if the exit is always open: a BLOCK whose copy
    points at an attorney is useless if referring out is itself blocked."""
    for state in ("TX", "CA", "WY"):
        assert pol.evaluate(shipped_configs, "escalate_to_attorney",
                            state)["outcome"] == "ALLOW"


def test_texas_is_the_only_populate_field_jurisdiction(shipped_configs):
    assert pol.evaluate(shipped_configs, "populate_field", "TX")["outcome"] == "ALLOW"
    for state in ("CA", "NY", "NM"):
        assert pol.evaluate(shipped_configs, "populate_field",
                            state)["outcome"] == "RESTRICT"


def test_every_shipped_rule_declares_a_basis(shipped):
    """`basis` is what tells Scott's reviewer which rows are load-bearing law
    and which are engineering defaults. A rule without one reads as reviewed."""
    for rule in shipped["capabilities"]:
        assert rule.get("basis") in ("PUBLISHED", "NEEDS_LEGAL"), rule.get("capability")
        assert (rule.get("basis_note") or "").strip()


def test_shipped_copy_covers_every_non_allow_outcome(shipped):
    """Every outcome that has to say something to the applicant must have
    copy in all three wire locales, or the client renders a refusal with no
    reason in someone's language."""
    for outcome in ("RESTRICT", "ESCALATE", "BLOCK"):
        block = shipped["outcome_copy"][outcome]
        for locale in ("en", "es", "pt"):
            assert (block["message"].get(locale) or "").strip(), (outcome, locale)


def test_stamping_the_review_cannot_leave_a_judgment_call_permissive(shipped):
    """The invariant that makes `legal_review.status` mean something.

    Stamping REVIEWED is one line of JSON and is the half of a legal review
    most likely to happen without the other half. If it is stamped, no row
    still labelled an engineering default may be sitting at ALLOW or RESTRICT.
    """
    permissive = pol.unreviewed_permissive_rules(shipped)
    if shipped["legal_review"]["status"] == "REVIEWED":
        assert permissive == [], permissive
    else:
        # Not yet reviewed: the guard is dormant, so prove it can still bite.
        assert pol.unreviewed_permissive_rules({"capabilities": [
            {"capability": "recommend_answer", "basis": "NEEDS_LEGAL",
             "matrix": [{"state": "TX", "form": "N-400", "outcome": "ALLOW"}]},
        ]}) == ["recommend_answer"]


# --- the endpoint -----------------------------------------------------------

def test_endpoint_requires_a_state(client):
    r = client.get("/v1/n400/policy")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "missing_state"


def test_endpoint_rejects_the_wildcard_as_a_jurisdiction(client):
    r = client.get("/v1/n400/policy", params={"state": "*"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "wildcard_state_not_a_jurisdiction"


def test_endpoint_503s_when_the_matrix_is_absent(client):
    """An outage must not render as 'policy says no'."""
    client.app.state.remote_configs = {}
    r = client.get("/v1/n400/policy", params={"state": "TX"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "policy_matrix_unavailable"


def test_endpoint_serves_the_effective_matrix(client, shipped):
    client.app.state.remote_configs = {pol.policy_slug(): shipped}
    r = client.get("/v1/n400/policy", params={"state": "TX", "form": "N-400"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "TX"
    assert body["form"] == "N-400"
    assert body["legal_review_status"] == shipped["legal_review"]["status"]
    outcomes = {c["capability"]: c["outcome"] for c in body["capabilities"]}
    assert outcomes["assess_moral_character_impact"] == "ESCALATE"
    assert outcomes["determine_eligibility"] == "BLOCK"
    assert outcomes["populate_field"] == "ALLOW"


def test_endpoint_normalizes_a_lowercase_state(client, shipped):
    client.app.state.remote_configs = {pol.policy_slug(): shipped}
    r = client.get("/v1/n400/policy", params={"state": "tx"})
    assert r.status_code == 200
    assert r.json()["state"] == "TX"
