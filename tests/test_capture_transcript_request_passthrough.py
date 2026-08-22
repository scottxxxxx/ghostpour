"""What SS SENDS to /v1/capture-transcript must be what CQ RECEIVES.

Rule 3 of the three-team protocol: a response-side test cannot see a
request-side hole. `to_name` was sent by SS and silently dropped by an
unmodelled field in our schema; SS saw a correct send, CQ saw a complete
request that simply lacked a name, and the hole lived only on this hop.

So this file drives the real route with the wire shape measured on the
2026-08-20 device test (origin fields top-level, speaker and language keys
inside `metadata`, project name and id alongside) and reads the body at
the httpx boundary to CQ's /v1/memory, the last place it is ours. The
reference is tests/test_cq_close_verbs_status_passthrough.py, pointed the
other way.
"""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services import context_quilt as cq

# A transcript that would expose the usual middlebox damage: multi-byte
# text, a very long line, CRLF, leading/trailing whitespace, a JSON-looking
# fragment, and a literal backslash.
TRANSCRIPT = (
    "  Speaker 1: Let's ship the Quiltline pilot by Friday.\r\n"
    "Speaker 2: José said the API étape is done; naïve estimate 3d.\n"
    "Speaker 1: " + "x" * 4000 + "\n"
    'Speaker 2: {"not": "json", "just": "talk"} and a backslash \\ here.\n'
    "Speaker 1: 了解しました。\n  "
)

# The measured shape. Keep it as one literal so a reviewer can diff it
# against usage_log raw_request rather than trusting the docstring.
SS_BODY = {
    "transcript": TRANSCRIPT,
    "origin_id": "D4E3C31E-71CF-47B7-B8C3-F802BB28FC22",
    "origin_type": "meeting",
    "project": "Quiltline Pilot",
    "project_id": "B568507A-994C-4DBB-B69C-006E8275FF6E",
    "metadata": {
        "user_identified": True,
        "user_label": "Scott",
        "identification_source": "voice",
        "language": "en-US",
        "transcript_source": "entity-repair-replay",
    },
}


@pytest.fixture
def cq_wire(monkeypatch):
    """Patch the httpx client capture() uses and hand back the POST mock."""
    monkeypatch.setattr(cq, "get_settings",
                        lambda: type("S", (), {"cq_base_url": "http://cq.test"})())
    post = AsyncMock()
    post.return_value = type("R", (), {"status_code": 200,
                                       "json": lambda self: {},
                                       "raise_for_status": lambda self: None})()
    http_client = type("C", (), {"post": post})()
    with patch.object(cq, "_get_client", lambda: http_client), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        yield post


def _wait_for_wire(post, timeout=3.0) -> dict:
    """The capture is fired with create_task after the 200 is returned, so
    give the loop a moment. A missing call is itself the finding."""
    deadline = time.monotonic() + timeout
    while not post.called and time.monotonic() < deadline:
        time.sleep(0.02)
    assert post.called, "no POST reached CQ: the transcript was accepted and never forwarded"
    return post.call_args.kwargs["json"]


@pytest.fixture
def sent(client, pro_user, cq_wire):
    resp = client.post("/v1/capture-transcript", json=SS_BODY,
                       headers={**pro_user["headers"],
                                "X-CZ-Recovery": "entity-repair-replay"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "queued"}
    return _wait_for_wire(cq_wire)


def test_the_transcript_reaches_cq_byte_for_byte(sent):
    """Not 'contains', not 'startswith', not stripped: equal. The device
    test joined three artifacts on sha256; this is that join in CI."""
    assert sent["content"] == TRANSCRIPT
    assert len(sent["content"]) == len(TRANSCRIPT)


def test_origin_and_project_ride_inside_metadata(sent):
    """Measured on the wire 2026-08-12: origin_id, origin_type and
    project_id are metadata keys, not top-level. CQ joins on origin_id;
    a top-level copy would be a key CQ never reads."""
    md = sent["metadata"]
    assert md["origin_id"] == SS_BODY["origin_id"]
    assert md["origin_type"] == SS_BODY["origin_type"]
    assert md["project"] == SS_BODY["project"]
    assert md["project_id"] == SS_BODY["project_id"]
    for k in ("origin_id", "origin_type", "project_id", "meeting_id"):
        assert k not in sent, f"{k!r} leaked to the top level of the CQ body"


def test_every_metadata_key_ss_sends_arrives_with_its_value_and_type(sent):
    """The to_name shape: each key SS puts in metadata must come out the
    other side unchanged, including the bool staying a bool. A key that is
    present on the way in and absent on the way out errors nowhere."""
    md = sent["metadata"]
    for k, v in SS_BODY["metadata"].items():
        assert k in md, f"SS sent metadata.{k} and CQ will not receive it"
        assert md[k] == v and type(md[k]) is type(v), (
            f"metadata.{k}: sent {v!r} ({type(v).__name__}), "
            f"forwarding {md[k]!r} ({type(md[k]).__name__})")


def test_the_interaction_type_is_meeting_transcript(sent):
    """CQ routes extraction on this; a transcript filed as a chat turn
    would be captured and never become a meeting."""
    assert sent["interaction_type"] == "meeting_transcript"


def test_who_the_user_is_travels_with_the_transcript(sent, pro_user):
    """Identity and tier ride the same body (CQ attributes the user's own
    commitments via display_name when no user_label is sent, observed on
    the device test). They must match the authenticated user, not the
    body, so a client cannot file a transcript under someone else."""
    assert sent["user_id"] == pro_user["user_id"]
    assert sent["metadata"]["subscription_tier"] == "pro"


def test_a_metadata_key_outside_the_allowlist_does_not_cross(client, pro_user, cq_wire):
    """The other direction of the same seam: the allowlist is the contract,
    so an invented key is dropped here and not discovered at CQ."""
    body = json.loads(json.dumps(SS_BODY))
    body["metadata"]["invented_by_a_future_build"] = {"deep": [1, None]}
    resp = client.post("/v1/capture-transcript", json=body, headers=pro_user["headers"])
    assert resp.status_code == 200
    md = _wait_for_wire(cq_wire)["metadata"]
    assert "invented_by_a_future_build" not in md
    # and dropping it cost nothing else
    assert md["user_label"] == "Scott"


# --- recording_started_at (2026-08-22) --------------------------------------
#
# Until this key existed the capture body carried NO timestamp of any kind.
# That is why no end of this lane has ever known when a meeting happened:
# CQ spends the ingest clock resolving relative deadlines and drops it, so
# every timestamp they serve is the clock of when their importer ran, and GP
# was never sending them anything better. SS's MeetingStore was the only
# place in the entire system holding a real one.
#
# This is a POST, so unlike the person-detail GET a request-side test here
# is the one rule 3 actually asks for: the field is dropped SILENTLY if it
# is not on the allowlist, SS would see a correct send, CQ would see a
# complete request that merely lacks a date, and the hole would live only on
# this hop. That is `to_name` exactly, and it is why SS is holding their
# build until this is confirmed rather than sending and hoping.
#
# Named for what it is: SS does not hold a meeting start, only when the
# RECORDING began. They sometimes hold a calendar event start, but only on a
# confident prep match, and folding that into this field would make one
# field mean two things depending on whether a match happened.

RECORDING_STARTED_AT = "2026-08-21T23:30:00-07:00"


@pytest.fixture
def sent_with_recording_start(client, pro_user, cq_wire):
    body = json.loads(json.dumps(SS_BODY))
    body["metadata"]["recording_started_at"] = RECORDING_STARTED_AT
    resp = client.post("/v1/capture-transcript", json=body,
                       headers=pro_user["headers"])
    assert resp.status_code == 200, resp.text
    return _wait_for_wire(cq_wire)


def test_recording_started_at_reaches_cq(sent_with_recording_start):
    """The allowlist is the gate. Before 2026-08-22 this key was not on it,
    so this exact send would have been eaten here with nothing to show for
    it at either end."""
    md = sent_with_recording_start["metadata"]
    assert "recording_started_at" in md, (
        "the allowlist ate it: SS sees a correct send, CQ sees a request "
        "that merely lacks a date, and nobody holds evidence of the hole")
    assert md["recording_started_at"] == RECORDING_STARTED_AT


def test_the_offset_is_carried_as_sent_and_not_normalised(sent_with_recording_start):
    """The whole value of the field. 23:30 at UTC-7 is 06:30 the NEXT day in
    UTC, so a helpful normalisation to Z names a different day while naming
    the same instant, and a day is the only thing this field exists to say.
    GP must forward the STRING, not parse it and re-emit it: asserted
    character for character, including the literal offset, because a
    round trip through a datetime is exactly how the offset gets lost."""
    value = sent_with_recording_start["metadata"]["recording_started_at"]
    assert value == RECORDING_STARTED_AT
    assert type(value) is str
    assert value.endswith("-07:00"), "the offset was rewritten"
    assert "Z" not in value and "+00:00" not in value, "normalised to UTC"
    assert value[:10] == "2026-08-21", "the DAY moved, which is the only thing that matters"


def test_it_is_absent_rather_than_guessed_when_the_client_sends_none(sent):
    """`sent` is the measured SS body, which does not carry the key. GP must
    not fill it in: a fabricated recording time is indistinguishable at CQ
    from a real one, and CQ's migration treats absent as unknown. Absent is
    a state we can fix later; a guess is one nobody can detect."""
    assert "recording_started_at" not in sent["metadata"]


def test_a_null_recording_start_does_not_travel_as_null(client, pro_user, cq_wire):
    """The allowlist drops None, so an explicit null arrives as absent
    rather than as a null CQ has to model. Pinned so the two states stay
    one state on this hop."""
    body = json.loads(json.dumps(SS_BODY))
    body["metadata"]["recording_started_at"] = None
    resp = client.post("/v1/capture-transcript", json=body,
                       headers=pro_user["headers"])
    assert resp.status_code == 200, resp.text
    assert "recording_started_at" not in _wait_for_wire(cq_wire)["metadata"]
