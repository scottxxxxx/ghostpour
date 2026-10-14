"""A node this response made moot is not open (Scott's run f0a763cc, 2026-10-14).

The agenda is one turn stale. A gate answered "no" in this response still has
its follow-up listed, and a child count of 0 still has its child rows, until
the client prunes them next turn. Two guards read those lines as open:

  t_011  p6.total_children = 0 and "That's Part 6: no children". The
         checkpoint was refused on q_p6_child2..4, retried, refused again and
         dropped, so a correct card never reached her.
  t_004  p2.has_uscis_account = no, then the settled-question swap spoke
         q_p2_uscis_account's "What is your USCIS online account number?",
         after cutting the model's own question at "a U.S.".

Agenda lines are the real ones from those requests, asks abridged. The t_004
reply is reconstructed from what she heard: GP does not store the retry's
text, and the words after "a U.S." are the obvious completion, not a record.
"""
import json

from app.services.n400_interviewer_guard import (
    _split_trailing_question, checkpoint_contradicts_agenda, drop_stale_asking,
    moot_nodes_in_this_response, replace_settled_question,
)

AG_P6 = (
    "q_p6_children_count | Part 6: Your children | p6.total_children | To start, how many children do you have?\n"
    "q_p6_child2 | Part 6: Your children | p6.child2.child_name,p6.child2.date_of_birth,p6.child2.residence,"
    "p6.child2.relationship,p6.child2.supported | Now the next child: their full name and date of birth.\n"
    "q_p6_child3 | Part 6: Your children | p6.child3.child_name,p6.child3.date_of_birth,p6.child3.residence,"
    "p6.child3.relationship,p6.child3.supported | Now the next child: their full name and date of birth.\n"
    "q_p6_child4 | Part 6: Your children | p6.child4.child_name,p6.child4.date_of_birth,p6.child4.residence,"
    "p6.child4.relationship,p6.child4.supported | Now the next child: their full name and date of birth.\n"
    "q_p7_job1 | Part 7: Your work | p7.employer1.employer_name,p7.employer1.occupation | Where do you work now?\n"
)


def _fact(fid, value):
    return {"field_id": fid, "value": value, "value_type": "string",
            "provenance": {"source": "user_stated", "confidence": 0.95, "utterance": "x"}}


def _p6(count):
    return json.dumps({
        "facts": [_fact("p6.total_children", count)],
        "asking": {"node_id": "q_p7_job1", "field_ids": ["p7.employer1.employer_name"]},
        "section_checkpoint": {"part": 6, "section": "Part 6: Your children", "awaiting_confirmation": False},
        "reply": {"en": "That's Part 6: no children. Part 7 is your work: where do you work now?"},
    })


def test_t011_no_children_closes_part_6():
    assert checkpoint_contradicts_agenda(_p6("0"), AG_P6) is None


def test_a_count_moots_only_the_rows_above_it():
    info = checkpoint_contradicts_agenda(_p6("2"), AG_P6)
    assert info is not None and info["open_nodes"] == ["q_p6_child2"]


def test_without_a_count_every_child_row_is_still_open():
    text = json.dumps({**json.loads(_p6("0")), "facts": []})
    info = checkpoint_contradicts_agenda(text, AG_P6)
    assert info["open_nodes"] == ["q_p6_child2", "q_p6_child3", "q_p6_child4", "q_p6_children_count"]


AG_T004 = (
    "q_p1_eligibility_basis | Part 1: Your eligibility | p1.eligibility_basis | Which one applies to you?\n"
    "q_p1_a_number | Part 1: Your eligibility | p1.a_number | What is your USCIS number, also called your A-Number?\n"
    "q_p1_full_name | Part 2: About you | p1.first_name,p2.middle_name,p1.last_name | Give me the name exactly as it is printed.\n"
    "q_p2_other_names_gate | Part 2: About you | p2.has_other_names | Part 2 is about you. Any other names you've used?\n"
    "q_p2_name_change | Part 2: About you | p2.name_change_requested | Would you like to legally change your name? | options: yes, no\n"
    "q_p2_uscis_account_gate | Part 2: About you | p2.has_uscis_account | Do you have a USCIS online account number? | options: yes, no\n"
    "q_p2_uscis_account | Part 2: About you | p2.uscis_account_number | What is your USCIS online account number?\n"
    "q_p1_dob | Part 2: About you | p1.date_of_birth | What is your date of birth?\n"
    "q_p3_ethnicity | Part 3: Your appearance | p3.ethnicity | Part 3 is what you look like. Are you Hispanic or Latino?\n"
)
T004_HEAD = ("Got your name, A-Number, date of birth and birth country noted, general provision for eligibility. "
             "When did you become a permanent resident, and was either of your parents a U.S.")


def _t004(has_account):
    return json.dumps({
        "facts": [_fact(f, v) for f, v in [
            ("p1.eligibility_basis", "general_provision"), ("p1.a_number", "230911782"),
            ("p1.first_name", "Jacinta"), ("p1.last_name", "Thackeray"), ("p2.has_other_names", "no"),
            ("p2.name_change_requested", "no"), ("p2.has_uscis_account", has_account),
            ("p1.date_of_birth", "1985-12-16")]],
        "asking": {"node_id": "q_p2_other_names_gate", "field_ids": ["p2.has_other_names"]},
        "reply": {"en": T004_HEAD + " citizen before you turned 18?"},
    })


def _swap(text):
    text, _ = drop_stale_asking(text, AG_T004)
    return json.loads(replace_settled_question(text, AG_T004, "en")[0])


def test_t004_a_no_gate_never_speaks_its_follow_up():
    o = _swap(_t004("no"))
    assert "online account number" not in o["reply"]["en"]
    assert o["asking"]["node_id"] == "q_p3_ethnicity"


def test_t004_the_cut_never_lands_inside_u_s():
    o = _swap(_t004("no"))
    assert "a U.S." not in o["reply"]["en"]
    assert o["reply"]["en"].startswith("Got your name, A-Number, date of birth and birth country noted, "
                                       "general provision for eligibility. Part 3 is what you look like.")


def test_a_yes_gate_leaves_its_follow_up_next():
    o = _swap(_t004("yes"))
    assert o["asking"]["node_id"] == "q_p2_uscis_account"


def test_the_gate_rule_reads_only_its_own_stem():
    turn = {"facts": [_fact("p2.has_uscis_account", "no")]}
    assert moot_nodes_in_this_response(turn, AG_T004) == {"q_p2_uscis_account"}


def test_the_split_keeps_abbreviations_inside_their_sentence():
    assert _split_trailing_question("Noted. Are you the Spouse of U.S. Citizen?") == (
        "Noted. ", "Are you the Spouse of U.S. Citizen?")
    assert _split_trailing_question("Got it. Was your parent a U.S. citizen?") == (
        "Got it. ", "Was your parent a U.S. citizen?")
    assert _split_trailing_question("Anotado. ¿Nació en México?") == ("Anotado. ", "¿Nació en México?")
    assert _split_trailing_question("What is your name?") == ("", "What is your name?")
    assert _split_trailing_question("No question here.") is None
