"""The allocation alert reads what was SPENT, not what the meter SAYS.

Scott, 2026-09-20: the overview showed a $2 overage for a user whose lifetime
cost was $0.17. Both numbers were true. Downgrade-to-free deliberately sets
monthly_used_usd to the free cap so a lapsed trial cannot double dip, and the
alert read that meter, so every lapsed trial showed as an overage. The meter
is right for gating and wrong for alerting: it cannot tell "spent to the cap"
from "set to the cap". Same shape as the stalled alert that could not tell
its two causes apart. Fix the signal, not the threshold.
"""
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta

from app.services.allocation_reset import period_start
from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _spend(db_path, user_id, cost, when=None):
    when = (when or datetime.now(timezone.utc)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms, status, app_id)
           VALUES (?, ?, 'anthropic', 'claude-sonnet-5', 100, 50, ?, ?, 100, 'success', 'shouldersurf')""",
        (str(uuid.uuid4()), user_id, cost, when),
    )
    conn.commit()
    conn.close()


def _alerts(client):
    return client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()["allocation_alerts"]


def test_a_lapsed_trial_with_the_meter_at_the_cap_does_not_alert(client, tmp_db_path):
    """The reported case, exactly: meter 2.00 of 2.00, real spend 0.17."""
    _insert_user(tmp_db_path, user_id="lapsed", tier="free",
                 monthly_limit=2.0, monthly_used=2.0)
    _spend(tmp_db_path, "lapsed", 0.17)
    assert [a["user_id"] for a in _alerts(client)] == []


def test_real_spend_at_the_threshold_alerts_whatever_the_meter_says(client, tmp_db_path):
    """The other direction, so the fix is not "alerts never fire". Meter
    reads zero (say it was reset by hand); spend is 85% of the cap."""
    _insert_user(tmp_db_path, user_id="spender", tier="free",
                 monthly_limit=2.0, monthly_used=0.0)
    _spend(tmp_db_path, "spender", 1.70)
    alerts = _alerts(client)
    assert [a["user_id"] for a in alerts] == ["spender"]
    a = alerts[0]
    assert a["monthly_used_usd"] == 1.7, "the alert's number is the real spend"
    assert a["meter_usd"] == 0.0, "and the meter rides beside it, not instead of it"
    assert a["percent_used"] == 85.0
    assert a["monthly_limit_usd"] == 2.0


def test_spend_from_a_previous_period_does_not_count(client, tmp_db_path):
    """The period is the user's own: a reset three days ago starts the
    window three days ago, and last month's spend is not this month's."""
    resets_at = datetime.now(timezone.utc) + relativedelta(months=1) - timedelta(days=3)
    _insert_user(tmp_db_path, user_id="fresh", tier="free",
                 monthly_limit=2.0, monthly_used=0.0)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET allocation_resets_at = ? WHERE id = 'fresh'", (resets_at.isoformat(),))
    conn.commit(); conn.close()
    _spend(tmp_db_path, "fresh", 1.90, when=datetime.now(timezone.utc) - timedelta(days=10))  # last period
    _spend(tmp_db_path, "fresh", 0.10)                                                        # this period
    assert [a["user_id"] for a in _alerts(client)] == []


def test_period_start_covers_its_three_cases():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    # Anchored, in the future: one month before the reset.
    assert period_start(datetime(2026, 10, 18, 13, 22, tzinfo=timezone.utc), now) \
        == datetime(2026, 9, 18, 13, 22, tzinfo=timezone.utc)
    # Stale (the lazy reset has not run): the current period began AT the missed reset.
    assert period_start(datetime(2026, 9, 18, 13, 22, tzinfo=timezone.utc), now) \
        == datetime(2026, 9, 18, 13, 22, tzinfo=timezone.utc)
    # Unset: the calendar month.
    assert period_start(None, now) == datetime(2026, 9, 1, tzinfo=timezone.utc)
    # More than a month out (bad data, or the fixture's 2099): the trailing
    # month, never a start in the future that counts nothing.
    assert period_start(datetime(2099, 1, 1, tzinfo=timezone.utc), now) \
        == datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
