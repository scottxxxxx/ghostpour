"""Per-app filter on the admin dashboard endpoints (Phase A3).

The dashboard, users, errors, user-detail and telemetry/rich endpoints all
accept an optional `app` query param. Empty/absent means "all apps"; when set,
every usage_log- (or telemetry_events-) derived metric is scoped to that
app_id. These tests seed rows tagged with distinct app_ids and assert the
filter narrows the result.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _insert_usage(
    db_path: str,
    user_id: str,
    app_id: str,
    *,
    status: str = "success",
    cost: float = 0.01,
    provider: str = "anthropic",
    model: str = "claude-haiku-4-5",
):
    """Insert one usage_log row tagged with an app_id (recent timestamp)."""
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, error_message, call_type, app_id)
           VALUES (?, ?, ?, ?, 100, 50, ?, ?, 120, ?, ?, 'chat', ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            provider,
            model,
            cost,
            now,
            status,
            "boom" if status != "success" else None,
            app_id,
        ),
    )
    conn.commit()
    conn.close()


def _seed_two_apps(client, tmp_db_path):
    """One user; 3 shouldersurf + 2 techrehearsal successful rows."""
    from tests.conftest import _insert_user

    user_id = "app-filter-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="pro", monthly_limit=5.10)
    for _ in range(3):
        _insert_usage(tmp_db_path, user_id, "shouldersurf")
    for _ in range(2):
        _insert_usage(tmp_db_path, user_id, "techrehearsal")
    return user_id


def test_dashboard_app_filter_narrows_totals(client, tmp_db_path):
    _seed_two_apps(client, tmp_db_path)

    all_apps = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()
    tr = client.get(
        "/webhooks/admin/dashboard?days=7&app=techrehearsal", headers=ADMIN
    ).json()
    ss = client.get(
        "/webhooks/admin/dashboard?days=7&app=shouldersurf", headers=ADMIN
    ).json()

    assert all_apps["usage"]["total_requests"] == 5
    assert tr["usage"]["total_requests"] == 2
    assert ss["usage"]["total_requests"] == 3
    # Empty app param behaves like "all apps", not like a literal app_id.
    empty = client.get(
        "/webhooks/admin/dashboard?days=7&app=", headers=ADMIN
    ).json()
    assert empty["usage"]["total_requests"] == 5


def test_users_window_requests_respect_app_filter(client, tmp_db_path):
    user_id = _seed_two_apps(client, tmp_db_path)

    all_apps = client.get("/webhooks/admin/users?days=7", headers=ADMIN).json()
    tr = client.get(
        "/webhooks/admin/users?days=7&app=techrehearsal", headers=ADMIN
    ).json()

    def _row(payload):
        return next(u for u in payload["users"] if u["id"] == user_id)

    # Windowed and lifetime counts both scope to the selected app.
    assert _row(all_apps)["window_requests"] == 5
    assert _row(all_apps)["lifetime_requests"] == 5
    assert _row(tr)["window_requests"] == 2
    assert _row(tr)["lifetime_requests"] == 2


def _insert_telemetry(db_path, user_id, app_id):
    """Insert one telemetry_events row tagged with an app_id."""
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO telemetry_events
           (id, event_type, device_id, user_id, received_at, app_id)
           VALUES (?, 'app_start', ?, ?, ?, ?)""",
        (str(uuid.uuid4()), str(uuid.uuid4()), user_id, now, app_id),
    )
    conn.commit()
    conn.close()


def test_users_list_hides_users_without_app_activity(client, tmp_db_path):
    """Filtering by an app shows only users with activity in that app.
    Activity = a usage_log OR a telemetry_events row tagged with the app_id."""
    from tests.conftest import _insert_user

    _insert_user(tmp_db_path, user_id="ss-only", tier="pro", monthly_limit=5.10)
    _insert_user(tmp_db_path, user_id="tr-only", tier="pro", monthly_limit=5.10)
    _insert_user(tmp_db_path, user_id="tel-only", tier="pro", monthly_limit=5.10)
    _insert_usage(tmp_db_path, "ss-only", "shouldersurf")
    _insert_usage(tmp_db_path, "tr-only", "techrehearsal")
    # tel-only has no usage_log, only a telemetry ping — should still count.
    _insert_telemetry(tmp_db_path, "tel-only", "techrehearsal")

    def _ids(payload):
        return {u["id"] for u in payload["users"]}

    all_apps = client.get("/webhooks/admin/users?days=7", headers=ADMIN).json()
    tr = client.get(
        "/webhooks/admin/users?days=7&app=techrehearsal", headers=ADMIN
    ).json()
    ss = client.get(
        "/webhooks/admin/users?days=7&app=shouldersurf", headers=ADMIN
    ).json()

    assert {"ss-only", "tr-only", "tel-only"} <= _ids(all_apps)
    assert _ids(tr) == {"tr-only", "tel-only"}      # SS-only hidden; telemetry counts
    assert _ids(ss) == {"ss-only"}                  # TR users hidden


def test_user_detail_app_filter(client, tmp_db_path):
    user_id = _seed_two_apps(client, tmp_db_path)

    tr = client.get(
        f"/webhooks/admin/user/{user_id}?days=30&app=techrehearsal", headers=ADMIN
    ).json()
    total = sum(c["requests"] for c in tr["by_call_type"])
    assert total == 2
    assert tr["budget"]["this_month"]["requests"] == 2


def test_errors_app_filter(client, tmp_db_path):
    from tests.conftest import _insert_user

    user_id = "app-filter-err-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="pro", monthly_limit=5.10)
    _insert_usage(tmp_db_path, user_id, "shouldersurf", status="error")
    _insert_usage(tmp_db_path, user_id, "techrehearsal", status="error")
    _insert_usage(tmp_db_path, user_id, "techrehearsal", status="error")

    all_apps = client.get("/webhooks/admin/errors?days=7", headers=ADMIN).json()
    tr = client.get(
        "/webhooks/admin/errors?days=7&app=techrehearsal", headers=ADMIN
    ).json()

    assert all_apps["total"] == 3
    assert tr["total"] == 2


def test_telemetry_rich_app_filter(client):
    """telemetry_rich scopes by app_id (sourced from the X-App-ID header)."""
    def _ping(app_id):
        r = client.post(
            "/v1/events/ping",
            json={"event_type": "app_start", "device_id": str(uuid.uuid4())},
            headers={"X-App-ID": app_id},
        )
        assert r.status_code == 204

    _ping("shouldersurf")
    _ping("techrehearsal")
    _ping("techrehearsal")

    all_apps = client.get(
        "/webhooks/admin/telemetry/rich?days=30", headers=ADMIN
    ).json()
    tr = client.get(
        "/webhooks/admin/telemetry/rich?days=30&app=techrehearsal", headers=ADMIN
    ).json()

    assert tr["kpis"]["total_events"] == 2
    assert tr["kpis"]["total_events"] < all_apps["kpis"]["total_events"]


# --- usage-visibility metrics (Scott 2026-08-23): files / docs / photos /
# --- translations, all intercepted server-side from usage_log ----------------

def _insert_media_usage(db_path, user_id, *, metadata=None, image_count=0,
                        call_type="chat", days_ago=0, status="success"):
    now = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, call_type, image_count, metadata, app_id)
           VALUES (?, ?, 'anthropic', 'claude-haiku-4-5', 100, 50, 0.01, ?,
                   120, ?, ?, ?, ?, 'shouldersurf')""",
        (str(uuid.uuid4()), user_id, now, status, call_type, image_count,
         json.dumps(metadata) if metadata else None),
    )
    conn.commit()
    conn.close()


def test_users_carry_media_metrics_from_usage_log(client, tmp_db_path):
    """Docs, photos, generated files, and translations are summed off the
    wire records alone — no client field, no users-table counter."""
    from tests.conftest import _insert_user
    _insert_user(tmp_db_path, user_id="media-u", tier="free", monthly_limit=2.00)
    # Two chat turns with attachments: 3 docs + 2 docs, 4 photos + 1 photo.
    _insert_media_usage(tmp_db_path, "media-u",
                        metadata={"documents": {"count": 3, "raw_bytes": 900}}, image_count=4)
    _insert_media_usage(tmp_db_path, "media-u",
                        metadata={"documents": {"count": 2, "raw_bytes": 500}}, image_count=1)
    # One generation staging 2 artifacts.
    _insert_media_usage(tmp_db_path, "media-u", call_type="artifact_generation",
                        metadata={"generated": {"count": 2, "bytes": 12000}})
    # One translation call (future endpoint's shape: only call_type matters).
    _insert_media_usage(tmp_db_path, "media-u", call_type="translation")
    # Noise that must NOT count: a failed request and an out-of-window row.
    _insert_media_usage(tmp_db_path, "media-u", image_count=9, status="error")
    _insert_media_usage(tmp_db_path, "media-u", image_count=7,
                        metadata={"documents": {"count": 7}}, days_ago=30)

    payload = client.get("/webhooks/admin/users?days=7", headers=ADMIN).json()
    row = next(u for u in payload["users"] if u["id"] == "media-u")
    assert row["window_documents"] == 5
    assert row["window_photos"] == 5
    assert row["window_files_generated"] == 2
    assert row["window_translations"] == 1
    # Lifetime picks up the old row but still never the failed one.
    assert row["lifetime_documents"] == 12
    assert row["lifetime_photos"] == 12
    assert row["lifetime_translations"] == 1


def test_media_metrics_scope_to_app_filter(client, tmp_db_path):
    from tests.conftest import _insert_user
    _insert_user(tmp_db_path, user_id="media-v", tier="pro", monthly_limit=5.10)
    _insert_media_usage(tmp_db_path, "media-v", image_count=3)  # shouldersurf
    payload = client.get(
        "/webhooks/admin/users?days=7&app=techrehearsal", headers=ADMIN).json()
    assert all(u["id"] != "media-v" for u in payload["users"])
    payload = client.get(
        "/webhooks/admin/users?days=7&app=shouldersurf", headers=ADMIN).json()
    row = next(u for u in payload["users"] if u["id"] == "media-v")
    assert row["window_photos"] == 3


# ---------------------------------------------------------------------------
# Multi-select (2026-09-01). Scott's requirement is one holistic view that can
# be narrowed to ONE OR MORE apps, never a page per tenant. The wire form is
# one comma-separated `app` param, so every single-app caller above keeps
# working unchanged and the tests above are the proof of that.
# ---------------------------------------------------------------------------

from app.routers.webhooks import _app_sql, _apps_from_filter  # noqa: E402


def test_parsing_the_filter():
    # Nothing selected means EVERY app. The operator view is holistic by
    # default and narrowing is the deliberate act.
    for empty in (None, "", "   ", ",", " , "):
        assert _apps_from_filter(empty) == ()
    assert _apps_from_filter("n400") == ("n400",)
    assert _apps_from_filter("n400,techrehearsal") == ("n400", "techrehearsal")
    # Lowercased and de-duplicated, matching resolve_app_dir, so a filter
    # built from a checkbox list cannot disagree with config resolution
    # about what "N400" means.
    assert _apps_from_filter(" N400 , techrehearsal ,n400") == (
        "n400", "techrehearsal")


def test_building_the_clause():
    assert _app_sql(()) == ""
    assert _app_sql(("n400",)) == " AND app_id IN (?)"
    assert _app_sql(("a", "b")) == " AND app_id IN (?, ?)"
    assert _app_sql(("a", "b"), "l.app_id") == " AND l.app_id IN (?, ?)"


def test_dashboard_totals_for_two_apps_are_the_union(client, tmp_db_path):
    from tests.conftest import _insert_user

    user_id = "multi-app-user"
    _insert_user(tmp_db_path, user_id=user_id, tier="pro", monthly_limit=5.10)
    for _ in range(3):
        _insert_usage(tmp_db_path, user_id, "shouldersurf")
    for _ in range(2):
        _insert_usage(tmp_db_path, user_id, "techrehearsal")
    _insert_usage(tmp_db_path, user_id, "n400")

    def _total(q):
        return client.get(
            f"/webhooks/admin/dashboard?days=7{q}", headers=ADMIN
        ).json()["usage"]["total_requests"]

    assert _total("") == 6
    assert _total("&app=n400") == 1
    # The union of two, and NOT the total: a filter that quietly ignored the
    # second id would return 3 here and a filter that ignored both would
    # return 6, so this distinguishes all three behaviours.
    assert _total("&app=shouldersurf,techrehearsal") == 5
    assert _total("&app=n400,techrehearsal") == 3


def test_users_list_keeps_users_active_in_either_selected_app(
        client, tmp_db_path):
    from tests.conftest import _insert_user

    for uid, app in (("multi-ss", "shouldersurf"),
                     ("multi-tr", "techrehearsal"),
                     ("multi-n4", "n400")):
        _insert_user(tmp_db_path, user_id=uid, tier="free", monthly_limit=1.0)
        _insert_usage(tmp_db_path, uid, app)

    def _ids(q):
        payload = client.get(
            f"/webhooks/admin/users?days=7{q}", headers=ADMIN).json()
        return {u["id"] for u in payload["users"]}

    both = _ids("&app=shouldersurf,n400")
    assert "multi-ss" in both and "multi-n4" in both
    assert "multi-tr" not in both


def test_errors_panel_takes_more_than_one_app(client, tmp_db_path):
    from tests.conftest import _insert_user

    _insert_user(tmp_db_path, user_id="multi-err", tier="free", monthly_limit=1.0)
    _insert_usage(tmp_db_path, "multi-err", "shouldersurf", status="error")
    _insert_usage(tmp_db_path, "multi-err", "techrehearsal", status="error")
    _insert_usage(tmp_db_path, "multi-err", "n400", status="error")

    def _count(q):
        return client.get(
            f"/webhooks/admin/errors?days=7{q}", headers=ADMIN).json()["total"]

    assert _count("") == 3
    assert _count("&app=shouldersurf,techrehearsal") == 2
    assert _count("&app=n400") == 1


def test_the_tenant_list_comes_from_the_registry(client):
    """The filter's options must be DERIVED, not typed.

    The old selector was four hardcoded <option> tags, which is how N-400
    became a registered tenant with its own config and no way to isolate it
    on any screen. Registering an app has to be the only step.
    """
    payload = client.get("/webhooks/admin/apps", headers=ADMIN).json()
    ids = [a["id"] for a in payload["apps"]]
    assert "shouldersurf" in ids
    assert "techrehearsal" in ids
    assert "n400" in ids, "a registered tenant is missing from the filter"
    # Not a tenant, but real traffic: a request with a missing or
    # unrecognised X-App-ID is logged with this literal app_id, so without
    # it there is traffic no selection could ever show.
    assert "unknown" in ids
    assert all(a["label"] for a in payload["apps"])


def test_every_registered_app_is_offered(client):
    """Pins the derivation itself rather than a list of names, so adding a
    fourth tenant cannot pass this while being absent from the filter."""
    from app.routers.config import load_apps

    payload = client.get("/webhooks/admin/apps", headers=ADMIN).json()
    offered = {a["id"] for a in payload["apps"]}
    assert set((load_apps()["apps"] or {}).keys()) <= offered
