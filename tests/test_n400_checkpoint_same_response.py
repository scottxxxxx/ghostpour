"""The agenda is one turn stale, so the response must be applied first.

conf-v25b: the first version of the checkpoint refusal fired eleven times
and EIGHT were false positives. The agenda arrives with the REQUEST, so it
cannot know about facts minted in the response being checked, and the
ordinary shape we asked the lane for is exactly the one it refused:
answering the last question of a part and summarising it in the same
breath.

Worse, the outcome inverted. All three genuine cases resolved true on
retry; all eight false positives resolved false and were DROPPED. Causal,
not chance: a premature claim can be fixed by not making it, a correct one
cannot be "fixed", so the model re-asserts it, is refused twice, and the
object dies. The guard kept the wrong claims and destroyed the right ones,
and eight legitimate checkpoint cards never reached the applicant.

Each case below is a real turn from that run.
"""
import json

import pytest

from app.services.n400_interviewer_guard import checkpoint_contradicts_agenda


def _agenda(node, part, ids):
    return f"{node} | Part {part}: X | {','.join(ids)} | A question?"


def _resp(part, filled=(), deferred=()):
    return json.dumps({
        "facts": [{"field_id": f, "value": "v"} for f in filled],
        "deferred": [{"field_id": f} for f in deferred],
        "section_checkpoint": {"part": part},
        "reply": {"en": "That's Part %d, is that right?" % part},
    })


# (label, part, agenda field ids still listed, ids this response settles, refuse?)
CASES = [
    ("turn 7  Part 1,  1 missing, filled 1", 1, ["p1.a"], ["p1.a"], False),
    ("turn 25 Part 2,  1 missing, filled 1", 2, ["p2.a"], ["p2.a"], False),
    ("turn 30 Part 3,  2 missing, filled 2", 3, ["p3.eye", "p3.hair"],
     ["p3.eye", "p3.hair"], False),
    ("turn 40 Part 4, 27 missing, filled 1", 4, ["p4.%d" % i for i in range(27)],
     ["p4.0"], True),
    ("turn 42 Part 5,  1 missing, filled 1", 5, ["p5.a"], ["p5.a"], False),
    ("turn 47 Part 6,  1 missing, filled 1", 6, ["p6.a"], ["p6.a"], False),
    ("turn 55 Part 7, 20 missing, filled 1", 7, ["p7.%d" % i for i in range(20)],
     ["p7.0"], True),
    ("turn 59 Part 8, 13 missing, filled 1", 8, ["p8.%d" % i for i in range(13)],
     ["p8.0"], True),
    ("turn 60 Part 7,  2 missing, filled 2", 7, ["p7.a", "p7.b"], ["p7.a", "p7.b"], False),
    ("turn 78 Part 11, 3 missing, filled 3", 11, ["p11.a", "p11.b", "p11.c"],
     ["p11.a", "p11.b", "p11.c"], False),
    ("turn 80 Part 9,  6 missing, filled 6 (THE OATH)", 9,
     ["p9.%d" % i for i in range(6)], ["p9.%d" % i for i in range(6)], False),
]


@pytest.mark.parametrize("label,part,listed,filled,refuse",
                         CASES, ids=[c[0] for c in CASES])
def test_every_real_case_from_conf_v25b(label, part, listed, filled, refuse):
    agenda = _agenda("q_p%d" % part, part, listed)
    got = checkpoint_contradicts_agenda(_resp(part, filled), agenda)
    assert (got is not None) == refuse, label


def test_the_run_totals_are_three_refused_and_eight_permitted():
    """The number that says the fix is right: it was 11 refused, 8 of them
    wrong. It must now refuse exactly the 3 genuine ones."""
    refused = sum(
        1 for _, part, listed, filled, _ in CASES
        if checkpoint_contradicts_agenda(
            _resp(part, filled), _agenda("q_p%d" % part, part, listed)) is not None)
    assert refused == 3, f"refused {refused}, want exactly the 3 genuine cases"


def test_a_deferral_settles_a_field_just_like_a_fact():
    """"She is checking it later" closes the node for this purpose, the same
    way drop_facts_that_are_also_deferred treats the deferral as the honest
    half of the pair."""
    agenda = _agenda("q_p4", 4, ["p4.a", "p4.b"])
    assert checkpoint_contradicts_agenda(
        _resp(4, filled=["p4.a"], deferred=["p4.b"]), agenda) is None


def test_a_part_still_genuinely_open_is_still_refused():
    """The guard must not be neutered into permitting everything: turn 79's
    original shape, one of six answered, must still refuse."""
    agenda = _agenda("q_p9_oath", 9, ["p9.%d" % i for i in range(6)])
    got = checkpoint_contradicts_agenda(_resp(9, filled=["p9.0"]), agenda)
    assert got is not None and got["open_nodes"] == ["q_p9_oath"]


def test_another_part_being_open_does_not_block_this_one():
    agenda = (_agenda("q_p3", 3, ["p3.a"]) + "\n"
              + _agenda("q_p9", 9, ["p9.a", "p9.b"]))
    assert checkpoint_contradicts_agenda(_resp(3, ["p3.a"]), agenda) is None
    assert checkpoint_contradicts_agenda(_resp(9, ["p9.a"]), agenda) is not None
