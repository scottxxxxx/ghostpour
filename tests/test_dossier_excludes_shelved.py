"""A shelved patch must not be read back to the user as an open obligation.

CQ's `shelve` deliberately leaves the patch ACTIVE: it keeps flowing
through /v1/quilt and their recall still finds it, so the assistant can
still answer a question ABOUT it ("did Vijay ever owe me the hardware
POC?"). That is the right call for their storage and the wrong one for
this block, which is a list of what is still owed.

The user-visible failure it prevents: someone taps "Let it go", and the
assistant keeps chasing the item in the next conversation. They do not
experience a distinction between a to-do list and a memory. They
experience the button not working.

Scope, stated so nobody reads this as more than it is: this covers the
dossier injection block only. CQ's recall path is a separate query that
intentionally still surfaces shelved items, and it is not fixed here.

Wire shape verified 2026-08-19 against prod rather than read off a
design note: `shelved_at` is a TOP-LEVEL key on every patch row CQ
returns (86 of 86 on a real account). Prod has zero shelved patches
today, so nothing in ordinary traffic exercises this yet, which is
exactly why it needs a test rather than an observation.
"""

from app.services.context_quilt import format_dossier


def _patch(pid, fact, shelved_at=None):
    return {"patch_id": pid, "fact": fact, "patch_type": "action_item",
            "created_at": "2026-08-18T19:19:56.077482+00:00",
            "shelved_at": shelved_at, "shelved_source": None}


def _data(meeting_patches=(), facts=(), action_items=()):
    return {
        "meetings": [{"patches": list(meeting_patches)}] if meeting_patches else [],
        "facts": list(facts),
        "action_items": list(action_items),
    }


def test_a_shelved_patch_is_not_rendered():
    out = format_dossier(_data(meeting_patches=[
        _patch("live", "Send the SOW to the customer"),
        _patch("gone", "Write the hardware POC", shelved_at="2026-08-19T01:00:00Z"),
    ]))
    assert "Send the SOW to the customer" in out
    assert "Write the hardware POC" not in out, (
        "the user set this aside and the assistant is about to chase it")


def test_it_is_excluded_from_the_flat_sections_too():
    """action_items and facts are a separate render path from meetings,
    so a filter applied to one and not the other looks fixed and is not."""
    out = format_dossier(_data(
        action_items=[_patch("a1", "Book the venue", shelved_at="2026-08-19T01:00:00Z")],
        facts=[_patch("f1", "Robin owns the renewal")],
    ))
    assert "Robin owns the renewal" in out
    assert "Book the venue" not in out


def test_the_count_in_the_header_excludes_shelved():
    """The header is a denominator, and the counting artifact reads it.
    A count that silently includes rows the block does not show is the
    same wrong-number failure this lane keeps producing."""
    out = format_dossier(_data(meeting_patches=[
        _patch("p1", "One"),
        _patch("p2", "Two", shelved_at="2026-08-19T01:00:00Z"),
        _patch("p3", "Three"),
    ]))
    assert "2 patches" in out, out.splitlines()[0]


def test_the_omission_is_disclosed_rather_than_silent():
    """Dropping rows without saying so turns a shelved item into a gap
    nobody can see. Say what was dropped and why."""
    out = format_dossier(_data(meeting_patches=[
        _patch("p1", "One"),
        _patch("p2", "Two", shelved_at="2026-08-19T01:00:00Z"),
    ]))
    assert "1 shelved patch omitted" in out
    assert "must not be chased" in out


def test_plural_and_singular_both_read_correctly():
    two = format_dossier(_data(meeting_patches=[
        _patch("p1", "One"),
        _patch("p2", "Two", shelved_at="x"),
        _patch("p3", "Three", shelved_at="x"),
    ]))
    assert "2 shelved patches omitted" in two
    assert "they are not an open obligation" in two
    one = format_dossier(_data(meeting_patches=[
        _patch("p1", "One"), _patch("p2", "Two", shelved_at="x")]))
    assert "1 shelved patch omitted" in one
    assert "it is not an open obligation" in one


def test_an_unshelved_patch_is_untouched_by_any_of_this():
    """`shelved_at` is null on every row in prod today. If the predicate
    were wrong in the other direction the block would go empty for
    everyone, which is a worse failure than the one being fixed."""
    out = format_dossier(_data(
        meeting_patches=[_patch("p1", "Still owed"), _patch("p2", "Also owed")],
        facts=[_patch("f1", "A fact")],
    ))
    for text in ("Still owed", "Also owed", "A fact"):
        assert text in out
    assert "3 patches" in out
    assert "shelved" not in out, (
        "no shelved rows means no shelved line; an always-on notice "
        "would train the reader to ignore it")


def test_a_meeting_whose_patches_are_all_shelved_gets_no_heading():
    out = format_dossier({"meetings": [
        {"patches": [_patch("p1", "Set aside", shelved_at="x")]},
        {"patches": [_patch("p2", "Still owed")]},
    ]})
    assert "Still owed" in out
    assert "## Meeting 1 of 2" not in out, (
        "an empty meeting heading in a prompt reads as missing data")
    assert "## Meeting 2 of 2" in out
