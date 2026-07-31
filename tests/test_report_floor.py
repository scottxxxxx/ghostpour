"""The report floor is enforced server-side, from the number we serve.

Scott, 2026-07-31: "enforce the floor server-side too but be sure we are
reading our own setting so if we change it in the dashboard it changes our
own enforcement." So these tests care about two things equally: that a
too-short request is refused, and that the refusal follows the CONFIG rather
than a constant, because the whole reason this moved off the device was that
a compiled-in threshold could not be tuned.

Calibration for the 30 second default, from the fleet on the day it shipped:
106 completed meetings, the three shortest 9, 10 and 11 seconds, and zero of
53 reports ever generated would have been refused by it (shortest was 135s).
"""

from __future__ import annotations

import pytest

from app.services.post_session import post_session_policy, report_floor_seconds

SS = "shouldersurf"
TR = "techrehearsal"


def _configs(**post_session):
    return {"client-config": {"version": 1, "post_session": dict(post_session)}}


# --- reading our own setting --------------------------------------------

def test_floor_comes_from_the_served_config():
    assert report_floor_seconds(_configs(request_min_seconds=30), SS) == 30


def test_changing_the_config_changes_enforcement():
    """The dashboard writes the config and hot-reloads it into app state, so
    the next request must see the new number with no deploy."""
    cfgs = _configs(request_min_seconds=30)
    assert report_floor_seconds(cfgs, SS) == 30
    cfgs["client-config"]["post_session"]["request_min_seconds"] = 90
    assert report_floor_seconds(cfgs, SS) == 90


def test_zero_disables_enforcement():
    assert report_floor_seconds(_configs(request_min_seconds=0), SS) == 0


def test_a_malformed_value_is_not_an_outage():
    # A bad edit must serve the report, not refuse every request.
    assert report_floor_seconds(_configs(request_min_seconds="banana"), SS) == 0
    assert report_floor_seconds(_configs(request_min_seconds=None), SS) == 0
    assert report_floor_seconds(_configs(request_min_seconds=-5), SS) == 0


def test_missing_config_falls_back_to_the_default_policy():
    assert post_session_policy({}, SS)["report_min_seconds"] == 300
    assert post_session_policy({}, SS)["request_min_seconds"] == 30


def test_policy_merges_partial_config_over_defaults():
    p = post_session_policy(_configs(report_min_seconds=120), SS)
    assert p["report_min_seconds"] == 120          # from config
    assert p["allow_request_below_minimum"] is True  # from defaults


# --- app segregation ----------------------------------------------------

def test_another_app_does_not_inherit_our_floor():
    """TR reads the flat client-config for everything else, but a rejection
    is not something to inherit: the number was measured on SS meetings."""
    assert report_floor_seconds(_configs(request_min_seconds=30), TR) == 0


def test_another_app_gets_a_floor_when_it_declares_one():
    cfgs = _configs(request_min_seconds=30)
    cfgs["techrehearsal/client-config"] = {
        "version": 1, "post_session": {"request_min_seconds": 45}}
    assert report_floor_seconds(cfgs, TR) == 45
    assert report_floor_seconds(cfgs, SS) == 30     # unchanged


def test_unknown_app_id_is_treated_as_the_default_app():
    # resolve_app_dir fails open, and so must we
    assert report_floor_seconds(_configs(request_min_seconds=30), None) == 30
    assert report_floor_seconds(_configs(request_min_seconds=30), "unknown") == 30


# --- the route ----------------------------------------------------------

@pytest.mark.parametrize("duration,expect_block", [
    (9, True),      # the shortest session in the fleet
    (29, True),
    (30, False),    # the floor itself is allowed
    (298, False),   # the meeting that started all of this
    (419, False),
])
def test_route_refuses_only_below_the_floor(client, pro_user, duration,
                                            expect_block):
    r = client.post(
        "/v1/meetings/floor-test-meeting/report",
        json={"duration_seconds": duration},
        headers={**pro_user["headers"], "X-App-ID": SS},
    )
    if expect_block:
        assert r.status_code == 400
        body = r.json()["detail"]
        assert body["code"] == "meeting_too_short"
        assert body["details"]["minimum_seconds"] == 30
        assert body["details"]["duration_seconds"] == duration
    else:
        # anything but the floor rejection; the meeting has no transcript in
        # this fixture, so 404 no_meeting_data is the expected next stop
        assert r.status_code != 400 or \
            r.json()["detail"].get("code") != "meeting_too_short"
