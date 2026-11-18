"""CTA to purchase panel (Scott, 2026-11-18). A trial or purchase is credited
to a CTA when that user tapped one in the 30 minutes before the start; an
impression alone in the 24 hours before is 'saw_gate'; otherwise 'no_cta'.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from app.services.cta_purchases import attribute, label
from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/subscriptions/cta-purchases"
T = datetime(2026, 11, 17, 9, 18, 17, tzinfo=timezone.utc)


def test_a_tap_seconds_before_the_start_is_after_tap():
    ev = [(T - timedelta(seconds=13), "click", "memory_tab (context_quilt)")]
    assert attribute(T, ev) == ("after_tap", "memory_tab (context_quilt)")


def test_a_tap_older_than_30_min_falls_back_to_seen_or_none():
    old = T - timedelta(minutes=31)
    assert attribute(T, [(old, "click", "x")]) == ("no_cta", None)
    assert attribute(T, [(old, "click", "x"), (old, "impression", "x")]) == ("saw_gate", "x")


def test_events_after_the_start_never_count():
    assert attribute(T, [(T + timedelta(seconds=5), "click", "x")]) == ("no_cta", None)


def test_label_prefers_campaign_then_surface():
    assert label("c1", "memory_tab", "context_quilt") == "campaign c1"
    assert label(None, "memory_tab", "context_quilt") == "memory_tab (context_quilt)"
    assert label(None, None, None) == "unlabeled"


def _iso(dt):
    return dt.isoformat()


def _seed(db, start=None, events=(), paid=False, excluded=False, env="Production"):
    uid = "u-" + uuid.uuid4().hex[:8]
    _insert_user(db, user_id=uid, tier="free", monthly_limit=2.0)
    c = sqlite3.connect(db)
    c.execute("UPDATE users SET exclude_from_stats = ? WHERE id = ?", (int(excluded), uid))
    for when, et in events:
        c.execute("INSERT INTO promo_events (id, created_at, device_id, user_id, app_id, event_type, surface, feature) "
                  "VALUES (?, ?, 'dev-' || ?, ?, 'shouldersurf', ?, 'memory_tab', 'context_quilt')",
                  (uuid.uuid4().hex, _iso(when), uid, uid, et))
    if start:
        c.execute("INSERT INTO subscription_events (id, user_id, event_type, environment, effective_at, recorded_at) "
                  "VALUES (?, ?, 'subscribed', ?, ?, ?)", (uuid.uuid4().hex, uid, env, _iso(start), _iso(start)))
        c.execute("INSERT INTO subscription_status (original_transaction_id, user_id, environment, paid_ever, checked_at, source) "
                  "VALUES (?, ?, ?, ?, ?, 'apple_status')", (uuid.uuid4().hex, uid, env, int(paid), _iso(start)))
    c.commit()
    c.close()


def test_the_endpoint_credits_the_tap_and_counts_the_funnel(client, tmp_db_path):
    s = datetime.now(timezone.utc) - timedelta(days=1)
    _seed(tmp_db_path, s, [(s - timedelta(seconds=15), "impression"), (s - timedelta(seconds=13), "click")], paid=True)
    _seed(tmp_db_path, s, [(s - timedelta(hours=2), "impression")])
    _seed(tmp_db_path, s, [])
    _seed(tmp_db_path, None, [(s, "impression")])
    _seed(tmp_db_path, s, [(s - timedelta(seconds=5), "click")], excluded=True)
    _seed(tmp_db_path, s, [(s - timedelta(seconds=5), "click")], env="Sandbox")
    r = client.get(URL, headers=ADMIN)
    assert r.status_code == 200, r.text
    d = r.json()
    t = d["totals"]
    assert (t["after_tap"], t["saw_gate"], t["no_cta"]) == (
        {"starts": 1, "paid": 1}, {"starts": 1, "paid": 0}, {"starts": 1, "paid": 0}), t
    (c,) = d["ctas"]
    assert c["cta"] == "memory_tab (context_quilt)"
    assert (c["impressions"], c["clicks"], c["viewers"], c["tappers"]) == (3, 2, 3, 2)
    assert (c["starts_after_tap"], c["paid_after_tap"]) == (1, 1)


def test_the_endpoint_needs_the_admin_key(client):
    assert client.get(URL, headers={"X-Admin-Key": "nope"}).status_code in (401, 403)
