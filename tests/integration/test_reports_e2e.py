"""End-to-end integration tests for POST /v1/meetings/{meeting_id}/report.

Exercises the enforcement gates added to prevent the cost leak where
exhausted users could keep generating reports (cost was recorded post-call).
"""


class TestReportQuota:
    def test_report_quota_exhausted_no_llm_call(self, client, exhausted_user, mock_provider):
        """Exhausted free user → 429 from report endpoint, no LLM call."""
        resp = client.post(
            "/v1/meetings/test-meeting-001/report",
            json={"duration_seconds": 600},
            headers=exhausted_user["headers"],
        )
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "allocation_exhausted"
        mock_provider.assert_not_called()

    def test_report_no_auth_returns_401(self, client):
        """Report request without auth token → 401/403."""
        resp = client.post(
            "/v1/meetings/test-meeting-001/report",
            json={"duration_seconds": 600},
        )
        assert resp.status_code in (401, 403)
