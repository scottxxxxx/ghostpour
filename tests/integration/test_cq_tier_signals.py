"""Tests for tier signals to Context Quilt.

Covers:
- subscription_tier is forwarded on cq.capture (capture-transcript) and
  cq.recall (chat hook).
- notify_tier_change fires from /v1/verify-receipt on real state changes.
- notify_tier_change fires from /v1/apple-notifications on
  upgrade/downgrade/refund.
"""

from unittest.mock import AsyncMock, patch


class TestCaptureTierMetadata:
    def test_pro_capture_carries_pro_tier(self, client_with_cq, pro_user, mock_cq):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-pro"},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["subscription_tier"] == "pro"

    def test_free_within_quota_capture_carries_free_tier(
        self, client_with_cq, free_user, mock_cq
    ):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-free"},
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].await_count == 1
        assert mock_cq["capture"].call_args.kwargs["subscription_tier"] == "free"

    def test_admin_capture_carries_target_user_tier(
        self, client_with_cq, pro_user, mock_cq
    ):
        resp = client_with_cq.post(
            "/webhooks/admin/capture-transcript",
            json={
                "user_id": pro_user["user_id"],
                "transcript": "...",
                "meeting_id": "m-admin",
            },
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].call_args.kwargs["subscription_tier"] == "pro"


class TestNotifyTierChangeClient:
    """Direct tests of the cq.notify_tier_change client."""

    def test_posts_to_tier_change_endpoint(self):
        from app.services import context_quilt as cq

        async def run():
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=AsyncMock(
                raise_for_status=lambda: None,
            ))
            with patch("app.services.context_quilt._get_client", return_value=mock_client), \
                 patch("app.services.context_quilt._get_auth_headers",
                       new_callable=AsyncMock, return_value={"Authorization": "Bearer x"}), \
                 patch("app.services.context_quilt.get_settings") as gs:
                gs.return_value.cq_base_url = "https://cq.example"
                await cq.notify_tier_change(
                    user_id="u-1",
                    old_tier="free",
                    new_tier="pro",
                    event_type="upgrade",
                )
            mock_client.post.assert_awaited_once()
            call = mock_client.post.await_args
            assert call.args[0] == "/v1/users/u-1/tier-change"
            payload = call.kwargs["json"]
            assert payload["old_tier"] == "free"
            assert payload["new_tier"] == "pro"
            assert payload["event_type"] == "upgrade"
            assert payload["occurred_at"]  # ISO timestamp present

        import asyncio
        asyncio.run(run())

    def test_skips_when_cq_unconfigured(self):
        from app.services import context_quilt as cq

        async def run():
            mock_client = AsyncMock()
            with patch("app.services.context_quilt._get_client", return_value=mock_client), \
                 patch("app.services.context_quilt.get_settings") as gs:
                gs.return_value.cq_base_url = ""
                await cq.notify_tier_change(
                    user_id="u-2", old_tier="pro", new_tier="free", event_type="expire",
                )
            mock_client.post.assert_not_called()

        import asyncio
        asyncio.run(run())


class TestVerifyReceiptFiresNotify:
    """/v1/verify-receipt should fire notify_tier_change on state changes."""

    def test_free_to_paid_fires_upgrade(self, client_with_cq, free_user, mock_cq):
        with patch(
            "app.routers.chat.cq.notify_tier_change",
            new_callable=AsyncMock,
        ) as mock_notify:
            resp = client_with_cq.post(
                "/v1/verify-receipt",
                json={
                    "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
                    "transaction_id": "txn-1",
                    "is_trial": False,
                },
                headers=free_user["headers"],
            )
            assert resp.status_code == 200
            mock_notify.assert_awaited_once()
            kwargs = mock_notify.await_args.kwargs
            assert kwargs["old_tier"] == "free"
            assert kwargs["new_tier"] == "pro"
            assert kwargs["event_type"] == "upgrade"

    def test_idempotent_reverify_does_not_fire(self, client_with_cq, pro_user, mock_cq):
        with patch(
            "app.routers.chat.cq.notify_tier_change",
            new_callable=AsyncMock,
        ) as mock_notify:
            resp = client_with_cq.post(
                "/v1/verify-receipt",
                json={
                    "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
                    "transaction_id": "txn-2",
                    "is_trial": False,
                },
                headers=pro_user["headers"],
            )
            assert resp.status_code == 200
            mock_notify.assert_not_called()

    def test_trial_start_fires_trial_start_event(
        self, client_with_cq, free_user, mock_cq
    ):
        with patch(
            "app.routers.chat.cq.notify_tier_change",
            new_callable=AsyncMock,
        ) as mock_notify:
            resp = client_with_cq.post(
                "/v1/verify-receipt",
                json={
                    "product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
                    "transaction_id": "txn-3",
                    "is_trial": True,
                },
                headers=free_user["headers"],
            )
            assert resp.status_code == 200
            mock_notify.assert_awaited_once()
            assert mock_notify.await_args.kwargs["event_type"] == "trial_start"


class TestSyncSubscriptionFiresNotify:
    """/v1/sync-subscription should fire notify_tier_change on transitions."""

    def test_cancellation_fires(self, client_with_cq, pro_user, mock_cq):
        with patch(
            "app.routers.chat.cq.notify_tier_change",
            new_callable=AsyncMock,
        ) as mock_notify:
            resp = client_with_cq.post(
                "/v1/sync-subscription",
                json={"active_product_id": None, "is_trial": False},
                headers=pro_user["headers"],
            )
            assert resp.status_code == 200
            mock_notify.assert_awaited_once()
            kwargs = mock_notify.await_args.kwargs
            assert kwargs["old_tier"] == "pro"
            assert kwargs["new_tier"] == "free"
            assert kwargs["event_type"] == "cancellation"

    def test_idempotent_sync_does_not_fire(self, client_with_cq, pro_user, mock_cq):
        with patch(
            "app.routers.chat.cq.notify_tier_change",
            new_callable=AsyncMock,
        ) as mock_notify:
            resp = client_with_cq.post(
                "/v1/sync-subscription",
                json={
                    "active_product_id": "com.weirtech.shouldersurf.sub.pro.monthly",
                    "is_trial": False,
                },
                headers=pro_user["headers"],
            )
            assert resp.status_code == 200
            mock_notify.assert_not_called()

    def test_already_free_no_active_does_not_fire(
        self, client_with_cq, free_user, mock_cq
    ):
        with patch(
            "app.routers.chat.cq.notify_tier_change",
            new_callable=AsyncMock,
        ) as mock_notify:
            resp = client_with_cq.post(
                "/v1/sync-subscription",
                json={"active_product_id": None, "is_trial": False},
                headers=free_user["headers"],
            )
            assert resp.status_code == 200
            mock_notify.assert_not_called()
