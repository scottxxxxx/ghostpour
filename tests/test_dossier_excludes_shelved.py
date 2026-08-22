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


def test_the_block_never_mentions_the_shelved_item_at_all():
    """The model is the ONE consumer that must not be told.

    My first version disclosed the omission in the header, on the
    reasoning that a silent gap is the bug we keep shipping. CQ pushed
    back and was right: telling the model hands it something to say, and
    "1 shelved patch omitted, it must not be chased" is a fact plus an
    instruction about that fact. The failure it produces is the
    assistant announcing "there is one thing you set aside" to the user
    whose entire request was that it stop coming up. The disclosure
    meant to prevent a leak becomes the leak.

    Code that needs the number gets it out of band, where the model does
    not read. See test_the_shelved_count_still_reaches_code.
    """
    out = format_dossier(_data(meeting_patches=[
        _patch("p1", "One"),
        _patch("p2", "Two", shelved_at="2026-08-19T01:00:00Z"),
    ]))
    assert "shelved" not in out.lower()
    assert "omitted" not in out.lower()
    assert "chased" not in out.lower()
    assert "set aside" not in out.lower()


def test_the_header_claims_only_what_the_block_holds():
    """"complete stored memory" is the word that turned an omission into
    a lie. Once anything is filtered the block is not complete stored
    memory, so the claim was false and the footnote existed only to
    repair it. Describing the contents needs no footnote."""
    out = format_dossier(_data(meeting_patches=[
        _patch("p1", "One"),
        _patch("p2", "Two", shelved_at="x"),
    ]))
    assert out.startswith("[PROJECT MEMORY DOSSIER: 1 patches across 1 meetings]")
    assert "complete" not in out.lower(), (
        "a completeness claim is exactly what makes any future filter a lie")


def test_the_shelved_count_still_reaches_code(caplog):
    """Out of band, so the counting artifact can be told what the model
    must not be."""
    import logging
    with caplog.at_level(logging.INFO):
        format_dossier(_data(meeting_patches=[
            _patch("p1", "One"),
            _patch("p2", "Two", shelved_at="x"),
            _patch("p3", "Three", shelved_at="x"),
        ]))
    assert any("cq_dossier_shelved_omitted" in r.getMessage() and "count=2" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_nothing_is_logged_when_nothing_was_shelved(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        format_dossier(_data(meeting_patches=[_patch("p1", "One")]))
    assert not any("cq_dossier_shelved_omitted" in r.getMessage()
                   for r in caplog.records)


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
    assert "shelved" not in out.lower()


def test_a_meeting_whose_patches_are_all_shelved_gets_no_heading():
    out = format_dossier({"meetings": [
        {"patches": [_patch("p1", "Set aside", shelved_at="x")]},
        {"patches": [_patch("p2", "Still owed")]},
    ]})
    assert "Still owed" in out
    assert "## Meeting 1 of 2" not in out, (
        "an empty meeting heading in a prompt reads as missing data")
    assert "## Meeting 2 of 2" in out


# --- The heading stamp is an ingest clock, and says so (2026-08-22) --------
#
# CQ flagged the class and it turned out to apply here: CQ never persists a
# meeting date. One arrives at ingest, is spent resolving relative
# deadlines, and is dropped, so every timestamp CQ serves is the clock of
# when the importer ran. GP cannot supply one either: the capture body we
# POST to /v1/memory has no timestamp field at all.
#
# The heading used to render a bare `(2026-08-11)` next to "## Meeting 1 of
# 5", which in a prompt reads as the day the meeting happened. Live capture
# runs minutes after a meeting, so it was usually right BY ACCIDENT; on a
# backfill every meeting collapses onto one date and the model narrates a
# week that never happened, confidently and undetectably.

def _one_meeting(created_at="2026-08-11T10:00:00Z"):
    return {"meetings": [{"origin_id": "m1", "patches": [
        {"patch_id": "p1", "content": "Ada owns the migration",
         "type": "fact", "created_at": created_at}]}]}


def test_heading_does_not_present_the_ingest_clock_as_a_meeting_date():
    out = format_dossier(_one_meeting())
    assert "## Meeting 1 of 1 (added to memory on 2026-08-11)" in out
    # The bare parenthesised date is the exact string that read as a
    # meeting date. It must not come back, in this or any future edit.
    assert "## Meeting 1 of 1 (2026-08-11)" not in out


def test_heading_omits_the_stamp_entirely_when_cq_sends_none():
    """No timestamp is better than a manufactured one: the heading simply
    loses its parenthetical rather than falling back to today."""
    out = format_dossier(_one_meeting(created_at=None))
    assert "## Meeting 1 of 1" in out
    assert "added to memory" not in out
    assert "(" not in out.split("## Meeting 1 of 1")[1].split("\n")[0]


def test_the_stamp_still_reaches_the_model():
    """The fix is honesty about what the number is, not deleting it. The
    recency signal is worth having and it survives."""
    out = format_dossier(_one_meeting())
    assert "2026-08-11" in out
