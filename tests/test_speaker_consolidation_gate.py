"""speaker_consolidation remote kill switch (SS post-session consolidation +
contamination-demotion path).

GP publishes the per-tier state string in the features map returned by
/v1/usage/me; the client acts on it. Inverted polarity: "disabled" kills
the path, anything else (incl. "enabled"/absent) is fail-open. Default is
"enabled" for every tier so shipping nothing preserves today's behavior.
"""

import json

from app.models.tier import load_tier_config


def test_all_tiers_default_enabled():
    # Phase 2: the matrix bundle is the single home for feature states
    matrix = json.load(open("config/remote/entitlements.json"))["matrix"]
    tc = load_tier_config("config/tiers.yml")
    assert tc.tiers, "no tiers loaded"
    for name in tc.tiers:
        assert matrix["speaker_consolidation"].get(name) == "enabled", (
            f"{name} should default speaker_consolidation=enabled (fail-open kill switch)"
        )


def test_speaker_consolidation_surfaces_in_usage_me(client, free_user):
    r = client.get("/v1/usage/me", headers=free_user["headers"])
    assert r.status_code == 200
    assert r.json()["features"]["speaker_consolidation"] == "enabled"


def test_speaker_consolidation_surfaces_for_pro(client, pro_user):
    r = client.get("/v1/usage/me", headers=pro_user["headers"])
    assert r.status_code == 200
    assert r.json()["features"]["speaker_consolidation"] == "enabled"
