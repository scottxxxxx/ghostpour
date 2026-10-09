"""The anonymous N-400 caps survive "Delete all my data" (Scott, 2026-10-08:
"Caps survive"). The caps are sums over usage_log and a delete removes those
rows, so before this a delete handed the same install a fresh allowance. The
delete now carries the install's spend into one row keyed on the account's
apple_sub (already a one-way hash of the app and install id), and the cap
check adds it back. No user_id, no content and no case id survive."""
from __future__ import annotations

import sqlite3
import uuid

from tests.test_n400_anonymous import ANON_DOC, _chat, _served, _spend, _state

N400 = {"X-App-ID": "n400"}
DOC = {**ANON_DOC, "per_install_lifetime_usd": 10}


def _mint(client, install, app="n400"):
    r = client.post("/auth/anonymous", json={"install_id": install}, headers={"X-App-ID": app})
    assert r.status_code == 200, r.text
    tok = r.json()["access_token"]
    uid = client.app.state.jwt_service.verify_access_token(tok)["sub"]
    return uid, {"Authorization": f"Bearer {tok}"}


def _delete(client, headers, app="n400"):
    r = client.post("/v1/account/delete", json={}, headers={**headers, "X-App-ID": app})
    assert r.status_code == 200, r.text


def _carry_rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM anonymous_spend_carry")]
    finally:
        conn.close()


def test_a_delete_does_not_reset_the_install_cap(client, tmp_db_path):
    """The real shape: spend, delete all my data, mint again from the same
    install, and the install is still at its cap."""
    install = str(uuid.uuid4())
    uid, h = _mint(client, install)
    _spend(tmp_db_path, uid, "n400", 10.0)
    _delete(client, h)
    uid2, h2 = _mint(client, install)
    assert uid2 != uid, "the delete removed the account, so this is a new row"
    with _served(client, DOC):
        r = _chat(client, h2)
    assert _state(r)["code"] == "n400_install_allowance_used"


def test_a_different_install_is_not_charged_for_it(client, tmp_db_path):
    """The counterweight: the carry belongs to that install only."""
    uid, h = _mint(client, str(uuid.uuid4()))
    _spend(tmp_db_path, uid, "n400", 10.0)
    _delete(client, h)
    _, other = _mint(client, str(uuid.uuid4()))
    with _served(client, DOC):
        r = _chat(client, other)
    assert _state(r).get("budget_exhausted") is not True and r.json()["text"]


def test_the_carry_row_holds_one_number_per_install_and_no_content(client, tmp_db_path):
    install = str(uuid.uuid4())
    uid, h = _mint(client, install)
    _spend(tmp_db_path, uid, "n400", 1.25)
    _spend(tmp_db_path, uid, "n400", 0.5, days_ago=3)
    _delete(client, h)
    rows = _carry_rows(tmp_db_path)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"sub_hash", "app_id", "lifetime_usd", "day", "day_usd", "updated_at"}
    assert row["sub_hash"].startswith("anonymous:") and install not in row["sub_hash"]
    assert (row["app_id"], row["lifetime_usd"], row["day_usd"]) == ("n400", 1.75, 1.25)
    conn = sqlite3.connect(tmp_db_path)
    assert conn.execute("SELECT COUNT(*) FROM usage_log WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)).fetchone()[0] == 0
    conn.close()


def test_a_second_delete_adds_to_the_first(client, tmp_db_path):
    install = str(uuid.uuid4())
    uid, h = _mint(client, install)
    _spend(tmp_db_path, uid, "n400", 4.0)
    _delete(client, h)
    uid, h = _mint(client, install)
    _spend(tmp_db_path, uid, "n400", 4.0)
    _delete(client, h)
    assert _carry_rows(tmp_db_path)[0]["lifetime_usd"] == 8.0


def test_todays_carried_spend_still_counts_toward_the_daily_backstop(client, tmp_db_path):
    install = str(uuid.uuid4())
    uid, h = _mint(client, install)
    _spend(tmp_db_path, uid, "n400", 5.0)
    _delete(client, h)
    _, other = _mint(client, str(uuid.uuid4()))
    with _served(client, {**DOC, "daily_all_installs_usd": 5}):
        r = _chat(client, other)
    assert _state(r)["code"] == "n400_daily_capacity_reached"


def test_nothing_is_kept_for_an_app_with_no_allowance(client, tmp_db_path):
    """ShoulderSurf's anonymous accounts get no allowance, so a delete keeps
    nothing of theirs."""
    uid, h = _mint(client, str(uuid.uuid4()), app="shouldersurf")
    _spend(tmp_db_path, uid, "shouldersurf", 3.0)
    _delete(client, h, app="shouldersurf")
    assert _carry_rows(tmp_db_path) == []


def test_both_carry_reads_use_an_index(client, tmp_db_path):
    """Both run on every anonymous turn, beside the live sums."""
    from app.services import anonymous_budget
    from tests.test_n400_anonymous import _plan

    plan = _plan(tmp_db_path, anonymous_budget.CARRIED_SQL, ("u", "n400"))
    assert "SCAN" not in plan, plan
    plan = _plan(tmp_db_path, anonymous_budget.CARRIED_TODAY_SQL, ("n400", "2026-10-08"))
    assert "SCAN" not in plan and "idx_spend_carry_app_day" in plan, plan
