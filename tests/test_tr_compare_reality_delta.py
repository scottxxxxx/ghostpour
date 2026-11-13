"""What `missed` means on tr_compare_reality (2026-08-07, TR).

TR asked which reading our prompt encouraged, because the answer decides
whether their UI is honest. Their rendering is landed green, drifted amber,
missed GREY, unplanned indigo, with grey meaning "neutral absence, nobody's
fault".

The answer was: both readings, and the wrong one was written down. The
prompt said to mark missed when the topic "never came up OR WAS NOT
DELIVERED", so a candidate who was asked their hardest question and
fumbled it landed in the bucket TR renders as a neutral absence. The one
thing they most needed to hear about was being drawn as if it never
happened.

Tightened rather than split, which is also the option that needs no client
change: missed is strictly an absence, and a topic that came up and went
badly is drifted, because reality diverged from the plan.
"""

import json
import pathlib

CONFIG = pathlib.Path("config/remote/techrehearsal/compare-reality.json")


def _prompt() -> str:
    return " ".join(json.loads(CONFIG.read_text())["systemPrompt"].split())


def test_missed_is_an_absence_and_says_so():
    p = _prompt()
    assert '"missed" = the topic NEVER CAME UP' in p
    assert "nobody is at fault" in p


def test_a_failure_to_deliver_is_never_missed():
    """The exact misclassification TR flagged. A fumble rendered grey reads
    as "this never happened", which is the opposite of true and hides the
    only thing worth coaching."""
    p = _prompt()
    assert 'DID come up and went badly is NEVER "missed"' in p
    assert "fumbled" in p


def test_the_failure_case_has_somewhere_to_go():
    """Tightening one bucket without naming the destination just moves the
    ambiguity. Failures land in drifted, and the note explains."""
    p = _prompt()
    assert 'all of that is "drifted"' in p
    assert 'the "note" says what went wrong' in p


def test_the_schema_block_carries_the_definitions_too():
    """The delta enum is where the model looks while filling the field, not
    the guidance paragraph twenty lines up."""
    p = _prompt()
    assert "missed = never came up at all" in p
    assert "INCLUDING a topic delivered poorly" in p
    assert "mixed = came up and held in part" in p


def test_the_enum_is_the_five_the_client_decodes():
    """Split after all (TR round 8, 2026-11-12): a topic that half landed
    came back DRIFTED beside its own praise. The client decodes `delta` as a
    plain String and reads `mixed` and `new` since round 8 (Section.isMixed,
    Section.isNew in TR's CompareRealityClient.swift), so the new values are
    not a decode risk. `unplanned` is retired for `new`, which the client
    renders the same way."""
    p = _prompt()
    assert '"landed" | "mixed" | "drifted" | "missed" | "new"' in p
    assert '"unplanned"' not in p
    for absent in ("fumbled\"", "\"failed\"", "\"botched\""):
        assert absent not in p
