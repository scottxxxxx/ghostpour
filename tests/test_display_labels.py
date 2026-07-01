"""Dashboard display relabeling for the summary-consolidation pass.

The SS client sends call_type="analysis" for BOTH its genuine
PostSessionAnalysis and its end-of-meeting SummaryConsolidation roll,
distinguished only by prompt_mode. The dashboard relabels the consolidation
subset as "consolidation" (display only — the stored wire value is untouched).
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from app.services.display_labels import display_call_type

ADMIN = {"X-Admin-Key": "test-admin-key"}


def test_consolidation_is_relabeled():
    assert display_call_type("analysis", "SummaryConsolidation") == "consolidation"


def test_real_analysis_is_untouched():
    assert display_call_type("analysis", "PostSessionAnalysis") == "analysis"


def test_other_call_types_pass_through():
    assert display_call_type("summary", "AutoSummary") == "summary"
    assert display_call_type("query", "ProjectChat") == "query"
    assert display_call_type("report", None) == "report"
    assert display_call_type(None, None) is None


def _insert_analysis(db_path, user_id, prompt_mode, *, latency_ms):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, error_message, call_type, prompt_mode, app_id)
           VALUES (?, ?, 'anthropic', 'claude-sonnet-4-6', 100, 50, 0.02, ?,
                   ?, 'success', NULL, 'analysis', ?, 'shouldersurf')""",
        (str(uuid.uuid4()), user_id, now, latency_ms, prompt_mode),
    )
    conn.commit()
    conn.close()


def test_by_call_type_splits_consolidation_from_analysis(client, tmp_db_path):
    """Two analysis rows with different prompt_modes land in separate buckets,
    and the CASE grouping keeps each bucket's latency average intact."""
    from tests.conftest import _insert_user

    user_id = "consolidation-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="pro", monthly_limit=5.0)
    # Two consolidation rolls (latency avg 200) + one real analysis (latency 500).
    _insert_analysis(tmp_db_path, user_id, "SummaryConsolidation", latency_ms=100)
    _insert_analysis(tmp_db_path, user_id, "SummaryConsolidation", latency_ms=300)
    _insert_analysis(tmp_db_path, user_id, "PostSessionAnalysis", latency_ms=500)

    detail = client.get(
        f"/webhooks/admin/user/{user_id}?days=30", headers=ADMIN
    ).json()
    buckets = {c["call_type"]: c for c in detail["by_call_type"]}

    assert buckets["consolidation"]["requests"] == 2
    assert buckets["consolidation"]["avg_latency_ms"] == 200
    assert buckets["analysis"]["requests"] == 1
    assert buckets["analysis"]["avg_latency_ms"] == 500
