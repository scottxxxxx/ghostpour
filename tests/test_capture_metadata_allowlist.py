"""Capture metadata forwarding (2026-08-04).

CQ traced why a new metadata key silently never arrived. `capture()` had a
closed parameter list and rebuilt its outbound metadata through thirteen
hand-written conditionals, while the route enumerated the same fields
again via get_meta. So a new key cost THREE edits across two files, and
missing any one produced a key that pydantic accepted, that sat in the
request object, and that nothing ever read. `language` is the precedent:
it had to be threaded through all three.

That is worse than an allowlist, because an allowlist is at least one list
somebody can audit. Capture now uses the same extension point recall does.

The rule CQ drew from this, and from the tier-catalog entry and the
envelope before it: **presence in a payload is not evidence of
consumption.** A field is integrated only when every hop that must read it
is shown to read it. These tests assert the outbound body, not the inbound
request, because the inbound side was never the broken half.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services import context_quilt as cq


def _sent_body(mock) -> dict:
    """The JSON we actually put on the wire to CQ."""
    assert mock.called, "no request was made to CQ"
    return mock.call_args.kwargs["json"]


@pytest.fixture
def cq_post(monkeypatch):
    monkeypatch.setattr(cq, "get_settings",
                        lambda: type("S", (), {"cq_base_url": "http://cq.test"})())
    post = AsyncMock()
    post.return_value = type("R", (), {"status_code": 200,
                                       "json": lambda self: {},
                                       "raise_for_status": lambda self: None})()
    client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        yield post


def test_an_allowlisted_key_reaches_the_outbound_body(cq_post):
    """The failure this replaces: accepted on the way in, absent on the way
    out, with no error anywhere."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"user_label": "Scott", "identification_source": "voice"}))
    md = _sent_body(cq_post)["metadata"]
    assert md["user_label"] == "Scott"
    assert md["identification_source"] == "voice"


def test_a_key_outside_the_allowlist_is_dropped(cq_post):
    """The boundary is still a boundary. Forwarding blindly was never the
    ask; one auditable list was."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"user_label": "Scott", "not_a_real_key": "x"}))
    md = _sent_body(cq_post)["metadata"]
    assert "not_a_real_key" not in md
    assert md["user_label"] == "Scott"


def test_a_client_cannot_spoof_server_derived_identity(cq_post):
    """subscription_tier, email and display_name come from the user record.
    If they were allowlisted, a request body could claim any tier."""
    for field in ("subscription_tier", "email", "display_name"):
        assert field not in cq.CAPTURE_METADATA_ALLOWLIST, field
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        subscription_tier="free",
        passthrough={"subscription_tier": "pro", "email": "attacker@x.com"}))
    md = _sent_body(cq_post)["metadata"]
    assert md["subscription_tier"] == "free"
    assert "email" not in md


def test_explicit_parameters_still_win_over_passthrough(cq_post):
    """`language` keeps an explicit parameter because the route supplies a
    server-side fallback from Accept-Language when the client sends none.
    The allowlist forwards what the client sent; the parameter supplies what
    it did not."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        language="es-US", passthrough={"language": "en"}))
    assert _sent_body(cq_post)["metadata"]["language"] == "es-US"


def test_no_passthrough_behaves_exactly_as_before(cq_post):
    """Every other caller passes named parameters only. Adoption must be a
    no-op for them."""
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="chat", content="hi",
        origin_id="m-1", origin_type="meeting", project="P",
        subscription_tier="pro"))
    md = _sent_body(cq_post)["metadata"]
    assert md == {"origin_id": "m-1", "origin_type": "meeting",
                  "project": "P", "subscription_tier": "pro"}


def test_none_values_are_not_forwarded_as_nulls(cq_post):
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        passthrough={"user_label": None, "language": "en"}))
    md = _sent_body(cq_post)["metadata"]
    assert "user_label" not in md
    assert md["language"] == "en"


class TestOriginIdSurvivesTheOutboundBody:
    """CQ's dedup, their item-ledger same-origin restatement guard and
    their freshness fix ALL key on `origin_id`, and all three fail
    SILENTLY without it: no error, just a phantom meeting per ingest,
    inflated meeting counts and wrong "last met" dates.

    Verified on the wire 2026-08-17 by replaying one real meeting: the
    id arrives byte identical and CQ merged it into the existing
    meeting rather than minting a new one. But it travels INSIDE
    `metadata`, which means it is carried by an allowlist entry rather
    than by the schema.

    CQ's own rule, from the two months their entity extraction sat
    broken: **when a contract has exactly one carrier, its
    disappearance is silent by construction.** An allowlist entry is
    exactly one carrier. So this pins the carrier.
    """

    def test_origin_id_and_type_reach_the_wire(self, cq_post):
        asyncio.run(cq.capture(
            user_id="u1", interaction_type="meeting_transcript",
            content="hi", origin_id="MTG-1", origin_type="meeting"))
        md = _sent_body(cq_post)["metadata"]
        assert md["origin_id"] == "MTG-1", (
            "origin_id left the building; every ingest now mints a "
            "phantom meeting and nothing errors")
        assert md["origin_type"] == "meeting"

    def test_the_deprecated_alias_still_arrives_as_origin_id(self, cq_post):
        """Callers on the old arg must not silently lose the field."""
        asyncio.run(cq.capture(
            user_id="u1", interaction_type="meeting_transcript",
            content="hi", meeting_id="MTG-2"))
        md = _sent_body(cq_post)["metadata"]
        assert md["origin_id"] == "MTG-2"
        assert md["origin_type"] == "meeting"

    def test_it_is_nested_in_metadata_not_at_the_root(self, cq_post):
        """Pins WHERE it sits, because both sides agreed on nested and a
        well-meaning move to the root would break their read sites."""
        asyncio.run(cq.capture(
            user_id="u1", interaction_type="meeting_transcript",
            content="hi", origin_id="MTG-3", origin_type="meeting"))
        body = _sent_body(cq_post)
        assert "origin_id" not in body, (
            "origin_id moved to the root; CQ reads metadata.origin_id at "
            "every site and would see nothing")
        assert body["metadata"]["origin_id"] == "MTG-3"
