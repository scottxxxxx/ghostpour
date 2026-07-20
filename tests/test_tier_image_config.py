"""Per-tier image send config (2026-07-20): GP dictates the downscale
long-edge and JPEG quality SS applies on the chat/generation send path,
per subscription tier, in tiers.json feature_definitions.images. Served
on the tiers payload and runtime-editable via the tunable endpoint
(including the float jpeg_quality)."""

import json

from app.services.document_generation import tier_feature_block

ADMIN = {"X-Admin-Key": "test-admin-key"}


def test_tier_feature_block_reads_images():
    cfgs = {"tiers": {"tiers": {
        "pro": {"feature_definitions": {"images": {"max_long_edge": 1568, "jpeg_quality": 0.8}}},
        "free": {"feature_definitions": {}},
    }}}
    assert tier_feature_block(cfgs, "pro", "images") == {"max_long_edge": 1568, "jpeg_quality": 0.8}
    assert tier_feature_block(cfgs, "free", "images") is None
    assert tier_feature_block({}, "pro", "images") is None


def test_tiers_payload_surfaces_images(client):
    tiers = client.get("/webhooks/admin/tiers", headers=ADMIN).json()["tiers"]
    pro = tiers["pro"]
    assert pro["images"]["max_long_edge"] == 1568
    assert pro["images"]["jpeg_quality"] == 0.8


def test_capture_guidance_served_for_every_tier(client):
    # Capture guidance rides the same images block SS already reads; the
    # client renders it as a pre-capture hint. Served for all tiers, and
    # dash-free per the served-copy rule (the model copies punctuation it
    # sees, and these strings are user-facing).
    tiers = client.get("/webhooks/admin/tiers", headers=ADMIN).json()["tiers"]
    for name in ("free", "plus", "pro"):
        guide = tiers[name]["images"]["capture_guidance"]
        assert guide["title"]
        assert len(guide["tips"]) >= 3
        blob = guide["title"] + " ".join(guide["tips"])
        assert "—" not in blob and "–" not in blob  # no em/en dashes


def test_tunable_endpoint_edits_image_config_including_float(client):
    # int field
    r = client.put("/webhooks/admin/tunable/tier-field",
                   json={"tier": "pro", "feature": "images",
                         "field": "max_long_edge", "value": 2000},
                   headers=ADMIN)
    assert r.status_code == 200
    assert tier_feature_block(client.app.state.remote_configs, "pro", "images")["max_long_edge"] == 2000
    # float field (jpeg_quality) must survive the endpoint's value type
    r2 = client.put("/webhooks/admin/tunable/tier-field",
                    json={"tier": "pro", "feature": "images",
                          "field": "jpeg_quality", "value": 0.9},
                    headers=ADMIN)
    assert r2.status_code == 200
    assert tier_feature_block(client.app.state.remote_configs, "pro", "images")["jpeg_quality"] == 0.9
    # Restore: the tunable endpoint persists to the overlay beside the DB,
    # so leaving the mutation in place poisons sibling tests that assert the
    # bundle defaults (the overlay shadows the bundle). Put it back.
    for field, val in (("max_long_edge", 1568), ("jpeg_quality", 0.8)):
        client.put("/webhooks/admin/tunable/tier-field",
                   json={"tier": "pro", "feature": "images",
                         "field": field, "value": val}, headers=ADMIN)
