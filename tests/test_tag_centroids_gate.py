"""tag_centroids remote kill switch (SS client diarization path).

GP publishes the per-tier state string in the features map returned by
/v1/usage/me; the client acts on it. Inverted polarity: "disabled" kills
the path, anything else (incl. "enabled"/absent) is fail-open. Default is
"enabled" for every tier so shipping nothing preserves today's behavior.
"""

from app.models.tier import load_tier_config


def test_all_tiers_default_enabled():
    tc = load_tier_config("config/tiers.yml")
    assert tc.tiers, "no tiers loaded"
    for name, tier in tc.tiers.items():
        assert tier.features.get("tag_centroids") == "enabled", (
            f"{name} should default tag_centroids=enabled (fail-open kill switch)"
        )


def test_tag_centroids_surfaces_in_usage_me(client, free_user):
    r = client.get("/v1/usage/me", headers=free_user["headers"])
    assert r.status_code == 200
    assert r.json()["features"]["tag_centroids"] == "enabled"


def test_tag_centroids_surfaces_for_pro(client, pro_user):
    r = client.get("/v1/usage/me", headers=pro_user["headers"])
    assert r.status_code == 200
    assert r.json()["features"]["tag_centroids"] == "enabled"
