"""GET /webhooks/admin/users: the per-user numbers, pinned value by value.

Written first against the correlated-subquery implementation (the oracle),
then kept unchanged through the grouped-aggregate rewrite (2026-11-01;
receipt: the live list took 15.8 s and 16.9 s after a restart and 2 to 3 s
warm on 2026-10-31). Every field the SELECT derives is asserted with a
literal, so a rewrite that changes a filter, a window, a sum or the
latest-row rule fails here naming the field.

The seed is deliberately uneven:

- u_both: usage in and out of the 7-day window across two apps, one error
  row that is the NEWEST row (last_request ignores status; nothing else
  does), metadata with generated.count and documents.count, image_count,
  one translation per app, three telemetry pings with holes so the
  per-field latest-non-null rule is exercised, a meeting start and stop.
- u_tel: telemetry only, under one app, with two subscription events so
  "latest environment" and "latest offer" are both tested.
- u_none: a row in users and nothing else.
- Two anonymous devices (telemetry with no user_id), one newer than the
  other, so the anonymous half and its ordering are pinned too.

Ties on timestamps are not seeded. The original ORDER BY ... LIMIT 1 and
the users ORDER BY created_at leave ties unspecified, and this test pins
only behaviour the old code actually defined.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/users"

NOW = datetime.now(timezone.utc)


def _ts(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


# Timestamps the assertions refer back to by name.
T_BOTH_ERROR = _ts(hours=1)          # newest usage row of u_both, status error
T_BOTH_R1 = _ts(days=1)              # ss, success, in window
T_BOTH_R2 = _ts(days=2)              # tr, success, in window, translation
T_BOTH_R4 = _ts(days=20)             # ss, success, out of the 7-day window
T_BOTH_R5 = _ts(days=30)             # tr, success, translation, no metadata
T_TEL_1 = _ts(days=3)
T_TEL_2 = _ts(days=1)
T_TEL_3 = _ts(hours=12)
T_UTEL_1 = _ts(days=5)
T_UTEL_2 = _ts(days=2)
T_ANON_NEW_1 = _ts(days=4)
T_ANON_NEW_2 = _ts(hours=6)
T_ANON_OLD = _ts(days=9)


def _usage(conn, user_id, app_id, ts, *, status="success", tokens=(0, 0),
           cost=None, images=0, call_type="chat", metadata=None):
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms, status,
            call_type, image_count, metadata, app_id)
           VALUES (?, ?, 'anthropic', 'm', ?, ?, ?, ?, 100, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, tokens[0], tokens[1], cost, ts, status,
         call_type, images, json.dumps(metadata) if metadata is not None else None,
         app_id),
    )


def _ping(conn, device_id, user_id, ts, *, event="app_start", app_id=None,
          duration=None, **fields):
    cols = ["id", "event_type", "device_id", "user_id", "received_at", "app_id",
            "duration_seconds", *fields]
    vals = [str(uuid.uuid4()), event, device_id, user_id, ts, app_id, duration,
            *fields.values()]
    conn.execute(
        f"INSERT INTO telemetry_events ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(vals))})", vals)


def _sub_event(conn, user_id, recorded_at, *, environment=None, offer_id=None):
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, environment, source, effective_at,
            recorded_at, offer_id)
           VALUES (?, ?, 'subscribed', ?, 'assn', ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, environment, recorded_at, recorded_at,
         offer_id),
    )


def _seed(db_path: str) -> None:
    for uid, created in (("u_both", "2026-10-01T00:00:00+00:00"),
                         ("u_tel", "2026-10-02T00:00:00+00:00"),
                         ("u_none", "2026-10-03T00:00:00+00:00")):
        _insert_user(db_path, user_id=uid, tier="pro", monthly_limit=5.0)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (created, uid))
        conn.commit()
        conn.close()

    conn = sqlite3.connect(db_path)

    # u_both: usage_log
    _usage(conn, "u_both", "shouldersurf", T_BOTH_R1, tokens=(100, 50), cost=0.01,
           images=2, metadata={"generated": {"count": 3}, "documents": {"count": 1}})
    _usage(conn, "u_both", "techrehearsal", T_BOTH_R2, tokens=(200, 70), cost=0.02,
           call_type="translation", metadata={"documents": {"count": 2}})
    # The error row is the newest row and carries big numbers in every
    # column: it must reach last_request and nothing else.
    _usage(conn, "u_both", "shouldersurf", T_BOTH_ERROR, status="error",
           tokens=(999, 999), cost=0.5, images=5,
           metadata={"generated": {"count": 9}, "documents": {"count": 9}})
    _usage(conn, "u_both", "shouldersurf", T_BOTH_R4, tokens=(1000, 500), cost=0.1,
           images=1, metadata={"generated": {"count": 2}, "documents": {"count": 3}})
    _usage(conn, "u_both", "techrehearsal", T_BOTH_R5, tokens=(10, 5), cost=0.001,
           call_type="translation")

    # u_both: telemetry. The middle ping drops app_locale, device_model and
    # city; the newest ping is a meeting_stop carrying only os_version.
    _ping(conn, "dev-both", "u_both", T_TEL_1, app_id="shouldersurf",
          app_locale="en", os_version="18.0", app_version="1.17",
          device_model="TestPhone1,1", app_build="100", distribution="sandbox",
          country="US", region="CA", city="San Jose")
    _ping(conn, "dev-both", "u_both", T_TEL_2, event="meeting_start",
          app_id="shouldersurf", os_version="18.1", app_version="1.18",
          app_build="101", distribution="production", country="US", region="CA")
    _ping(conn, "dev-both", "u_both", T_TEL_3, event="meeting_stop",
          app_id="shouldersurf", duration=1800, os_version="18.1")
    _sub_event(conn, "u_both", _ts(days=10), environment="Sandbox")

    # u_tel: two pings under techrehearsal, no usage_log at all.
    _ping(conn, "dev-tel", "u_tel", T_UTEL_1, app_id="techrehearsal",
          app_locale="de", os_version="17.5", app_version="1.16",
          device_model="TestPad2,2", app_build="90",
          country="DE", region="BE", city="Berlin")
    _ping(conn, "dev-tel", "u_tel", T_UTEL_2, app_id="techrehearsal",
          os_version="17.6")
    _sub_event(conn, "u_tel", _ts(days=40), environment="Production",
               offer_id="offer-old")
    _sub_event(conn, "u_tel", _ts(days=8), environment="Sandbox",
               offer_id="offer-new")

    # Anonymous devices: never pinged with a user_id.
    _ping(conn, "anon-new", None, T_ANON_NEW_1, app_locale="fr",
          device_model="TestPad3,3", country="FR", region="IDF", city="Paris",
          app_build="7")
    _ping(conn, "anon-new", None, T_ANON_NEW_2, os_version="18.1",
          app_version="1.18")
    _ping(conn, "anon-old", None, T_ANON_OLD, app_locale="es", country="MX")

    conn.commit()
    conn.close()


def _get(client, **params):
    r = client.get(URL, params={"days": 7, **params}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _row(payload, uid):
    return next(u for u in payload["users"] if u["id"] == uid)


def _subset(row, expected):
    """The fields under test, so a mismatch names the field in the diff."""
    return {k: row[k] for k in expected}


U_BOTH_ALL_APPS = {
    "window_requests": 2,
    "window_input_tokens": 300,
    "window_output_tokens": 120,
    "window_tokens": 420,
    "window_cost_usd": 0.03,
    "window_files_generated": 3,
    "window_documents": 3,
    "window_photos": 2,
    "window_translations": 1,
    "lifetime_requests": 4,
    "lifetime_input_tokens": 1310,
    "lifetime_output_tokens": 625,
    "lifetime_tokens": 1935,
    "lifetime_cost_usd": 0.131,
    "lifetime_files_generated": 5,
    "lifetime_documents": 6,
    "lifetime_photos": 3,
    "lifetime_translations": 2,
    "last_request": T_BOTH_ERROR,
    # latest non-null per field, across pings with holes
    "language": "en",
    "ios_version": "18.1",
    "app_version": "1.18",
    "app_build": "101",
    "device": "Unknown (TestPhone1,1)",
    "channel": "appstore",
    "telemetry_events": 3,
    "location": {"country": "US", "region": "CA", "city": "San Jose"},
    "meeting_hours": 0.5,
    "meeting_count": 1,
    "meetings_unfinished": 0,
    "subscription_offer_id": None,
}

U_TEL_ALL_APPS = {
    "window_requests": 0,
    "window_input_tokens": 0,
    "window_output_tokens": 0,
    "window_tokens": 0,
    "window_cost_usd": 0,
    "window_files_generated": 0,
    "window_documents": 0,
    "window_photos": 0,
    "window_translations": 0,
    "lifetime_requests": 0,
    "lifetime_input_tokens": 0,
    "lifetime_output_tokens": 0,
    "lifetime_tokens": 0,
    "lifetime_cost_usd": 0,
    "lifetime_files_generated": 0,
    "lifetime_documents": 0,
    "lifetime_photos": 0,
    "lifetime_translations": 0,
    "last_request": None,
    "language": "de",
    "ios_version": "17.6",
    "app_version": "1.16",
    "app_build": "90",
    "device": "Unknown (TestPad2,2)",
    # no telemetry distribution, so the StoreKit environment decides, and
    # the LATEST event's Sandbox wins over the older Production
    "channel": "testflight",
    "telemetry_events": 2,
    "location": {"country": "DE", "region": "BE", "city": "Berlin"},
    "meeting_hours": 0.0,
    "meeting_count": 0,
    "meetings_unfinished": 0,
    "subscription_offer_id": "offer-new",
}

U_NONE = {
    **{k: v for k, v in U_TEL_ALL_APPS.items()
       if k.startswith(("window_", "lifetime_")) or k.startswith("meeting")},
    "last_request": None,
    "language": None,
    "ios_version": None,
    "app_version": None,
    "app_build": None,
    "device": None,
    "channel": None,
    "telemetry_events": 0,
    "location": None,
    "subscription_offer_id": None,
}


def test_all_apps_values_and_ordering(client, tmp_db_path):
    _seed(tmp_db_path)
    body = _get(client)

    assert [u["id"] for u in body["users"]] == ["u_none", "u_tel", "u_both"]
    assert body["count"] == 3
    assert _subset(_row(body, "u_both"), U_BOTH_ALL_APPS) == U_BOTH_ALL_APPS
    assert _subset(_row(body, "u_tel"), U_TEL_ALL_APPS) == U_TEL_ALL_APPS
    assert _subset(_row(body, "u_none"), U_NONE) == U_NONE


def test_window_param_moves_the_window(client, tmp_db_path):
    _seed(tmp_db_path)
    row = _row(_get(client, days=30), "u_both")
    # Both older rows join the window, INCLUDING the one stamped exactly 30
    # days before now: the window starts at date('now', '-30 days'), which
    # is midnight UTC of that day, not now minus 30 days. A rewrite that
    # moved to datetime('now', ...) would lose that row and fail here.
    expected = {
        "window_requests": 4,
        "window_input_tokens": 1310,
        "window_output_tokens": 625,
        "window_cost_usd": 0.131,
        "window_files_generated": 5,
        "window_documents": 6,
        "window_photos": 3,
        "window_translations": 2,
        "lifetime_requests": 4,
        "last_request": T_BOTH_ERROR,
    }
    assert _subset(row, expected) == expected


def test_app_filter_scopes_usage_and_hides_inactive_users(client, tmp_db_path):
    _seed(tmp_db_path)

    ss = _get(client, app="shouldersurf")
    assert [u["id"] for u in ss["users"]] == ["u_both"]
    assert ss["count"] == 1
    expected_ss = {
        "window_requests": 1,
        "window_input_tokens": 100,
        "window_output_tokens": 50,
        "window_cost_usd": 0.01,
        "window_files_generated": 3,
        "window_documents": 1,
        "window_photos": 2,
        "window_translations": 0,
        "lifetime_requests": 2,
        "lifetime_input_tokens": 1100,
        "lifetime_output_tokens": 550,
        "lifetime_cost_usd": 0.11,
        "lifetime_files_generated": 5,
        "lifetime_documents": 4,
        "lifetime_photos": 3,
        "lifetime_translations": 0,
        "last_request": T_BOTH_ERROR,
        # device fields stay unscoped
        "language": "en",
        "ios_version": "18.1",
        "device": "Unknown (TestPhone1,1)",
        "telemetry_events": 3,
        "location": {"country": "US", "region": "CA", "city": "San Jose"},
    }
    assert _subset(_row(ss, "u_both"), expected_ss) == expected_ss

    tr = _get(client, app="techrehearsal")
    # u_tel has only telemetry under techrehearsal and is still listed;
    # u_none has nothing anywhere and is hidden.
    assert [u["id"] for u in tr["users"]] == ["u_tel", "u_both"]
    expected_tr = {
        "window_requests": 1,
        "window_input_tokens": 200,
        "window_output_tokens": 70,
        "window_cost_usd": 0.02,
        "window_files_generated": 0,
        "window_documents": 2,
        "window_photos": 0,
        "window_translations": 1,
        "lifetime_requests": 2,
        "lifetime_input_tokens": 210,
        "lifetime_output_tokens": 75,
        "lifetime_cost_usd": 0.021,
        "lifetime_files_generated": 0,
        "lifetime_documents": 2,
        "lifetime_photos": 0,
        "lifetime_translations": 2,
        # the newest tr row, not the newer ss error row
        "last_request": T_BOTH_R2,
        "language": "en",
        "telemetry_events": 3,
    }
    assert _subset(_row(tr, "u_both"), expected_tr) == expected_tr
    assert _subset(_row(tr, "u_tel"), U_TEL_ALL_APPS) == U_TEL_ALL_APPS

    # Two apps at once: the union of both scopes.
    both = _get(client, app="shouldersurf,techrehearsal")
    assert [u["id"] for u in both["users"]] == ["u_tel", "u_both"]
    assert _subset(_row(both, "u_both"), U_BOTH_ALL_APPS) == U_BOTH_ALL_APPS


def test_anonymous_devices_are_unscoped_and_newest_first(client, tmp_db_path):
    _seed(tmp_db_path)
    expected = [
        {
            "device_id": "anon-new",
            "language": "fr",
            "ios_version": "18.1",
            "app_version": "1.18",
            "app_build": "7",
            "telemetry_events": 2,
            "device": "Unknown (TestPad3,3)",
            "last_request": T_ANON_NEW_2,
            "location": {"country": "FR", "region": "IDF", "city": "Paris"},
        },
        {
            "device_id": "anon-old",
            "language": "es",
            "ios_version": None,
            "app_version": None,
            "app_build": None,
            "telemetry_events": 1,
            "device": None,
            "last_request": T_ANON_OLD,
            "location": {"country": "MX", "region": None, "city": None},
        },
    ]
    assert _get(client)["anonymous_devices"] == expected
    assert _get(client, app="shouldersurf")["anonymous_devices"] == expected


def test_usage_rollup_never_opens_a_usage_log_row(client, tmp_db_path):
    """The whole point of the rewrite, pinned at the plan.

    The usage roll-up must be answered from idx_usage_user_rollup as a
    COVERING index: a column added to the roll-up that the index lacks, or
    a json_extract written differently from the index's expression, makes
    SQLite open every row and read the 24 KB metadata blob again, which is
    the 16 s this exists to remove. Checked with and without an app filter.
    No prior query-plan test existed in this suite; this is the first.
    """
    from app.routers.webhooks import _list_users_sql

    _seed(tmp_db_path)
    conn = sqlite3.connect(tmp_db_path)
    for apps in ((), ("shouldersurf",), ("shouldersurf", "techrehearsal")):
        sql, params = _list_users_sql(apps, 7)
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
        usage_steps = [p for p in plan if " l " in f" {p} " or p.endswith(" l")]
        assert usage_steps == ["SCAN l USING COVERING INDEX idx_usage_user_rollup"], \
            (apps, plan)
        # and the statement is the one the endpoint runs: it executes
        assert conn.execute(sql, params).fetchall()
    conn.close()
