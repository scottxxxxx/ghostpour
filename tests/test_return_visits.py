"""Return visits panel (Scott, 2026-11-18). A new Shoulder Surf user
"returned" when a second meeting lands on a LATER UTC day within 72 hours of
the first. Same-day meetings never count, a first meeting under 72 hours old
is pending rather than a miss, and paid comes from Apple's paid_ever.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from app.services.return_visits import classify
from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/subscriptions/return-visits"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def test_a_second_meeting_the_same_day_is_not_a_return():
    a = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    assert classify([a, a + timedelta(hours=8)], NOW) == "not_returned"


def test_a_meeting_the_next_day_inside_72h_is_a_return():
    a = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
    assert classify([a, a + timedelta(hours=3)], NOW) == "returned"


def test_a_meeting_after_72h_is_not_a_return():
    a = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    assert classify([a, a + timedelta(hours=73)], NOW) == "not_returned"


def test_a_first_meeting_under_72h_old_is_pending_not_a_miss():
    a = NOW - timedelta(hours=10)
    assert classify([a], NOW) == "pending"
    assert classify([], NOW) == "no_meeting"


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _seed(db, created, meetings, live=0, paid=None, excluded=False, app="shouldersurf"):
    uid = "u-" + uuid.uuid4().hex[:8]
    _insert_user(db, user_id=uid, tier="free", monthly_limit=2.0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET created_at = ?, exclude_from_stats = ? WHERE id = ?",
                 (_ts(created), int(excluded), uid))
    rows = [(m, "summary") for m in meetings] + [(created + timedelta(minutes=5), "query")] * live
    for when, ct in rows:
        conn.execute(
            "INSERT INTO usage_log (id, user_id, provider, model, request_timestamp, status, call_type, app_id) "
            "VALUES (?, ?, 'anthropic', 'm', ?, 'success', ?, ?)",
            (uuid.uuid4().hex, uid, _ts(when), ct, app))
    if paid is not None:
        conn.execute(
            "INSERT INTO subscription_status (original_transaction_id, user_id, environment, paid_ever, checked_at, source) "
            "VALUES (?, ?, 'Production', ?, ?, 'apple_status')", (uuid.uuid4().hex, uid, int(paid), _ts(created)))
    conn.commit()
    conn.close()
    return uid


def test_the_endpoint_counts_by_signup_week(client, tmp_db_path):
    now = datetime.now(timezone.utc)
    base = (now - timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0)
    _seed(tmp_db_path, base, [base + timedelta(hours=1), base + timedelta(hours=26)], paid=True)
    _seed(tmp_db_path, base, [base + timedelta(hours=1), base + timedelta(hours=5)], paid=False)
    _seed(tmp_db_path, base, [], live=3)
    _seed(tmp_db_path, base, [base, base + timedelta(hours=25)], excluded=True)
    _seed(tmp_db_path, base, [base, base + timedelta(hours=25)], app="n400")
    r = client.get(URL, headers=ADMIN)
    assert r.status_code == 200, r.text
    d = r.json()
    states = sorted(u["state"] for u in d["users"])
    assert states == ["no_meeting", "not_returned", "returned"], states
    w = d["weeks"]
    assert sum(x["users"] for x in w) == 3
    assert sum(x["returned"] for x in w) == 1 and sum(x["not_returned"] for x in w) == 1
    assert sum(x["subscribed"] for x in w) == 2 and sum(x["paid"] for x in w) == 1
    assert sum(x["paid_returned"] for x in w) == 1


def test_the_endpoint_needs_the_admin_key(client):
    assert client.get(URL, headers={"X-Admin-Key": "nope"}).status_code in (401, 403)
