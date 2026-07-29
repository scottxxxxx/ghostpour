"""`by_call_type` on the admin dashboard.

The Models tab used to offer only `by_scenario`, which is a Tech Rehearsal
sub-dimension: Shoulder Surf never tags it, so an SS-filtered view collapsed
every request into one "(untagged)" row and looked like missing data. Every
usage_log row carries a call_type, so that is the breakdown that answers
"what are these requests?" for both apps.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _insert(db_path, user_id, app_id, call_type, *, cost=0.01, latency=100):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, call_type, app_id)
           VALUES (?, ?, 'anthropic', 'claude-haiku-4-5', 100, 50, ?, ?, ?,
                   'success', ?, ?)""",
        (str(uuid.uuid4()), user_id, cost, now, latency, call_type, app_id),
    )
    conn.commit()
    conn.close()


def _by_ct(payload):
    return {r["call_type"]: r for r in payload["by_call_type"]}


def test_call_type_breakdown_splits_requests(client, tmp_db_path):
    from tests.conftest import _insert_user

    _insert_user(tmp_db_path, user_id="ct-user", tier="pro", monthly_limit=5.0)
    for _ in range(3):
        _insert(tmp_db_path, "ct-user", "shouldersurf", "query")
    _insert(tmp_db_path, "ct-user", "shouldersurf", "report")
    _insert(tmp_db_path, "ct-user", "techrehearsal", "tr_parse_jd")

    payload = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()
    rows = _by_ct(payload)
    assert rows["query"]["requests"] == 3
    assert rows["report"]["requests"] == 1
    assert rows["tr_parse_jd"]["requests"] == 1
    # tokens are input + output, cost is summed, latency averaged
    assert rows["query"]["tokens"] == 450
    assert rows["query"]["cost_usd"] == 0.03
    assert rows["query"]["avg_latency_ms"] == 100
    # ordered by request count, busiest first
    assert payload["by_call_type"][0]["call_type"] == "query"


def test_call_type_breakdown_honors_app_filter(client, tmp_db_path):
    from tests.conftest import _insert_user

    _insert_user(tmp_db_path, user_id="ct-user2", tier="pro", monthly_limit=5.0)
    _insert(tmp_db_path, "ct-user2", "shouldersurf", "query")
    _insert(tmp_db_path, "ct-user2", "techrehearsal", "tr_parse_jd")

    ss = client.get(
        "/webhooks/admin/dashboard?days=7&app=shouldersurf", headers=ADMIN
    ).json()
    assert set(_by_ct(ss)) == {"query"}

    tr = client.get(
        "/webhooks/admin/dashboard?days=7&app=techrehearsal", headers=ADMIN
    ).json()
    assert set(_by_ct(tr)) == {"tr_parse_jd"}


def test_untyped_rows_bucket_rather_than_vanish(client, tmp_db_path):
    from tests.conftest import _insert_user

    _insert_user(tmp_db_path, user_id="ct-user3", tier="pro", monthly_limit=5.0)
    _insert(tmp_db_path, "ct-user3", "shouldersurf", None)

    payload = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()
    assert _by_ct(payload)["(untyped)"]["requests"] == 1
