"""Per-app filter on the admin dashboard endpoints (Phase A3).

The dashboard, users, errors, user-detail and telemetry/rich endpoints all
accept an optional `app` query param. Empty/absent means "all apps"; when set,
every usage_log- (or telemetry_events-) derived metric is scoped to that
app_id. These tests seed rows tagged with distinct app_ids and assert the
filter narrows the result.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

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
