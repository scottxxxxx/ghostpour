"""End-to-end pin: the /v1/tiers response contract iOS depends on.

iOS's TierCatalog reads `tiers[slug].feature_definitions.project_chat.max_input_tokens`
out of this endpoint. The values come from the live tiers.json (dashboard-editable),
not from the bundled tiers.yml — but the *wire path* must always be present
even when no dashboard edit has been made.
"""

from __future__ import annotations


def test_tiers_endpoint_passes_through_feature_definitions(client):
    resp = client.get("/v1/tiers")
    assert resp.status_code == 200
    body = resp.json()

    for tier in ("free", "plus", "pro"):
        assert tier in body["tiers"], f"{tier} missing from response"
        fd = body["tiers"][tier].get("feature_definitions")
        assert isinstance(fd, dict), f"{tier}.feature_definitions missing or not a dict"
        pc = fd.get("project_chat", {})
        assert "max_input_tokens" in pc, (
            f"tiers.{tier}.feature_definitions.project_chat.max_input_tokens "
            f"missing from /v1/tiers — iOS fuel gauge will fall back to a default "
            f"and disagree with server enforcement"
        )


def test_tiers_endpoint_stamps_top_level_version(client):
    resp = client.get("/v1/tiers")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body, (
        "/v1/tiers should include a top-level `version` field so iOS can tell "
        "at a glance which tiers.json payload it's looking at"
    )
    assert isinstance(body["version"], int)


def test_tiers_endpoint_caps_match_bundled_source(client):
    """Defense-in-depth: when no dashboard edit has been made, the caps that
    flow through to iOS via /v1/tiers should match what's in the bundled
    tiers.json source — i.e., 50K/150K/180K for free/plus/pro."""
    resp = client.get("/v1/tiers")
    body = resp.json()
    expected = {"free": 50_000, "plus": 150_000, "pro": 180_000}
    for tier, expected_cap in expected.items():
        actual = (
            body["tiers"][tier]
            .get("feature_definitions", {})
            .get("project_chat", {})
            .get("max_input_tokens")
        )
        assert actual == expected_cap, (
            f"/v1/tiers tiers.{tier}.feature_definitions.project_chat.max_input_tokens "
            f"= {actual!r}, expected {expected_cap}"
        )


def test_tiers_endpoint_localized_response_includes_feature_definitions(client):
    """es/ja locale responses also need to surface feature_definitions, since
    a Japanese-locale user hits /v1/tiers with Accept-Language: ja and gets
    the localized variant — but max_input_tokens must still come through."""
    for lang in ("es", "ja"):
        resp = client.get("/v1/tiers", headers={"Accept-Language": f"{lang}-XX,{lang};q=0.9"})
        assert resp.status_code == 200, f"locale={lang} failed: {resp.text}"
        body = resp.json()
        for tier in ("free", "plus", "pro"):
            fd = body["tiers"][tier].get("feature_definitions")
            assert isinstance(fd, dict), (
                f"locale={lang}: tiers.{tier}.feature_definitions missing"
            )
            assert "max_input_tokens" in fd.get("project_chat", {}), (
                f"locale={lang}: tiers.{tier}.feature_definitions.project_chat."
                f"max_input_tokens missing"
            )
