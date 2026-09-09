"""`X-CZ-Recovery` crosses GP's capture hop (2026-09-09).

The SS client sets this header on a capture that is a REPLAY of one that
never landed (pending-ingest sweep on next foreground, report-404 replay).
GP read it, logged it, and dropped it: the outbound request to CQ carried
auth headers only. CQ checked their source and logs and found the header
nowhere, so a replayed ingest was indistinguishable from a fresh duplicate.

This is the `to_name` shape exactly: additive at the sender, invisible at the
reader, dead on the middle hop, and neither endpoint can hold evidence of
it. Only a request-side test at THIS hop can. Two links, tested separately
because the route fires capture() as a background task:

  route  -> capture()   the header value is passed as `recovery_source`
  capture() -> CQ       `recovery_source` becomes the outbound header

Present in means present out; absent in means absent out. The second half
matters as much as the first: a header GP invented on a first-send capture
would make CQ count every ingest as a replay.
"""

import asyncio

import pytest

from app.services import context_quilt as cq
from tests.test_capture_metadata_allowlist import cq_post  # noqa: F401


def _sent_headers(mock) -> dict:
    assert mock.called, "no request was made to CQ"
    return mock.call_args.kwargs["headers"]


def test_capture_puts_the_recovery_source_on_the_outbound_header(cq_post):  # noqa: F811
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi",
        recovery_source="pending-ingest-sweep"))
    assert _sent_headers(cq_post)["X-CZ-Recovery"] == "pending-ingest-sweep"


def test_capture_sends_no_recovery_header_on_a_first_send(cq_post):  # noqa: F811
    asyncio.run(cq.capture(
        user_id="u1", interaction_type="meeting_transcript", content="hi"))
    assert "X-CZ-Recovery" not in _sent_headers(cq_post)


class TestRouteHop:
    def test_the_inbound_header_reaches_capture(self, client_with_cq, pro_user, mock_cq):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-recovery-1"},
            headers={**pro_user["headers"], "X-CZ-Recovery": "report-404-replay"},
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["recovery_source"] == "report-404-replay"

    def test_no_inbound_header_means_none_at_capture(self, client_with_cq, pro_user, mock_cq):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-recovery-2"},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs.get("recovery_source") is None
