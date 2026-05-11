"""Integration tests for the Project Chat policy endpoint.

Post-Slice 5: the count-quota fields (`quota_remaining`, `quota_total`,
`quota_resets_at`, `send_to_gp_with_cta`, `quota_exhausted`/`quota_remaining`
CTA kinds) are gone. Free-tier blocking is the budget gate's job — the
preflight here is purely a routing decision.
"""

from tests.conftest import _insert_user, _jwt_token


PREFLIGHT_PATH = "/v1/features/project-chat/check"


class TestProjectChatPreflight:
    def test_unauthenticated_returns_login_required(self, client):
        """No JWT + external selected → verdict=login_required."""
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "login_required"
        assert body["cta"]["kind"] == "login_required"

    def test_plus_user_external_routes_to_user_model_under_ssai_free_only(self, client, tmp_db_path):
        """Plus user with external selected → send_to_user_model under ssai_free_only default.

        ssai_free_only applies ssai semantics to Free tier and logged_in
        semantics to paid tiers — paying users keep their BYOK choice.
        """
        _insert_user(tmp_db_path, user_id="plus-pc", tier="plus", monthly_limit=-1)
        headers = {"Authorization": f"Bearer {_jwt_token('plus-pc')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_user_model"
        assert "cta" not in body

    def test_plus_user_ssai_model_routes_to_gp(self, client, tmp_db_path):
        """Plus user with SS AI selected → send_to_gp."""
        _insert_user(tmp_db_path, user_id="plus-ssai", tier="plus", monthly_limit=-1)
        headers = {"Authorization": f"Bearer {_jwt_token('plus-ssai')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "ssai"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp"
        assert "cta" not in body

    def test_free_user_ssai_selected_routes_to_gp(self, client, tmp_db_path):
        """Free user with SS AI picked → send_to_gp (no CTA — they opted in)."""
        _insert_user(tmp_db_path, user_id="free-ssai", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('free-ssai')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "ssai"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp"
        assert "cta" not in body

    def test_free_user_external_routes_to_gp_under_ssai_free_only(self, client, tmp_db_path):
        """Free + external → send_to_gp under ssai_free_only (GP overrides BYOK for Free)."""
        _insert_user(tmp_db_path, user_id="free-ext", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('free-ext')}"}
        resp = client.post(
            PREFLIGHT_PATH,
            json={"selected_model": "external"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "send_to_gp"
        assert "cta" not in body
        # No quota_* fields after Slice 5 — budget gate is the gate now.
        assert "quota_remaining" not in body
        assert "quota_total" not in body
