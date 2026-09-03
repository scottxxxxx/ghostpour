"""`material_kind` crosses GP's capture hop with its EXACT value (2026-09-02).

CQ doc 22 option C (their prod 2122234) lets a recording be captured as
`listening` rather than `meeting`, which stops a podcast seeding the People
roster. GP is the only gate: `/v1/capture-transcript` hand-enumerates its
arguments into `cq.capture()` and its metadata passes only keys in
CAPTURE_METADATA_ALLOWLIST, so before this the flag was accepted by pydantic
and read by nothing.

WHY THESE ASSERT THE VALUE AND NOT THE KEY. CQ resolves ABSENT and
UNRECOGNISED to `meeting` alike, deliberately, so a client sending a kind
they do not know does not lose its meeting. That means a misspelling is
silently a meeting and is INDISTINGUISHABLE, from either end and from the
outcome, from the flag never arriving. A test that asserts only presence
would pass while GP lowercased, trimmed, defaulted or substituted the
value, and every one of those looks like success downstream. So the
property under test is byte-identity of the value across this hop.

These assert the OUTBOUND body, like the rest of the capture suite,
because the inbound side was never the broken half.
"""

import asyncio

import pytest

from app.services import context_quilt as cq
from tests.test_capture_metadata_allowlist import cq_post, _sent_body  # noqa: F401


def test_material_kind_reaches_cq_with_its_exact_value(cq_post):  # noqa: F811
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"material_kind": "listening"}))
    md = _sent_body(cq_post).get("metadata", {})
    assert "material_kind" in md, "the key was dropped by the allowlist"
    assert md["material_kind"] == "listening", (
        f"value mutated on our hop: {md['material_kind']!r}. CQ reads an "
        "unrecognised value as `meeting`, so this is silent.")


def test_meeting_is_carried_explicitly_and_not_swallowed_as_a_default(cq_post):  # noqa: F811
    """`meeting` is CQ's default, so a hop that dropped it would look
    correct forever. It still has to cross."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"material_kind": "meeting"}))
    assert _sent_body(cq_post).get("metadata", {}).get("material_kind") == "meeting"


def test_gp_does_not_normalise_the_value(cq_post):  # noqa: F811
    """CQ owns the vocabulary. GP trimming or lowercasing here would be a
    second place to update and a new way to drop a kind they add later.
    A misspelling must arrive AS SENT so CQ's own rule decides it, and so
    the wire shows the difference between a typo and a dropped field."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"material_kind": "  Listenting  "}))
    assert _sent_body(cq_post).get("metadata", {}).get("material_kind") == "  Listenting  "


def test_absent_material_kind_sends_no_key_at_all(cq_post):  # noqa: F811
    """Absent must not become the string "meeting" on our side. CQ's
    default is CQ's to apply; GP inventing it would hide a client that
    never sent one."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"user_label": "Scott"}))
    assert "material_kind" not in _sent_body(cq_post).get("metadata", {})
