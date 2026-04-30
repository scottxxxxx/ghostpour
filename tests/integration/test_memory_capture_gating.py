"""End-to-end tests for memory-capture tier gating + CTA injection.

Covers:
- Pro user: capture-transcript fires cq.capture; no CTA injected on quilt fetch.
- Free user (within quota): capture-transcript fires cq.capture, decrements
  quota, stamps CTA. Next quilt fetch surfaces the synthetic upsell card
  with matching origin_id, then clears the flag.
- Free user (over quota): capture-transcript does NOT fire cq.capture, but
  still stamps the no-quota CTA. Quilt fetch surfaces the upsell card.
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import httpx


def _patched_quilt_fetch(json_payload):
    """Helper: patch httpx.AsyncClient so the proxied GET /v1/quilt returns
    a known payload. Mirrors the pattern in test_cq_proxy_e2e.py."""
    mock_resp = httpx.Response(
        status_code=200,
        json=json_payload,
        request=httpx.Request("GET", "http://cq-mock/v1/quilt/u"),
    )
    cm = patch("app.services.context_quilt._get_auth_headers",
               new_callable=AsyncMock,
               return_value={"Authorization": "Bearer mock"})
    client_cm = patch("httpx.AsyncClient")
    return mock_resp, cm, client_cm


def _setup_async_client_mock(MockClient, mock_resp):
    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.request = AsyncMock(return_value=mock_resp)
    MockClient.return_value = instance


class TestPro:
    def test_pro_capture_fires_no_cta_stamped(
        self, client_with_cq, pro_user, mock_cq, tmp_db_path,
    ):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-pro-1"},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200
        assert mock_cq["capture"].await_count == 1

        # No CTA should be stamped on the user row.
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT memory_last_origin_id, memory_last_cta_kind FROM users WHERE id = ?",
            (pro_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row == (None, None)


class TestFreeWithinQuota:
    def test_free_first_capture_fires_decrements_and_stamps_cta(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-free-1"},
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        # Free + within quota → capture fires.
        assert mock_cq["capture"].await_count == 1

        # Quota decremented + CTA stamped with origin id.
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            """SELECT memory_used_this_period, memory_last_origin_id, memory_last_cta_kind
               FROM users WHERE id = ?""",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] == "m-free-1"
        assert row[2] == "free_within_quota_footer"

    def test_free_quilt_fetch_injects_cta_card_then_clears(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        # Stamp the user manually (skip the capture-transcript round-trip).
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """UPDATE users SET memory_last_origin_id = ?, memory_last_cta_kind = ?
               WHERE id = ?""",
            ("m-free-1", "free_within_quota_footer", free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(
            {"patches": [{"id": "p1", "type": "TAKEAWAY", "text": "real memory"}],
             "count": 1}
        )
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{free_user['user_id']}",
                headers=free_user["headers"],
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        cta = body["patches"][-1]
        assert cta["metadata"]["is_synthetic"] is True
        assert cta["metadata"]["origin_id"] == "m-free-1"
        assert cta["metadata"]["cta_kind"] == "free_within_quota_footer"
        assert "Upgrade to Pro" in cta["text"]

        # Flag cleared after one render.
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT memory_last_origin_id, memory_last_cta_kind FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row == (None, None)


class TestFreeOverQuota:
    def test_free_over_quota_skips_capture_but_stamps_cta(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        # Pre-set the user to "1 used in current period" so quota is exhausted.
        from app.services.project_chat_quota import current_period_utc
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """UPDATE users SET memory_used_this_period = 1, memory_period = ?
               WHERE id = ?""",
            (current_period_utc(), free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "...", "meeting_id": "m-free-2"},
            headers=free_user["headers"],
        )
        assert resp.status_code == 200
        # Over quota → capture must NOT fire.
        assert mock_cq["capture"].await_count == 0

        # CTA stamped with the no-quota variant.
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT memory_last_origin_id, memory_last_cta_kind FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row[0] == "m-free-2"
        assert row[1] == "free_no_quota_only"


class TestNoCtaWhenNotPending:
    def test_pro_quilt_fetch_passes_through_unmodified(
        self, client_with_cq, pro_user, mock_cq, tmp_db_path,
    ):
        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(
            {"patches": [], "count": 0}
        )
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{pro_user['user_id']}",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        assert resp.json() == {"patches": [], "count": 0}


class TestCtaLocalization:
    """Verify Accept-Language picks the locale-matched CTA copy.

    Falls back to default tiers config (English) when the requested locale
    doesn't have a localized override, and to features.yml as the final
    English source of truth.
    """

    def test_es_locale_picks_spanish_cta(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """UPDATE users SET memory_last_origin_id = ?, memory_last_cta_kind = ?
               WHERE id = ?""",
            ("m-es-1", "free_within_quota_footer", free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(
            {"patches": [], "count": 0}
        )
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{free_user['user_id']}",
                headers={**free_user["headers"], "Accept-Language": "es"},
            )

        assert resp.status_code == 200
        body = resp.json()
        cta = body["patches"][-1]
        assert "Actualiza a Pro" in cta["text"]
        assert "Memoria" in cta["text"]

    def test_ja_locale_picks_japanese_cta(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """UPDATE users SET memory_last_origin_id = ?, memory_last_cta_kind = ?
               WHERE id = ?""",
            ("m-ja-1", "free_no_quota_only", free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(
            {"patches": [], "count": 0}
        )
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{free_user['user_id']}",
                headers={**free_user["headers"], "Accept-Language": "ja"},
            )

        assert resp.status_code == 200
        body = resp.json()
        cta = body["patches"][-1]
        assert "Pro" in cta["text"]
        # Japanese-specific marker: メモリー (memory)
        assert "メモリー" in cta["text"]

    def test_unknown_locale_falls_back_to_english(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """UPDATE users SET memory_last_origin_id = ?, memory_last_cta_kind = ?
               WHERE id = ?""",
            ("m-fr-1", "free_within_quota_footer", free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(
            {"patches": [], "count": 0}
        )
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{free_user['user_id']}",
                headers={**free_user["headers"], "Accept-Language": "fr"},
            )

        assert resp.status_code == 200
        body = resp.json()
        cta = body["patches"][-1]
        assert "Upgrade to Pro" in cta["text"]
