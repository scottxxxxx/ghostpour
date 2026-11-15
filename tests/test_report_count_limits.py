"""Free-tier meeting report counts (Scott, 2026-11-15).

Apple's models (on_device, apple_cloud): 3 reports for the life of the
account, then a card that sends the user to SS AI. SS AI: 5 a calendar
month, then the subscribe card. Paid tiers are not limited. A regenerate of a
meeting that already counted is never refused and never counts again.
"""
from __future__ import annotations

import json
import sqlite3

from tests.test_async_meeting_report import _post, _wire
from tests.test_report_write_build_floor import _seed


def _roomy(db, user_id, monkeypatch=None):
    # Keep the $ budget gate and the 5/min limiter out of the way: this file
    # tests the COUNT.
    if monkeypatch is not None:
        from app.main import app as _app
        monkeypatch.setattr(_app.state.rate_limiter, "check", lambda *a, **k: (True, 0))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET monthly_cost_limit_usd = 100 WHERE id = ?", (user_id,))
    conn.commit(); conn.close()


def _make(client, user, db, n, **body):
    out = []
    for _ in range(n):
        mid = _seed(db, user["user_id"])
        out.append((mid, _post(client, user, mid, **body)))
    return out


def _real(r):
    return r.status_code == 200 and r.json().get("report_status") is None


def _quota(r):
    fs = r.json().get("feature_state") or {}
    return fs if fs.get("code") == "report_quota_reached" else None


def test_ss_ai_free_gets_five_a_month_then_the_subscribe_card(client, free_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch); _roomy(tmp_db_path, free_user["user_id"], monkeypatch)
    made = _make(client, free_user, tmp_db_path, 6, selected_model="ssai")
    assert all(_real(r) for _, r in made[:5]), [r.text for _, r in made[:5]]
    q = _quota(made[5][1])
    assert q and q["bucket"] == "ssai" and (q["used"], q["limit"]) == (5, 5)
    assert q["resets_at"] and q["cta"]["action"] == "open_paywall"
    assert made[5][1].json()["report_status"] == "placeholder_budget_blocked"
    assert 'data-action="open_paywall"' in made[5][1].json()["report_html"]


def test_a_build_that_sends_no_model_counts_as_ss_ai(client, free_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch); _roomy(tmp_db_path, free_user["user_id"], monkeypatch)
    made = _make(client, free_user, tmp_db_path, 6)
    assert _quota(made[5][1])["bucket"] == "ssai"


def test_apple_models_get_three_for_life_then_the_switch_card(client, free_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch); _roomy(tmp_db_path, free_user["user_id"], monkeypatch)
    made = _make(client, free_user, tmp_db_path, 2, selected_model="on_device")
    made += _make(client, free_user, tmp_db_path, 2, selected_model="apple_cloud")
    assert all(_real(r) for _, r in made[:3])
    q = _quota(made[3][1])
    assert q and q["bucket"] == "apple" and (q["used"], q["limit"]) == (3, 3)
    assert q["resets_at"] is None and q["cta"]["action"] == "switch_to_ss_ai"
    assert "Switch to SS AI" in made[3][1].json()["report_html"]
    html = made[3][1].json()["report_html"]
    assert 'data-action="switch_to_ss_ai"' in html and 'data-action="open_paywall"' not in html
    # Apple reports do not use up SS AI's five (served default).
    assert _real(_make(client, free_user, tmp_db_path, 1, selected_model="ssai")[0][1])


def test_regenerating_a_counted_meeting_is_never_refused(client, free_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch); _roomy(tmp_db_path, free_user["user_id"], monkeypatch)
    made = _make(client, free_user, tmp_db_path, 5, selected_model="ssai")
    again = _post(client, free_user, made[0][0], selected_model="ssai")
    assert _real(again), again.text
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute("SELECT COUNT(*) FROM report_quota_ledger WHERE user_id = ?",
                     (free_user["user_id"],)).fetchone()[0]
    conn.close()
    assert n == 5


def test_paid_tiers_are_not_limited(client, pro_user, tmp_db_path, monkeypatch):
    _wire(monkeypatch)
    made = _make(client, pro_user, tmp_db_path, 4, selected_model="on_device")
    assert all(_real(r) for _, r in made)


def test_every_locale_serves_the_same_numbers():
    nums = set()
    for suf in ("", ".es", ".fr", ".ja"):
        d = json.load(open(f"config/remote/client-config{suf}.json"))
        nums.add(json.dumps(d["limits"]["meeting_reports"], sort_keys=True))
    assert len(nums) == 1
    assert json.loads(nums.pop())["apple_lifetime"] == 3
