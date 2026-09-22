"""A value the floor accepts but a predicate cannot read.

The N-400 client's typing floor accepted any non-empty string on a yes/no
field. `p9.oath_disability` gates four oath fields through
`if:p9.oath_disability==false`. Store "sí" there and the predicate cannot
read it, the condition goes false, the four dependent fields stop being
REQUIRED, their blanks stop being findings, and the document gate that
would have blocked her goes quiet.

Their statement of it: A VALUE THE FLOOR ACCEPTS BUT THE PREDICATE CANNOT
READ IS MORE DANGEROUS THAN ONE THE FLOOR REFUSES. Refusing leaves the
field blank; blank is a finding; a finding blocks the document. Accepting
an unreadable value is silent in both directions.

They canonicalise and refuse on their side. THIS SIDE ONLY CHECKS, by
their rule: a check that fails loudly is the invariant made explicit,
while two filters silently agreeing is the danger, so only one side may
write.
"""
import json

import pytest

from app.services.n400_interviewer_guard import (
    agenda_options, mark_values_outside_declared_options,
)

BOOL_NODE = ("q_p8_trips_gate | Part 8: Trips | p8.took_trips | "
             "Have you taken any trips? | options: yes, no")
# A five-field node whose options are a UNION across different fields.
UNION_NODE = ("q_p6_child1 | Part 6: Children | p6.child1.child_name,"
              "p6.child1.date_of_birth,p6.child1.residence,p6.child1.relationship,"
              "p6.child1.supported | Tell me about your child | options: "
              "resides_with_me, not_with_me, unknown_missing, biological, stepchild")
# The same node once four fields are answered: it SHRINKS to one and still
# carries the union. This is the case that broke the single-field rule.
UNION_SHRUNK = ("q_p6_child1 | Part 6: Children | p6.child1.supported | "
                "Are you supporting her? | options: resides_with_me, "
                "not_with_me, unknown_missing, biological, stepchild")


def _facts(*pairs):
    return json.dumps({"facts": [{"field_id": f, "value": v} for f, v in pairs]})


def test_the_option_set_comes_from_the_agenda_not_a_hardcoded_list():
    assert agenda_options(BOOL_NODE) == {"q_p8_trips_gate": {"yes", "no"}}


@pytest.mark.parametrize("value,marked", [
    ("yes", False), ("no", False), ("Yes", False), ("NO", False),
    ("sí", True), ("quizás", True), ("maybe", True), ("true", True),
    ("", True), (True, True), (None, True),
])
def test_a_boolean_gate_value_must_be_readable(value, marked):
    """"sí" is the exact shape that silences the document gate."""
    _, off = mark_values_outside_declared_options(
        _facts(("p8.took_trips", value)), BOOL_NODE)
    assert bool(off) == marked, repr(value)


def test_a_union_node_is_never_checked():
    """q_p6_child1 declares residence AND relationship options across five
    fields including the child's name. Applying the set to all of them
    marked 'Mariana' and '2001-02-11' as invalid: 17 false marks in 548
    real turns."""
    _, off = mark_values_outside_declared_options(
        _facts(("p6.child1.child_name", "Mariana"),
               ("p6.child1.date_of_birth", "2001-02-11")), UNION_NODE)
    assert off == []


def test_a_shrunken_union_node_is_still_never_checked():
    """THE ONE THAT BROKE THE SINGLE-FIELD RULE. A partly answered node
    lists only the ids still empty, so a five-field node becomes a
    one-field node while still carrying the union. 7 more false marks on
    p6.child1.supported = 'yes', which is a perfectly good value."""
    _, off = mark_values_outside_declared_options(
        _facts(("p6.child1.supported", "yes")), UNION_SHRUNK)
    assert off == [], "a shrunken union node must not be treated as a boolean gate"


def test_it_marks_rather_than_rewrites():
    """Their rule: only one side may write. If GP transformed as well,
    their floor would go on working and neither side would learn that the
    shape had moved."""
    out, off = mark_values_outside_declared_options(
        _facts(("p8.took_trips", "sí")), BOOL_NODE)
    turn = json.loads(out)
    assert turn["facts"][0]["value"] == "sí", "the value must be left exactly as it came"
    assert turn["values_outside_declared_options"][0]["declared"] == ["no", "yes"]
    assert off[0]["field_id"] == "p8.took_trips"


def test_no_agenda_and_no_options_are_both_silent():
    for agenda in (None, "", "q_x | Part 1: X | p1.a | A question?"):
        _, off = mark_values_outside_declared_options(
            _facts(("p1.a", "anything at all")), agenda)
        assert off == []


# --- the client's per-field catalogue (metadata.choice_fields, 2026-09-22) ---------------------
#
# Per FIELD, so the union that forced the 29-gate rule above has nothing to
# map. Measured across the auditor's 3,047 interviewer turns before it
# shipped: 3 marks, all values outside the ids, 0 near-misses, and the
# agenda-only rule marked 0 on the same turns (qa/measure_outside_options_catalogue.py).

CATALOGUE = {
    "p1.eligibility_basis": ["general_provision", "spouse_usc", "vawa", "other"],
    "p5.spouse_citizen_how": ["by_birth", "other"],
    "p6.child1.residence": ["resides_with_me", "not_with_me"],
    "p6.child1.relationship": ["biological", "stepchild"],
    "p8.took_trips": ["yes", "no"],
}
MARITAL_NODE = ("q_p5_marital_status | Part 5: Marital | p5.marital_status | "
                "What is your marital status? | options: single, married, divorced, widowed")


def test_with_the_catalogue_a_value_outside_the_fields_ids_is_marked_even_off_the_agenda():
    # The real s1-v6-full#31 mint: volunteered on the marital turn, no agenda line for it.
    out, off = mark_values_outside_declared_options(
        _facts(("p5.marital_status", "married"), ("p5.spouse_citizen_how", "citizen")), MARITAL_NODE, CATALOGUE)
    assert [(o["field_id"], o["value"], o["declared"]) for o in off] == [
        ("p5.spouse_citizen_how", "citizen", ["by_birth", "other"])]
    assert off[0]["reason"] == "value is not one of the options the form declares for this field"
    assert json.loads(out)["values_outside_declared_options"] == off
    # Without the catalogue the same turn is invisible: the marital node is not a yes/no gate.
    assert mark_values_outside_declared_options(
        _facts(("p5.marital_status", "married"), ("p5.spouse_citizen_how", "citizen")), MARITAL_NODE)[1] == []


def test_with_the_catalogue_a_union_node_is_checked_per_field_with_no_false_marks():
    # The five-field union that produced 17 false marks under the agenda rule.
    text = _facts(("p6.child1.child_name", "Mariana"), ("p6.child1.date_of_birth", "2001-02-11"),
                  ("p6.child1.residence", "resides_with_me"), ("p6.child1.relationship", "biological"))
    assert mark_values_outside_declared_options(text, UNION_NODE, CATALOGUE)[1] == []
    _, off = mark_values_outside_declared_options(
        _facts(("p6.child1.residence", "biological")), UNION_NODE, CATALOGUE)
    assert [(o["field_id"], o["value"]) for o in off] == [("p6.child1.residence", "biological")]


@pytest.mark.parametrize("value", ["yes", "No", " by_birth "])
def test_case_and_whitespace_are_folded_against_the_catalogue(value):
    field = "p8.took_trips" if value.strip().lower() in ("yes", "no") else "p5.spouse_citizen_how"
    assert mark_values_outside_declared_options(_facts((field, value)), None, CATALOGUE)[1] == []


def test_a_field_the_catalogue_does_not_list_is_never_marked():
    assert mark_values_outside_declared_options(
        _facts(("p1.a_number", "A204881367"), ("p5.marital_status", "sí")), MARITAL_NODE, CATALOGUE)[1] == []


@pytest.mark.parametrize("bad", ["yes,no", {"p8.took_trips": "yes,no"}, {"p8.took_trips": []}, {7: ["yes", "no"]}])
def test_a_malformed_catalogue_falls_back_to_the_agenda_gate_rule(bad):
    _, off = mark_values_outside_declared_options(_facts(("p8.took_trips", "sí")), BOOL_NODE, bad)
    assert [o["reason"] for o in off] == ["value is not one of the options the agenda declared for this node"]


def test_the_route_hands_the_catalogue_to_the_guard():
    src = open("app/routers/chat.py").read()
    call = src.index("response.text = guard_response_text(")
    assert 'choice_fields=body.get_meta("choice_fields")' in src[call:call + 400]
