"""GET /v1/i765/policy (2026-10-09): the N-400 matrix shape over I-765's own
document. One URL for the client; its own namespace on the server, so a
wrong app id can never be handed another form's rules."""
from __future__ import annotations

import json

from app.services import n400_policy as policy

N400_DOC = json.load(open("config/remote/n400/policy-matrix.json"))
I765_DOC = json.load(open("config/remote/i765/policy-matrix.json"))


def test_the_route_answers_the_n400_shape_for_form_i765(client):
    r = client.get("/v1/i765/policy?state=TX")
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["schema_version"], m["state"], m["form"]) == (1, "TX", "I-765")
    assert m["matrix_version"] == 1 and m["legal_review_status"] == "PENDING"
    names = [c["capability"] for c in m["capabilities"]]
    assert names == [c["capability"] for c in N400_DOC["capabilities"]], "the shared client decodes one set"
    by = {c["capability"]: c for c in m["capabilities"]}
    assert by["populate_field"]["outcome"] == "ALLOW"
    assert by["determine_eligibility"]["outcome"] == "BLOCK"
    assert by["determine_eligibility"]["reason_code"] == "eligibility_and_category_are_uscis_determinations"
    assert by["recommend_answer"]["outcome"] == "ESCALATE"
    assert by["escalate_to_attorney"]["outcome"] == "ALLOW"


def test_outside_texas_mirrors_the_n400_outcomes(client):
    tx = {c["capability"]: c["outcome"] for c in client.get("/v1/i765/policy?state=TX").json()["capabilities"]}
    ca = {c["capability"]: c["outcome"] for c in client.get("/v1/i765/policy?state=CA").json()["capabilities"]}
    n400_ca = {c["capability"]: c["outcome"] for c in client.get("/v1/n400/policy?state=CA").json()["capabilities"]}
    assert ca == n400_ca, "carried over from N-400 per state, as asked"
    assert tx["populate_field"] == "ALLOW" and ca["populate_field"] == "RESTRICT"
    assert tx["assess_moral_character_impact"] == "ESCALATE" and ca["assess_moral_character_impact"] == "BLOCK"


def test_asking_the_i765_route_about_another_form_fails_closed(client):
    m = client.get("/v1/i765/policy?state=TX&form=N-400").json()
    assert m["form"] == "N-400"
    assert {c["outcome"] for c in m["capabilities"]} == {"BLOCK"}
    assert {c["reason_code"] for c in m["capabilities"]} == {"no_row_for_jurisdiction"}


def test_each_route_reads_only_its_own_document(client):
    configs = dict(client.app.state.remote_configs)
    client.app.state.remote_configs = {k: v for k, v in configs.items() if k != "i765/policy-matrix"}
    r = client.get("/v1/i765/policy?state=TX")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "policy_matrix_unavailable"
    assert client.get("/v1/n400/policy?state=TX").status_code == 200, "n400 is untouched"
    client.app.state.remote_configs = {k: v for k, v in configs.items() if k != "n400/policy-matrix"}
    assert client.get("/v1/n400/policy?state=TX").status_code == 503
    assert client.get("/v1/i765/policy?state=TX").status_code == 200, "and i765 never borrows n400's"
    client.app.state.remote_configs = configs


def test_request_shape_errors_match_the_n400_route(client):
    assert client.get("/v1/i765/policy").json()["detail"]["code"] == "missing_state"
    assert client.get("/v1/i765/policy?state=*").json()["detail"]["code"] == "wildcard_state_not_a_jurisdiction"


def test_the_slug_comes_from_the_registry_and_the_default_is_still_n400():
    assert policy.policy_slug("i765") == "i765/policy-matrix"
    assert policy.policy_slug() == "n400/policy-matrix"
    assert policy.policy_document({"i765/policy-matrix": I765_DOC}, "i765") is I765_DOC
    assert policy.policy_document({"i765/policy-matrix": I765_DOC}) is None, "the default never reads i765's"


def test_the_document_is_an_engineering_default_and_says_so():
    assert I765_DOC["form"] == "I-765"
    assert all(row["form"] == "I-765" for c in I765_DOC["capabilities"] for row in c["matrix"])
    assert I765_DOC["legal_review"]["status"] == "PENDING" and I765_DOC["legal_review"]["reviewed_at"] is None
    assert policy.unreviewed_permissive_rules(I765_DOC) == []
    if I765_DOC["legal_review"]["status"] == "REVIEWED":  # the invariant n400 pins, kept for the day it flips
        assert not policy.unreviewed_permissive_rules(I765_DOC)
    # No N-400 node name survived the carry-over; the I-765 agenda names its own.
    assert all(c.get("halt_branch_nodes", []) == [] for c in I765_DOC["capabilities"])
    assert "N-400" not in json.dumps(I765_DOC["capabilities"]).replace("as for N-400", "").replace("the N-400 outcomes", "")
