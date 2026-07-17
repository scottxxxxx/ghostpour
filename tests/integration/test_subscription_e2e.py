"""End-to-end integration tests for subscription endpoints."""

import sqlite3

from app.models.tier import load_tier_config
from tests.conftest import _insert_user, _jwt_token, chat_request

# Read product IDs from tier config (respects product-ids.yml overrides)
_tier_config = load_tier_config("config/tiers.yml")
_PLUS_PRODUCT = _tier_config.tiers["plus"].storekit_product_id
_PRO_PRODUCT = _tier_config.tiers["pro"].storekit_product_id


class TestVerifyReceipt:
    def test_verify_receipt_upgrades_tier(self, client, tmp_db_path):
        """Verify receipt with a standard product ID → tier upgraded."""
        _insert_user(tmp_db_path, user_id="upgrade-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('upgrade-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_123",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_tier"] == "plus"
        assert data["old_tier"] == "free"
        assert data["is_trial"] is False

    def test_verify_receipt_trial(self, client, tmp_db_path):
        """Trial offer → is_trial=True, trial_end set."""
        _insert_user(tmp_db_path, user_id="trial-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('trial-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PRO_PRODUCT,
                "transaction_id": "txn_456",
                "offer_type": "introductory",
                "offer_price": 0,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_trial"] is True
        assert "trial_end" in data

    def test_verify_receipt_idempotent_preserves_usage(self, client, tmp_db_path):
        """Re-verification of same tier should NOT reset monthly_used_usd.

        SS calls verify-receipt on every launch. If GP resets allocation each
        time, users lose their accumulated usage and the hours.used display
        shows 0 even when they've consumed real quota.
        """
        _insert_user(
            tmp_db_path,
            user_id="idempotent-user",
            tier="plus",
            monthly_limit=2.40,
            monthly_used=0.50,
        )
        headers = {"Authorization": f"Bearer {_jwt_token('idempotent-user')}"}

        # Re-verify same subscription (not a tier change)
        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_same",
                "is_trial": False,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        # monthly_used_usd should be preserved (not reset to 0)
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT monthly_used_usd, monthly_cost_limit_usd FROM users WHERE id = ?",
            ("idempotent-user",),
        ).fetchone()
        conn.close()
        assert row[0] == 0.50, f"monthly_used_usd was reset to {row[0]}, expected 0.50"
        assert row[1] == -1  # Plus tier is unlimited

    def test_verify_receipt_idempotent_trial_preserves_usage(self, client, tmp_db_path):
        """Trial re-verification should NOT reset monthly_used_usd either."""
        _insert_user(
            tmp_db_path,
            user_id="idempotent-trial-user",
            tier="plus",
            monthly_limit=0.50,
            monthly_used=0.30,
            is_trial=True,
        )
        headers = {"Authorization": f"Bearer {_jwt_token('idempotent-trial-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_trial_same",
                "offer_type": "introductory",
                "offer_price": 0,
                "is_trial": True,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT monthly_used_usd, is_trial FROM users WHERE id = ?",
            ("idempotent-trial-user",),
        ).fetchone()
        conn.close()
        assert row[0] == 0.30, f"trial monthly_used_usd was reset to {row[0]}, expected 0.30"
        assert row[1] == 1  # still in trial

    def test_verify_receipt_cross_account_clears_other_binding(
        self, client, tmp_db_path
    ):
        """Same transaction_id under a new JWT clears the binding from any
        other user row, preventing duplicate bindings.

        Reachable when SS replays a queued receipt under a different signed-in
        user than the one that originally verified it (account switch on same
        device, or anon-purchase → later sign-in to a different account).
        Without cleanup-on-bind, two user rows would hold the same
        original_transaction_id and the apple-notifications webhook lookup
        would only update one of them.
        """
        # User A originally verified the receipt
        _insert_user(
            tmp_db_path, user_id="user-a", tier="plus", monthly_limit=-1,
        )
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            "UPDATE users SET original_transaction_id = ? WHERE id = ?",
            ("txn_replay", "user-a"),
        )
        conn.commit()
        conn.close()

        # User B signs in on the same device; SS replays the queued receipt
        _insert_user(
            tmp_db_path, user_id="user-b", tier="free", monthly_limit=0.05,
        )
        headers = {"Authorization": f"Bearer {_jwt_token('user-b')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_replay",
                "is_trial": False,
            },
            headers=headers,
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(tmp_db_path)
        rows = conn.execute(
            "SELECT id FROM users WHERE original_transaction_id = ?",
            ("txn_replay",),
        ).fetchall()
        a_txn = conn.execute(
            "SELECT original_transaction_id FROM users WHERE id = ?",
            ("user-a",),
        ).fetchone()[0]
        conn.close()

        assert len(rows) == 1, (
            f"expected exactly one row holding the txn, got {len(rows)}: "
            f"{[r[0] for r in rows]}"
        )
        assert rows[0][0] == "user-b"
        assert a_txn is None, "user-a should have been cleared"

    def test_verify_receipt_unknown_product(self, client, tmp_db_path):
        """Unknown product ID → 400."""
        _insert_user(tmp_db_path, user_id="unknown-product-user", tier="free")
        headers = {"Authorization": f"Bearer {_jwt_token('unknown-product-user')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": "com.fake.product",
                "transaction_id": "txn_789",
            },
            headers=headers,
        )
        assert resp.status_code == 400

    def test_verify_receipt_includes_placeholder_report_count(
        self, client, tmp_db_path,
    ):
        """When a Free user upgrades and they have canned (budget-blocked)
        meeting reports, the count is surfaced on the verify-receipt
        response so iOS can prompt regen for the most recent one without
        scanning the meeting list. Real reports don't count."""
        _insert_user(tmp_db_path, user_id="upgrade-with-placeholders", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('upgrade-with-placeholders')}"}

        # Seed: 1 real report + 2 canned (budget-blocked) reports.
        conn = sqlite3.connect(tmp_db_path)
        for i, status in enumerate([None, "placeholder_budget_blocked", "placeholder_budget_blocked"]):
            conn.execute(
                """INSERT INTO meeting_reports
                   (id, user_id, meeting_id, report_json, report_html,
                    created_at, report_status, is_editable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"r-{i}", "upgrade-with-placeholders", f"m-{i}",
                    "{}", "<html></html>",
                    "2026-04-30T00:00:00Z",
                    status,
                    1 if status is None else 0,
                ),
            )
        conn.commit()
        conn.close()

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_placeholder_count",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        # 2 canned reports, 1 real → count is 2 (real reports excluded).
        assert resp.json()["placeholder_report_count"] == 2

    def test_verify_receipt_zero_placeholders_when_none_exist(
        self, client, tmp_db_path,
    ):
        """User with no canned reports — count is 0, not absent. iOS can
        rely on the field being present and integer-valued."""
        _insert_user(tmp_db_path, user_id="no-placeholders", tier="free", monthly_limit=0.35)
        headers = {"Authorization": f"Bearer {_jwt_token('no-placeholders')}"}

        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PLUS_PRODUCT,
                "transaction_id": "txn_no_placeholders",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["placeholder_report_count"] == 0


class TestSyncSubscription:
    def test_sync_downgrade_to_free(self, client, tmp_db_path):
        """No active product → downgrade to free."""
        _insert_user(tmp_db_path, user_id="downgrade-user", tier="plus", monthly_limit=2.40)
        headers = {"Authorization": f"Bearer {_jwt_token('downgrade-user')}"}

        resp = client.post(
            "/v1/sync-subscription",
            json={"active_product_id": None},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "downgraded"
        assert data["new_tier"] == "free"

    def test_sync_no_change(self, client, tmp_db_path):
        """Already on correct tier → no change."""
        _insert_user(tmp_db_path, user_id="synced-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('synced-user')}"}

        resp = client.post(
            "/v1/sync-subscription",
            json={"active_product_id": None},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "none"


class TestUsageMe:
    def test_usage_me_response_shape(self, client, free_user):
        """GET /v1/usage/me returns expected fields."""
        resp = client.get("/v1/usage/me", headers=free_user["headers"])
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "tier" in data
        assert "allocation" in data
        assert "monthly_limit_usd" in data["allocation"]
        assert "monthly_used_usd" in data["allocation"]
        assert "percent_used" in data["allocation"]
        assert "hours" in data
        assert "this_month" in data
        assert "features" in data
        assert "summary_mode" in data

    def test_usage_me_reflects_tier(self, client, free_user, pro_user):
        """Different tiers return different allocation limits."""
        free_resp = client.get("/v1/usage/me", headers=free_user["headers"])
        pro_resp = client.get("/v1/usage/me", headers=pro_user["headers"])

        free_limit = free_resp.json()["allocation"]["monthly_limit_usd"]
        pro_limit = pro_resp.json()["allocation"]["monthly_limit_usd"]
        # Pro is unlimited (-1), free has a cap
        assert pro_limit == -1  # unlimited
        assert free_limit > 0   # capped

    def test_usage_me_includes_credits_block(self, client, free_user, tmp_db_path):
        """Free users see a credits.{used,total,remaining,resets_at} block
        for the iOS Account screen. Replaces the misleading 'X of Y meetings'
        derived display (which had drift between marketing-copy hours and
        the real $-budget). 1¢ = 100 credits → Free $0.35 = 3,500 credits."""
        # Prime monthly_used to exercise the math.
        import sqlite3
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            "UPDATE users SET monthly_used_usd = ? WHERE id = ?",
            (0.34, free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        resp = client.get("/v1/usage/me", headers=free_user["headers"])
        assert resp.status_code == 200
        credits = resp.json()["credits"]
        # total derives from the live free cap (survives the TestFlight bump).
        from app.models.tier import load_tier_config
        free_limit = load_tier_config("config/tiers.yml").tiers["free"].monthly_cost_limit_usd
        expected_total = int(round(free_limit * 10000))
        assert credits["total"] == expected_total
        # $0.34 used → 3,400 credits (independent of the cap).
        assert credits["used"] == 3400
        assert credits["remaining"] == expected_total - 3400
        # resets_at echoes allocation.resets_at — same field, different
        # presentation. iOS UI binds to credits.resets_at directly.
        assert "resets_at" in credits

    def test_usage_me_pro_has_unlimited_credits(self, client, pro_user):
        """Pro is unlimited budget. Surface this as total=-1 / remaining=-1
        so iOS can render an 'unlimited' badge instead of a depleting bar."""
        resp = client.get("/v1/usage/me", headers=pro_user["headers"])
        assert resp.status_code == 200
        credits = resp.json()["credits"]
        assert credits["total"] == -1
        assert credits["remaining"] == -1
        # `used` still gets a real value — useful for analytics even when
        # there's no cap.
        assert credits["used"] >= 0

    def test_usage_me_budget_exhausted_cta_present_when_credits_zero(
        self, client, exhausted_user,
    ):
        """A Free user past their cap gets a `budget_exhausted_cta` block
        on /v1/usage/me so iOS can render the upgrade prompt on pre-flight
        gates (meeting-start, etc.) without firing a /v1/chat call first.
        Same canonical CTA shape the /v1/chat block-response emits."""
        resp = client.get("/v1/usage/me", headers=exhausted_user["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["credits"]["remaining"] == 0
        cta = body["budget_exhausted_cta"]
        assert cta["kind"] == "budget_exhausted"
        assert cta["action"] == "open_paywall"
        assert isinstance(cta["text"], str) and cta["text"]

    def test_usage_me_no_budget_cta_when_credits_remaining(
        self, client, free_user,
    ):
        """Free user with budget still available → no `budget_exhausted_cta`."""
        resp = client.get("/v1/usage/me", headers=free_user["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["credits"]["remaining"] > 0
        assert "budget_exhausted_cta" not in body

    def test_usage_me_no_budget_cta_for_unlimited_pro(self, client, pro_user):
        """Pro is unlimited (`credits.total == -1`). They never exhaust;
        omitting the field saves bytes and prevents nonsense UX where iOS
        could try to render an upgrade prompt for a paying user."""
        resp = client.get("/v1/usage/me", headers=pro_user["headers"])
        assert resp.status_code == 200
        assert "budget_exhausted_cta" not in resp.json()

    def test_usage_me_budget_cta_honors_accept_language(
        self, client, exhausted_user,
    ):
        """Spanish-locale request gets Spanish copy. Pin the locale plumbing
        all the way through `_parse_accept_language` → resolver → response."""
        resp = client.get(
            "/v1/usage/me",
            headers={
                **exhausted_user["headers"],
                "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            },
        )
        assert resp.status_code == 200
        cta = resp.json()["budget_exhausted_cta"]
        # Localized copy lives in tiers.es.json → free → feature_definitions.budget
        assert "Plus" in cta["text"]
        # Crude but durable signal: Spanish copy mentions "Actualiza"
        assert "Actualiza" in cta["text"] or "agotado" in cta["text"]


class TestOfferIdAttribution:
    """ASC offer redemption attribution (SS emailed offer codes, 2026-07-17).

    The client sends offer_id (StoreKit transaction.offer.id) on
    /v1/verify-receipt; GP stores it on the subscription_events row and
    reports it via /webhooks/admin/subscriptions/redemptions. Apple never
    exposes the redeemed code string, so offer_id is the finest grain — SS
    joins it against their send log for per-user, per-code attribution.
    """

    ADMIN = {"X-Admin-Key": "test-admin-key"}

    def test_offer_id_stored_on_subscription_event(self, client, tmp_db_path):
        _insert_user(tmp_db_path, user_id="email-offer-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('email-offer-user')}"}
        resp = client.post(
            "/v1/verify-receipt",
            json={
                "product_id": _PRO_PRODUCT,
                "transaction_id": "txn_email_offer",
                "offer_id": "ss_email_launch_2026",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT offer_id, event_type, to_tier FROM subscription_events "
            "WHERE user_id = ?", ("email-offer-user",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "ss_email_launch_2026"
        assert row[2] == "pro"

    def test_offer_id_absent_stays_null(self, client, tmp_db_path):
        """Additive field: clients that don't send it produce NULL rows,
        which the redemptions report excludes."""
        _insert_user(tmp_db_path, user_id="no-offer-user", tier="free", monthly_limit=0.05)
        headers = {"Authorization": f"Bearer {_jwt_token('no-offer-user')}"}
        resp = client.post(
            "/v1/verify-receipt",
            json={"product_id": _PLUS_PRODUCT, "transaction_id": "txn_plain"},
            headers=headers,
        )
        assert resp.status_code == 200
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT offer_id FROM subscription_events WHERE user_id = ?",
            ("no-offer-user",),
        ).fetchone()
        conn.close()
        assert row is not None and row[0] is None

    def test_redemptions_report_groups_and_filters(self, client, tmp_db_path):
        for i, (uid, offer) in enumerate([
            ("redeem-a", "ss_email_launch_2026"),
            ("redeem-b", "ss_email_launch_2026"),
            ("redeem-c", "ss_cta_pool"),
        ]):
            _insert_user(tmp_db_path, user_id=uid, tier="free", monthly_limit=0.05)
            r = client.post(
                "/v1/verify-receipt",
                json={
                    "product_id": _PRO_PRODUCT,
                    "transaction_id": f"txn_redeem_{i}",
                    "offer_id": offer,
                },
                headers={"Authorization": f"Bearer {_jwt_token(uid)}"},
            )
            assert r.status_code == 200

        resp = client.get("/webhooks/admin/subscriptions/redemptions", headers=self.ADMIN)
        assert resp.status_code == 200
        body = resp.json()
        by_offer = {o["offer_id"]: o for o in body["offers"]}
        assert by_offer["ss_email_launch_2026"]["redemptions"] == 2
        assert by_offer["ss_email_launch_2026"]["users"] == 2
        assert by_offer["ss_cta_pool"]["redemptions"] == 1
        assert len(body["redemptions"]) == 3

        # ?offer_id= narrows the row list to one pool
        resp = client.get(
            "/webhooks/admin/subscriptions/redemptions",
            params={"offer_id": "ss_email_launch_2026"},
            headers=self.ADMIN,
        )
        rows = resp.json()["redemptions"]
        assert {r["user_id"] for r in rows} == {"redeem-a", "redeem-b"}
        assert all(r["offer_id"] == "ss_email_launch_2026" for r in rows)

    def test_redemptions_report_requires_admin_key(self, client):
        resp = client.get(
            "/webhooks/admin/subscriptions/redemptions",
            headers={"X-Admin-Key": "wrong"},
        )
        assert resp.status_code == 403
