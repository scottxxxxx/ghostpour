"""Who is on Apple Cloud (Scott, 2026-11-02). The meetings-by-model chart
showed a total and nothing named the people behind it. The Subscriptions tab
now does, through /webhooks/admin/subscriptions/apple-cloud-users: use from
telemetry_events, the pack from the purchases table, and anyone who holds the
pack without a meeting in the window listed beside them.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import _insert_user

PCC = "onDevice/private-cloud-compute"
ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/subscriptions/apple-cloud-users"


def _ago(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _user(db, excluded=False):
    uid = "u-" + uuid.uuid4().hex[:8]
    _insert_user(db, user_id=uid, tier="free", monthly_limit=2.0)
    if excluded:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET exclude_from_stats = 1 WHERE id = ?", (uid,))
        conn.commit()
        conn.close()
    return uid


def _meeting(db, user_id, model=PCC, device="dev-1", event="meeting_start", days_ago=0, resumed_from=None):
    mid = "m-" + uuid.uuid4().hex[:8]
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO telemetry_events (id, event_type, device_id, user_id, meeting_id, model_id, "
        "received_at, app_id, resumed_from_meeting_id) VALUES (?, ?, ?, ?, ?, ?, ?, 'shouldersurf', ?)",
        (uuid.uuid4().hex, event, device, user_id, mid, model, _ago(days_ago), resumed_from))
    conn.commit()
    conn.close()
    return mid


def _pack(db, user_id, grants=("unlock_apple_cloud",), status="granted", revoked=None, env="Production"):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO purchases (transaction_id, user_id, product_id, status, grants, environment, "
        "purchased_at, recorded_at, revoked_at, source) VALUES (?, ?, 'p', ?, ?, ?, ?, ?, ?, 'verify_receipt')",
        (uuid.uuid4().hex, user_id, status, json.dumps(list(grants)), env, _ago(), _ago(), revoked))
    conn.commit()
    conn.close()


def _get(client):
    r = client.get(URL, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_names_the_user_and_counts_their_apple_cloud_meetings_only(client, tmp_db_path):
    uid = _user(tmp_db_path)
    _meeting(tmp_db_path, uid)
    _meeting(tmp_db_path, uid)
    _meeting(tmp_db_path, uid, model="onDevice/foundation-models")
    d = _get(client)
    assert [(u["user_id"], u["meetings"]) for u in d["users"]] == [(uid, 2)]
    assert d["meetings"] == 2


def test_a_user_only_on_other_models_is_not_listed(client, tmp_db_path):
    _meeting(tmp_db_path, _user(tmp_db_path), model="cloudzap/auto")
    assert _get(client)["users"] == []


def test_a_stop_event_is_not_a_second_meeting(client, tmp_db_path):
    uid = _user(tmp_db_path)
    _meeting(tmp_db_path, uid)
    _meeting(tmp_db_path, uid, event="meeting_stop")
    assert _get(client)["users"][0]["meetings"] == 1


def test_the_orphan_half_of_a_resumed_meeting_is_not_counted(client, tmp_db_path):
    uid = _user(tmp_db_path)
    orphan = _meeting(tmp_db_path, uid)
    _meeting(tmp_db_path, uid, resumed_from=orphan)
    assert _get(client)["users"][0]["meetings"] == 1


def test_a_meeting_past_the_window_is_not_counted(client, tmp_db_path):
    _meeting(tmp_db_path, _user(tmp_db_path), days_ago=31)
    assert _get(client)["users"] == []


def test_signed_out_meetings_group_by_device(client, tmp_db_path):
    _meeting(tmp_db_path, None, device="dev-a")
    _meeting(tmp_db_path, None, device="dev-a")
    _meeting(tmp_db_path, None, device="dev-b")
    got = sorted((u["device_id"], u["meetings"], u["user_id"]) for u in _get(client)["users"])
    assert got == [("dev-a", 2, None), ("dev-b", 1, None)]


def test_a_stats_excluded_account_is_left_out_of_both_lists(client, tmp_db_path):
    used, bought = _user(tmp_db_path, excluded=True), _user(tmp_db_path, excluded=True)
    _meeting(tmp_db_path, used)
    _pack(tmp_db_path, bought)
    d = _get(client)
    assert d["users"] == [] and d["bought_not_used"] == []


def test_owning_the_pack_is_marked_on_a_user(client, tmp_db_path):
    owner, other = _user(tmp_db_path), _user(tmp_db_path)
    _pack(tmp_db_path, owner)
    _meeting(tmp_db_path, owner)
    _meeting(tmp_db_path, other)
    by = {u["user_id"]: u["owns_pack"] for u in _get(client)["users"]}
    assert by == {owner: True, other: False}


def test_bought_and_unused_lists_only_a_live_apple_cloud_grant(client, tmp_db_path):
    unused, bundle, used, byok, refunded, pending = (_user(tmp_db_path) for _ in range(6))
    _pack(tmp_db_path, unused)
    _pack(tmp_db_path, bundle, grants=("unlock_apple_cloud", "unlock_byok"))
    _pack(tmp_db_path, used)
    _meeting(tmp_db_path, used)
    _pack(tmp_db_path, byok, grants=("unlock_byok",))
    _pack(tmp_db_path, refunded, revoked=_ago())
    _pack(tmp_db_path, pending, status="pending", grants=())
    got = {o["user_id"] for o in _get(client)["bought_not_used"]}
    assert got == {unused, bundle}


def test_the_window_cannot_ask_past_what_the_events_keep(client):
    assert client.get(URL + "?days=31", headers=ADMIN).status_code == 422


def test_needs_the_admin_key(client):
    assert client.get(URL, headers={"X-Admin-Key": "nope"}).status_code == 403
