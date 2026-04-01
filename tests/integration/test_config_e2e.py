"""End-to-end integration tests for remote config endpoints."""


class TestRemoteConfig:
    def test_config_returns_payload(self, client):
        """GET /v1/config/{slug} returns full JSON with version."""
        resp = client.get("/v1/config/idle-tips")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert data["version"] >= 1

    def test_config_version_not_changed(self, client):
        """Client sends matching version → {"changed": false}."""
        # First get the current version
        resp1 = client.get("/v1/config/idle-tips")
        version = resp1.json()["version"]

        # Send version header
        resp2 = client.get(
            "/v1/config/idle-tips",
            headers={"X-Config-Version": str(version)},
        )
        assert resp2.status_code == 200
        assert resp2.json()["changed"] is False

    def test_config_unknown_slug_returns_404(self, client):
        """Unknown config slug → 404."""
        resp = client.get("/v1/config/nonexistent-config")
        assert resp.status_code == 404

    def test_config_locale_spanish(self, client):
        """Accept-Language: es → returns Spanish variant if available."""
        resp = client.get(
            "/v1/config/protected-prompts",
            headers={"Accept-Language": "es"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-config-locale") == "es"

    def test_config_locale_fallback_to_base(self, client):
        """Accept-Language for unavailable locale → falls back to base config."""
        # Get base version for comparison
        base_resp = client.get("/v1/config/idle-tips")
        base_version = base_resp.json()["version"]

        # Request French (no variant exists)
        resp = client.get(
            "/v1/config/idle-tips",
            headers={"Accept-Language": "fr"},
        )
        assert resp.status_code == 200
        # Falls back to base config, same version
        assert resp.json()["version"] == base_version
