"""Placement resolution (2026-08-04).

Until now resolve ignored `placements` entirely: it returned the
highest-priority matching campaign for the app, whatever moment the client
was asking about. Harmless while "launch" was the only moment anyone
rendered, and actively wrong the moment a second one existed. SS built the
feature_locked placement and started sending `placement` on resolve, which
would have got them the launch card at a gate, and gate copy on the launch
ping with no gate around it.

The other half is `feature`. A gate campaign is written for a gate: copy
about Memory must not appear at the People gate. A placement entry may
name the feature it belongs to, and an entry that names none serves at any
gate, which is how you write "Plus unlocks this" and have it read
correctly wherever it lands.
"""

import pytest

SS = {"X-App-ID": "shouldersurf"}
ADMIN = {"X-Admin-Key": "test-admin-key"}
DEV = "22222222-2222-4222-8222-222222222222"


def _campaign(cid, placements, priority=0, label=None):
    return {
        "id": cid, "name": cid, "app_id": "shouldersurf", "status": "active",
        "priority": priority, "targeting": {}, "frequency": {},
        "placements": placements,
        "variants": [{
            "variant_id": "a", "weight": 100, "render": "native",
            "native": {"schema_version": 1, "title": label or cid,
                       "ctas": [{"label": label or cid, "cta_id": "primary",
                                 "action": {"type": "paywall"}}]},
        }],
    }


@pytest.fixture
def make(client):
    def _make(*campaigns):
        for c in campaigns:
            r = client.post("/webhooks/admin/campaigns", headers=ADMIN, json=c)
            assert r.status_code == 200, r.text
    return _make


def _resolve(client, **params):
    q = "&".join(f"{k}={v}" for k, v in {"device_id": DEV, **params}.items())
    r = client.get(f"/v1/promo/resolve?{q}", headers=SS)
    assert r.status_code == 200, r.text
    return r.json()


# --- the moment ------------------------------------------------------


def test_a_gate_does_not_serve_the_launch_card(client, make):
    make(_campaign("launch_card", [{"placement": "launch"}], priority=100))
    assert _resolve(client, placement="feature_locked", feature="context_quilt") == {}


def test_the_launch_ping_does_not_serve_gate_copy(client, make):
    """The mirror, and the worse of the two: gate copy on a launch card is
    an upgrade ask with no blocked feature behind it."""
    make(_campaign("gate_card", [{"placement": "feature_locked"}], priority=100))
    assert _resolve(client, placement="launch") == {}


def test_the_right_moment_serves(client, make):
    make(_campaign("gate_card", [{"placement": "feature_locked"}]))
    assert _resolve(client, placement="feature_locked",
                    feature="context_quilt")["campaign_id"] == "gate_card"


# --- which gate ------------------------------------------------------


def test_a_campaign_for_one_gate_stays_on_that_gate(client, make):
    make(_campaign("memory_card",
                   [{"placement": "feature_locked", "feature": "context_quilt"}]))
    assert _resolve(client, placement="feature_locked",
                    feature="context_quilt")["campaign_id"] == "memory_card"
    assert _resolve(client, placement="feature_locked", feature="people") == {}


def test_an_unqualified_gate_campaign_serves_every_gate(client, make):
    """Deliberate: generic copy like "Plus unlocks this" reads correctly at
    any gate, and requiring a feature per campaign would mean authoring one
    campaign per gate to say the same sentence."""
    make(_campaign("generic", [{"placement": "feature_locked"}]))
    for f in ("context_quilt", "people", "project_chat"):
        assert _resolve(client, placement="feature_locked",
                        feature=f)["campaign_id"] == "generic"


def test_a_feature_qualified_campaign_needs_a_feature_in_the_request(client, make):
    """The campaign was authored for one gate; with no feature on the
    request we cannot tell which gate is on screen, so we do not guess."""
    make(_campaign("memory_card",
                   [{"placement": "feature_locked", "feature": "context_quilt"}]))
    assert _resolve(client, placement="feature_locked") == {}


def test_the_specific_campaign_can_outrank_the_generic_one(client, make):
    """Per-placement priority is a statement about that moment, so it has to
    beat the campaign-wide number or a loud launch-priority card wins every
    gate it happens to also claim."""
    make(_campaign("generic", [{"placement": "feature_locked", "priority": 10}], priority=99),
         _campaign("memory", [{"placement": "feature_locked",
                               "feature": "context_quilt", "priority": 50}], priority=0))
    assert _resolve(client, placement="feature_locked",
                    feature="context_quilt")["campaign_id"] == "memory"
    assert _resolve(client, placement="feature_locked",
                    feature="people")["campaign_id"] == "generic"


# --- nothing already live changes ------------------------------------


def test_a_client_that_sends_no_placement_sees_the_old_behaviour(client, make):
    """Build 803 does not send placement and never will. It has to keep
    getting the launch card."""
    make(_campaign("launch_card", [{"placement": "launch"}]))
    assert _resolve(client)["campaign_id"] == "launch_card"


def test_a_campaign_with_no_placements_still_matches_anything(client, make):
    make(_campaign("everywhere", []))
    assert _resolve(client, placement="feature_locked",
                    feature="people")["campaign_id"] == "everywhere"
    assert _resolve(client, placement="launch")["campaign_id"] == "everywhere"


# --- authoring -------------------------------------------------------


def test_a_malformed_placement_is_rejected_at_authoring(client):
    """It used to be inert. Now it means the campaign never appears and
    nothing says why, which is the failure mode SS flagged: a dark campaign
    is indistinguishable from a flat test result."""
    bad = _campaign("bad", [{"feature": "context_quilt"}])
    r = client.post("/webhooks/admin/campaigns", headers=ADMIN, json=bad)
    assert r.status_code == 400
    assert "placement" in r.text
