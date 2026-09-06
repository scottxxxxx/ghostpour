"""The N-400 knowledge pack: every claim carries a source, or it is a defect.

`policy-matrix.json` permits `explain_question_literal` on the stated basis
that it is "reading back what USCIS itself publishes about a question, in
plainer words". A row without a citation is not merely undocumented, it is
outside the capability that authorises this file to exist. DOJ EOIR's fraud
guide is the sharpest formulation of the boundary: a non-attorney "CANNOT
tell you which immigration forms to use or what answers to put on the
forms".

So these tests check two things a reviewer cannot check by reading: that
nothing is uncited, and that nothing tells her what to answer.
"""
import json
import pathlib

import pytest

PACK = json.loads(
    (pathlib.Path("config/remote/n400/form-knowledge.json")).read_text())


def _rows(node, path=""):
    """Every dict that carries a `basis`, with its path."""
    if isinstance(node, dict):
        if "basis" in node:
            yield path, node
        for k, v in node.items():
            if not k.startswith("_"):
                yield from _rows(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _rows(v, f"{path}[{i}]")


def test_the_pack_is_server_only():
    """It is reasoning material, not client config. It must never be served
    to a device."""
    assert PACK["server_only"] is True


def test_it_names_the_form_edition_it_describes():
    """The form was reorganised from 18 parts to 16 in the 04/01/24 edition
    and changed again on 01/20/25. A pack that does not say which edition it
    describes cannot be checked against anything."""
    assert PACK["form_edition"] == "01/20/25"


def test_every_claim_carries_a_basis_and_a_source():
    missing = [p for p, row in _rows(PACK)
               if not row.get("source") and row.get("basis") == "PUBLISHED"]
    assert missing == [], f"PUBLISHED rows with no source: {missing}"


def test_every_basis_is_a_recognised_value():
    bad = [(p, row["basis"]) for p, row in _rows(PACK)
           if row["basis"] not in ("PUBLISHED", "NEEDS_LEGAL")]
    assert bad == [], f"unrecognised basis values: {bad}"


def test_there_is_at_least_one_row(): 
    assert len(list(_rows(PACK))) >= 8


SUGGESTIVE = ("you should answer", "answer yes", "answer no", "say yes",
              "say no", "most people", "we recommend", "the right answer",
              "put yes", "put no")


def test_nothing_in_the_pack_tells_her_what_to_answer():
    """The one thing this file may never contain. 'Most people say no' on the
    fee question was graded as a nudge and is, under the UPL framing,
    suggesting the answer on the one question with a money consequence."""
    blob = json.dumps(PACK, ensure_ascii=False).lower()
    hits = [s for s in SUGGESTIVE if s in blob]
    assert hits == [], f"the pack suggests an answer: {hits}"


def test_the_three_printed_glosses_are_uscis_wording_verbatim():
    """Not a paraphrase. v25 shipped four glosses GP wrote and two NARROWED
    the question, which changes what she is answering yes or no to."""
    t = PACK["terms"]
    assert t["bear_arms"]["gloss_official_en"] == "carry weapons"
    assert (t["noncombatant_services"]["gloss_official_en"]
            == "do something that does not include fighting in a war")
    assert (t["work_of_national_importance"]["gloss_official_en"]
            == "do non-military work that the U.S. Government says is important to the country")


def test_the_narrowings_that_shipped_in_v25_are_absent_FROM_THE_GLOSSES():
    """Scoped to the gloss fields, not the whole file.

    The first version of this test searched the whole blob and failed on the
    pack's own warning that work of national importance is NOT limited to an
    emergency. A naive substring check cannot tell a narrowing from the note
    forbidding it, which is the same polarity problem the oath-modification
    detector had: the correct sentence and the defect share their words.
    """
    glosses = " ".join(
        str(row.get(k) or "")
        for row in PACK["terms"].values() if isinstance(row, dict)
        for k in ("gloss_official_en", "form_wording_en")).lower()
    for narrowing in ("in an emergency", "as a soldier", "during wartime"):
        assert narrowing not in glosses, f"a narrowing crept into a gloss: {narrowing}"


def test_the_pack_explicitly_warns_against_the_narrowing_it_used_to_ship():
    """The warning must be present, which is the opposite assertion and the
    reason the test above had to be scoped."""
    note = PACK["terms"]["work_of_national_importance"]["note"].lower()
    assert "not limited to" in note and "emergency" in note


def test_title_of_nobility_has_no_invented_gloss():
    """USCIS publishes none, so the pack must carry none and must say what is
    unsettled instead of filling it in."""
    row = PACK["terms"]["title_of_nobility"]
    assert row["gloss_official_en"] is None
    assert row["unsettled"], "the gap must be stated, not left blank"


def test_the_one_clause_with_no_modification_is_pinned():
    r = PACK["rules"]["oath_modification"]
    assert r["not_modifiable"] == ["p9.willing_national_importance"]
    assert set(r["modifiable"]) == {"p9.willing_bear_arms", "p9.willing_noncombatant"}
    assert len(r["source"]) >= 2, "the one irreversible rule carries two citations"


def test_the_dead_selective_service_url_is_recorded_as_a_hazard():
    """The USCIS instructions still send applicants to a page that 404s."""
    h = PACK["hazards"]["selective_service_status_letter"]
    assert "NEVER" in h["action"]
    assert "sss.gov/verify/sil" in h["hazard"]


def test_the_fee_floor_trap_is_recorded():
    """A stale USCIS FAQ says 150% to 400%; the current instructions say 400%
    or less with no floor. Encoding the FAQ would tell a poorer applicant she
    is ineligible."""
    assert "150%" in PACK["hazards"]["fee_reduction_floor"]["hazard"]
    assert "400% or less" in PACK["hazards"]["fee_reduction_floor"]["action"]


def test_the_widow_row_says_both_halves():
    """'The form does not ask' is true of the FIELDS and false of the CASE:
    USCIS still requires evidence a prior marriage ended."""
    row = PACK["not_asked"]["deceased_or_former_spouse"]
    assert row["say"] and row["say_es"]
    assert any("EVIDENCE" in line or "evidence" in line
               for line in row["still_true_of_the_case"])


def test_no_dashes_anywhere():
    blob = json.dumps(PACK, ensure_ascii=False)
    assert "—" not in blob and "–" not in blob
