"""End-to-end tests for memory-capture tier gating.

Covers:
- Pro user: capture-transcript fires cq.capture; quilt fetch is passthrough.
- Free user (within quota): capture-transcript fires cq.capture and
  decrements quota. Nothing is stamped.
- Free user (over quota): capture-transcript does NOT fire cq.capture.
- Quilt fetch is a pure passthrough for every tier, including a user
  carrying a legacy CTA stamp from before the synthetic card retired.

The synthetic upsell card was retired 2026-08-11: SS's decode audit
showed it had NEVER rendered on any build (closed PatchType enum drops
unknown types at decode, before any filter), and the decision moved the
free-tier Memory upsell onto the gate/teaser lane with served copy. The
served strings themselves stay pinned, all four locales, in
tests/test_meeting_memory_tier.py.
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import httpx


def _empty_quilt_response():
    """Return the real CQ-shaped empty response for use in mocks.

    Real shape per https://cq.shouldersurf.com/v1/quilt:
      {user_id, facts, action_items, deleted, server_time}
    No "patches" key — that field is iOS-internal naming, not CQ's.
    """
    return {
        "user_id": "test-user",
        "facts": [],
        "action_items": [],
        "deleted": [],
        "server_time": "2026-04-30T20:00:00Z",
    }


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
    def test_free_first_capture_fires_and_decrements_without_stamping(
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

        # Quota decremented; nothing stamped (the synthetic card retired,
        # the upsell rides the gate/teaser lane with served copy).
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            """SELECT memory_used_this_period, memory_last_origin_id, memory_last_cta_kind
               FROM users WHERE id = ?""",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] is None
        assert row[2] is None

    def test_free_quilt_fetch_ignores_a_legacy_stamp(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        """A user row can still carry a CTA stamp written before the
        retirement (nothing clears them anymore, and nothing should
        write new ones). The fetch must ignore it: no synthetic fact, no
        mutation, CQ's body byte-for-byte."""
        # Stamp the user manually, simulating pre-retirement state.
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """UPDATE users SET memory_last_origin_id = ?, memory_last_cta_kind = ?
               WHERE id = ?""",
            ("m-free-1", "free_within_quota_footer", free_user["user_id"]),
        )
        conn.commit()
        conn.close()

        # Real CQ response shape: {user_id, facts, action_items, deleted,
        # server_time} — see https://cq.shouldersurf.com/v1/quilt sample.
        # Real fact shape uses patch_id + fact (not id + text).
        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(
            {
                "user_id": free_user["user_id"],
                "facts": [
                    {
                        "patch_id": "p1",
                        "fact": "Existing real memory",
                        "category": "fact",
                        "patch_type": "fact",
                    }
                ],
                "action_items": [],
                "deleted": [],
                "server_time": "2026-04-30T20:00:00Z",
            }
        )
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{free_user['user_id']}",
                headers=free_user["headers"],
            )

        assert resp.status_code == 200
        body = resp.json()
        # CQ's facts pass through untouched: the one real fact, nothing
        # synthetic. Before the retirement this asserted an appended cta
        # object that SS's decoder had been silently dropping all along.
        assert len(body["facts"]) == 1
        assert body["facts"][0]["patch_id"] == "p1"
        assert all(f.get("category") != "cta" for f in body["facts"])


class TestFreeOverQuota:
    def test_free_over_quota_still_captures_for_people_without_stamping(
        self, client_with_cq, free_user, mock_cq, tmp_db_path,
    ):
        # Pre-set the user to "1 used in current period" so quota is exhausted.
        from app.services.period import current_period_utc
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
        # Over quota now CAPTURES anyway (2026-08-10, Scott: People is
        # exempt from the free-tier cap). A capture feeds two things and
        # only one is paid: person entities are People, free on every tier,
        # and quilt patches are Memory, which is not. Skipping starved a
        # feature the user is entitled to in order to meter one they are
        # not, and left their People tab empty for months, which reads as
        # broken rather than locked.
        assert mock_cq["capture"].await_count == 1

        # And nothing stamped: the upsell state is the client's to derive
        # from the gate/teaser lane, not a per-meeting flag on the user row.
        conn = sqlite3.connect(tmp_db_path)
        row = conn.execute(
            "SELECT memory_last_origin_id, memory_last_cta_kind FROM users WHERE id = ?",
            (free_user["user_id"],),
        ).fetchone()
        conn.close()
        assert row == (None, None)


class TestNoCtaWhenNotPending:
    def test_pro_quilt_fetch_passes_through_unmodified(
        self, client_with_cq, pro_user, mock_cq, tmp_db_path,
    ):
        empty = _empty_quilt_response()
        mock_resp, auth_cm, client_cm = _patched_quilt_fetch(empty)
        with auth_cm, client_cm as MockClient:
            _setup_async_client_mock(MockClient, mock_resp)
            resp = client_with_cq.get(
                f"/v1/quilt/{pro_user['user_id']}",
                headers=pro_user["headers"],
            )
        assert resp.status_code == 200
        # No CTA stamped → response passes through CQ's body unchanged.
        body = resp.json()
        assert body["facts"] == []
        assert body["action_items"] == []


class TestRecoveryHeader:
    """X-CZ-Recovery header lands and emits a structured log line.

    SS sets this header on captures that are part of the report-404
    recovery flow. GP must surface it so dashboards can split capture
    volume by recovery_source.
    """

    def test_header_present_emits_structured_log(
        self, client_with_cq, pro_user, mock_cq, caplog,
    ):
        import logging
        caplog.set_level(logging.INFO, logger="app.routers.cq_proxy")

        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "hello", "meeting_id": "m-rec-1"},
            headers={**pro_user["headers"], "X-CZ-Recovery": "report-404-replay"},
        )
        assert resp.status_code == 200

        rec = next(
            (r for r in caplog.records if r.message == "capture_transcript_recovery"),
            None,
        )
        assert rec is not None, "expected capture_transcript_recovery log line"
        assert rec.recovery_source == "report-404-replay"
        assert rec.user_id == pro_user["user_id"]
        assert rec.origin_id == "m-rec-1"
        assert rec.origin_type == "meeting"

    def test_header_absent_no_recovery_log(
        self, client_with_cq, pro_user, mock_cq, caplog,
    ):
        import logging
        caplog.set_level(logging.INFO, logger="app.routers.cq_proxy")

        resp = client_with_cq.post(
            "/v1/capture-transcript",
            json={"transcript": "hello", "meeting_id": "m-rec-2"},
            headers=pro_user["headers"],
        )
        assert resp.status_code == 200

        recovery_logs = [
            r for r in caplog.records
            if r.message == "capture_transcript_recovery"
        ]
        assert recovery_logs == []
