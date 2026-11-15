"""Signed-out meeting time on the dashboard (Scott, 2026-11-15).

Steven recorded meetings signed out, so the pings carried his phone's
device_id and no user_id, and his Users row showed 0 min. A device that ever
pinged signed in credits its signed-out time to the LAST account seen on it,
in its own fields beside the signed-in ones. A device never signed in shows
its time on its anonymous row.
"""
from __future__ import annotations

import sqlite3

from tests.conftest import _insert_user
from tests.test_admin_users_query import ADMIN, URL, _ping, _ts


def _users(client):
    r = client.get(URL, params={"days": 7}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _row(p, uid):
    return next(u for u in p["users"] if u["id"] == uid)


def _seed(db):
    for uid in ("steven", "wife", "other"):
        _insert_user(db, user_id=uid, tier="pro", monthly_limit=5.0)
    c = sqlite3.connect(db)
    # Steven's phone: signed in once long ago, then signed out meetings.
    _ping(c, "phone-s", "steven", _ts(days=40))
    _ping(c, "phone-s", None, _ts(days=3), event="meeting_start")
    _ping(c, "phone-s", None, _ts(days=3), event="meeting_stop", duration=3600)
    _ping(c, "phone-s", None, _ts(days=1), event="meeting_start")  # never stopped
    # Out of the 7-day window: never counted.
    _ping(c, "phone-s", None, _ts(days=20), event="meeting_stop", duration=9999)
    # A signed-in meeting stays in the signed-in column only.
    _ping(c, "phone-s", "steven", _ts(days=2), event="meeting_start")
    _ping(c, "phone-s", "steven", _ts(days=2), event="meeting_stop", duration=1800)
    # Shared phone: last signed in by "wife", so her row gets it.
    _ping(c, "phone-shared", "other", _ts(days=30))
    _ping(c, "phone-shared", "wife", _ts(days=10))
    _ping(c, "phone-shared", None, _ts(days=2), event="meeting_start")
    _ping(c, "phone-shared", None, _ts(days=2), event="meeting_stop", duration=900)
    # Never signed in.
    _ping(c, "phone-anon", None, _ts(days=2), event="meeting_start")
    _ping(c, "phone-anon", None, _ts(days=2), event="meeting_stop", duration=1200)
    c.commit(); c.close()


def test_signed_out_time_is_credited_to_the_phones_account(client, tmp_db_path):
    _seed(tmp_db_path)
    s = _row(_users(client), "steven")
    assert (s["meeting_hours"], s["meeting_count"]) == (0.5, 1)
    assert (s["signed_out_meeting_hours"], s["signed_out_meeting_count"],
            s["signed_out_meetings_unfinished"]) == (1.0, 2, 1)


def test_a_shared_phone_credits_the_last_account_signed_in(client, tmp_db_path):
    _seed(tmp_db_path)
    p = _users(client)
    assert _row(p, "wife")["signed_out_meeting_hours"] == 0.25
    assert _row(p, "other")["signed_out_meeting_count"] == 0


def test_a_never_signed_in_phone_shows_its_time_on_its_own_row(client, tmp_db_path):
    _seed(tmp_db_path)
    anon = {a["device_id"]: a for a in _users(client)["anonymous_devices"]}
    assert set(anon) == {"phone-anon"}
    assert (anon["phone-anon"]["meeting_hours"], anon["phone-anon"]["meeting_count"]) == (0.33, 1)
