"""Integration tests for /v1/preferences endpoints + the
marketing_opt_in surface on /v1/usage/me + /unsubscribe public link."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.services import marketing_opt_in as marketing


def _seed_user(db_path: str, user_id: str, email: str = "test@example.com") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, tier, created_at, updated_at,
            is_active, monthly_used_usd, overage_balance_usd, marketing_opt_in)
           VALUES (?, ?, ?, 'free', ?, ?, 1, 0, 0, 0)""",
        (user_id, f"sub_{user_id}", email, now, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# /v1/preferences/me
# ---------------------------------------------------------------------------

class TestGetPreferences:
    def test_default_is_off(self, client, free_user):
        resp = client.get("/v1/preferences/me", headers=free_user["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["marketing_opt_in"]["enabled"] is False
        assert body["marketing_opt_in"]["updated_at"] is None
        assert body["marketing_opt_in"]["source"] is None

    def test_requires_auth(self, client):
        resp = client.get("/v1/preferences/me")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PUT /v1/preferences/marketing-opt-in
# ---------------------------------------------------------------------------

class TestUpdateMarketingOptIn:
    def test_opt_in_records_ios_source(self, client, free_user):
        resp = client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["marketing_opt_in"]["enabled"] is True
        assert body["marketing_opt_in"]["source"] == marketing.SOURCE_IOS
        assert body["changed"] is True

    def test_idempotent_same_value(self, client, free_user):
        client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )
        resp = client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )
        body = resp.json()
        assert body["marketing_opt_in"]["enabled"] is True
        assert body["changed"] is False

    def test_opt_out_after_opt_in(self, client, free_user):
        client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )
        resp = client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": False},
            headers=free_user["headers"],
        )
        body = resp.json()
        assert body["marketing_opt_in"]["enabled"] is False
        assert body["marketing_opt_in"]["source"] == marketing.SOURCE_IOS

    def test_requires_auth(self, client):
        resp = client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# /v1/usage/me — surfaces marketing_opt_in for SS startup query
# ---------------------------------------------------------------------------

class TestUsageMeMarketingField:
    def test_usage_me_includes_marketing_opt_in_default_off(self, client, free_user, mock_pricing):
        resp = client.get("/v1/usage/me", headers=free_user["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert "marketing_opt_in" in body
        assert body["marketing_opt_in"]["enabled"] is False

    def test_usage_me_reflects_recent_opt_in(self, client, free_user, mock_pricing):
        client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )
        resp = client.get("/v1/usage/me", headers=free_user["headers"])
        body = resp.json()
        assert body["marketing_opt_in"]["enabled"] is True
        assert body["marketing_opt_in"]["source"] == marketing.SOURCE_IOS


# ---------------------------------------------------------------------------
# /unsubscribe public link
# ---------------------------------------------------------------------------

# JWT secret matches the test env (set in conftest's app_env fixture).
_TEST_SECRET = "test-secret-key-that-is-long-enough-for-hs256-validation"


class TestUnsubscribeLink:
    def test_valid_token_flips_flag_off(self, client, free_user):
        # First opt them in via PUT so we can see the flip
        client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )
        token = marketing.generate_unsubscribe_token(free_user["user_id"], _TEST_SECRET)
        resp = client.get(f"/unsubscribe?token={token}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"].lower()
        assert "unsubscribed" in resp.text.lower()

        # Confirm via authenticated /v1/preferences/me
        check = client.get("/v1/preferences/me", headers=free_user["headers"])
        body = check.json()
        assert body["marketing_opt_in"]["enabled"] is False
        assert body["marketing_opt_in"]["source"] == marketing.SOURCE_UNSUBSCRIBE_LINK

    def test_invalid_token_returns_400_html(self, client):
        resp = client.get("/unsubscribe?token=garbage")
        assert resp.status_code == 400
        assert "text/html" in resp.headers["content-type"].lower()
        assert "invalid" in resp.text.lower()

    def test_missing_token_returns_400(self, client):
        resp = client.get("/unsubscribe")
        assert resp.status_code == 400

    def test_already_off_still_renders_ok_page(self, client, free_user):
        token = marketing.generate_unsubscribe_token(free_user["user_id"], _TEST_SECRET)
        # User is opt_in=False by default; clicking unsubscribe is a no-op flip
        resp = client.get(f"/unsubscribe?token={token}")
        assert resp.status_code == 200
        assert "already off" in resp.text.lower() or "unsubscribed" in resp.text.lower()

    def test_rate_limit_kicks_in_after_30_per_minute(self, client):
        """Per-IP rate limit pins the endpoint at 30/min. The HMAC token
        is unforgeable so this is just belt-and-suspenders against
        endpoint pounding. TestClient connects from 127.0.0.1 so all
        requests share the same rate-limit key."""
        # Burn through the budget with garbage tokens
        for _ in range(30):
            client.get("/unsubscribe?token=bad")
        # 31st should 429
        resp = client.get("/unsubscribe?token=bad")
        assert resp.status_code == 429
        assert "retry-after" in {h.lower() for h in resp.headers.keys()}


# ---------------------------------------------------------------------------
# Spam complaint webhook also flips the flag
# ---------------------------------------------------------------------------

class TestSpamComplaintFlipsOptIn:
    def test_complaint_for_known_user_flips_flag(
        self, client, free_user, monkeypatch, tmp_db_path,
    ):
        """A spam complaint for a user's email should set their
        marketing_opt_in to False with source=spam_complaint, in addition
        to adding their address to the suppression list."""
        from app import secrets as app_secrets
        # Webhook secret is required; use the same path as the existing
        # webhook tests.
        test_secret = "whsec_dGVzdHNlY3JldHRlc3RzZWNyZXR0ZXN0c2VjcmV0"
        monkeypatch.setenv("CZ_RESEND_WEBHOOK_SECRET", test_secret)
        app_secrets.get_secret.cache_clear()

        # First, opt them in
        client.put(
            "/v1/preferences/marketing-opt-in",
            json={"opt_in": True},
            headers=free_user["headers"],
        )

        # Build a signed spam-complaint webhook for the user's email
        import base64, hmac, hashlib, json, time, uuid
        body = {
            "type": "email.complained",
            "data": {"to": [f"{free_user['user_id']}@test.com"], "email_id": "em_complaint"},
        }
        raw = json.dumps(body).encode()
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        ts = str(int(time.time()))
        secret_bytes = base64.b64decode(test_secret.removeprefix("whsec_"))
        content = f"{msg_id}.{ts}.{raw.decode()}".encode()
        sig = base64.b64encode(
            hmac.new(secret_bytes, content, hashlib.sha256).digest()
        ).decode()
        headers = {
            "svix-id": msg_id,
            "svix-timestamp": ts,
            "svix-signature": f"v1,{sig}",
        }
        resp = client.post("/webhooks/resend", content=raw, headers=headers)
        assert resp.status_code == 200

        # User's marketing_opt_in should now be False with spam_complaint source
        check = client.get("/v1/preferences/me", headers=free_user["headers"])
        body = check.json()
        assert body["marketing_opt_in"]["enabled"] is False
        assert body["marketing_opt_in"]["source"] == marketing.SOURCE_SPAM_COMPLAINT

        app_secrets.get_secret.cache_clear()
