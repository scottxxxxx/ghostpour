"""End-to-end integration tests for POST /v1/meetings/{meeting_id}/report.

Exercises the enforcement gates added to prevent the cost leak where
exhausted users could keep generating reports (cost was recorded post-call).
"""


class TestReportQuota:
    def test_report_quota_exhausted_returns_canned(self, client, exhausted_user, mock_provider, tmp_db_path):
        """Exhausted free user → canned/sample report, no LLM call.

        Replaces the legacy 429/allocation_exhausted path. The canned-report
        envelope (report_status='placeholder_budget_blocked', is_editable=
        false, feature_state.cta) is now the unified over-cap response.
        """
        # Need a transcript so the endpoint passes the no_meeting_data
        # guard before hitting the budget gate.
        import sqlite3
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            """INSERT INTO meeting_transcripts (id, user_id, meeting_id, transcript, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("t1", exhausted_user["user_id"], "test-meeting-001", "transcript", "2026-05-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        resp = client.post(
            "/v1/meetings/test-meeting-001/report",
            json={"duration_seconds": 600},
            headers=exhausted_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_status"] == "placeholder_budget_blocked"
        assert body["is_editable"] is False
        cta = body["feature_state"]["cta"]
        assert cta["kind"] == "report_blocked_budget_exhausted"
        assert cta["action"] == "open_paywall"
        mock_provider.assert_not_called()

    def test_report_no_auth_returns_401(self, client):
        """Report request without auth token → 401/403."""
        resp = client.post(
            "/v1/meetings/test-meeting-001/report",
            json={"duration_seconds": 600},
        )
        assert resp.status_code in (401, 403)
