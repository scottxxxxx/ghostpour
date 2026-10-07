"""The anonymous N-400 allowance follows the APPLICATION, not the install
(Scott, 2026-10-06: "do the limit per application"). One phone may file a
person's own N-400 and then a spouse's. The install keeps a lifetime backstop,
because the client picks its own case_id."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from tests.test_n400_anonymous import ANON_DOC, _anon_user, _chat, _served, _state

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
DOC = {**ANON_DOC, "per_application_usd": 2, "per_install_lifetime_usd": 10,
       "application_allowance_used": {"kind": "application_allowance_used", "text": {"en": "App used."}}}


def _spend_case(db, user_id, amount, case):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO usage_log (id, user_id, app_id, provider, model, estimated_cost_usd, "
        "request_timestamp, status, case_id) VALUES (?,?,?,?,?,?,?, 'success', ?)",
        (str(uuid.uuid4()), user_id, "n400", "anthropic", "m", amount,
         datetime.now(timezone.utc).isoformat(), case))
    conn.commit(); conn.close()


def test_a_used_application_is_refused_and_a_second_application_is_served(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "pa-1")
    _spend_case(tmp_db_path, "pa-1", 2.0, A)
    with _served(client, DOC):
        first = _chat(client, h, case_id=A)
        second = _chat(client, h, case_id=B)
    st = _state(first)
    assert st["code"] == "n400_application_allowance_used" and st["resets_at"] is None
    assert st["cta"]["text"]["en"] == "App used."
    assert _state(second).get("budget_exhausted") is not True and second.json()["text"]


def test_the_case_id_is_matched_case_insensitively(client, tmp_db_path):
    hexy = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"  # letters, so .upper() changes it
    h = _anon_user(tmp_db_path, "pa-2")
    _spend_case(tmp_db_path, "pa-2", 2.0, hexy)
    with _served(client, DOC):
        r = _chat(client, h, case_id=hexy.upper())
    assert _state(r)["code"] == "n400_application_allowance_used"


def test_calls_without_a_case_id_share_one_bucket(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "pa-3")
    _spend_case(tmp_db_path, "pa-3", 2.0, None)
    with _served(client, DOC):
        none = _chat(client, h)
        junk = _chat(client, h, case_id="not-a-uuid")
        real = _chat(client, h, case_id=A)
    assert _state(none)["code"] == "n400_application_allowance_used"
    assert _state(junk)["code"] == "n400_application_allowance_used"
    assert _state(real).get("budget_exhausted") is not True


def test_the_install_backstop_stops_fresh_case_ids(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "pa-4")
    for i in range(5):
        _spend_case(tmp_db_path, "pa-4", 2.0, str(uuid.uuid4()))
    with _served(client, DOC):
        r = _chat(client, h, case_id=str(uuid.uuid4()))
    assert _state(r)["code"] == "n400_install_allowance_used"


def test_without_the_dial_the_install_cap_alone_applies(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "pa-5")
    _spend_case(tmp_db_path, "pa-5", 2.0, A)
    with _served(client):  # ANON_DOC: no per_application_usd, per_install 2
        r = _chat(client, h, case_id=B)
    assert _state(r)["code"] == "n400_install_allowance_used"


# --- N-400's own 8-hex case ids (2026-10-06) ----------------------------------

def _stored_case(db, user_id):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute(
            "SELECT case_id FROM usage_log WHERE user_id=? AND app_id='n400' ORDER BY rowid", (user_id,))]
    finally:
        conn.close()


def test_a_short_hex_case_id_is_stored_lowercased_and_others_are_not(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "pa-6")
    with _served(client, DOC):
        for cid in ("CBC80157", "cbc8015", "cbc801570", "cbc8015g"):
            assert _chat(client, h, case_id=cid).status_code == 200
    assert _stored_case(tmp_db_path, "pa-6") == ["cbc80157", None, None, None]


def test_the_per_application_cap_buckets_short_ids(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "pa-7")
    _spend_case(tmp_db_path, "pa-7", 2.0, "cbc80157")
    with _served(client, DOC):
        used = _chat(client, h, case_id="CBC80157")
        other = _chat(client, h, case_id="0000abcd")
    assert _state(used)["code"] == "n400_application_allowance_used"
    assert _state(other).get("budget_exhausted") is not True


def test_the_same_short_id_on_another_install_is_a_different_application(client, tmp_db_path):
    _anon_user(tmp_db_path, "pa-8a")
    _spend_case(tmp_db_path, "pa-8a", 2.0, "cbc80157")
    h = _anon_user(tmp_db_path, "pa-8b")
    with _served(client, DOC):
        r = _chat(client, h, case_id="cbc80157")
    assert _state(r).get("budget_exhausted") is not True


def test_the_dashboard_counts_the_same_short_id_on_two_installs_as_two_cases(client, tmp_db_path):
    for uid in ("pa-9a", "pa-9b"):
        h = _anon_user(tmp_db_path, uid)
        with _served(client, DOC):
            _chat(client, h, jurisdiction="US-CA", case_id="cbc80157")
            _chat(client, h, jurisdiction="US-CA", case_id="cbc80157")
    key = {"X-Admin-Key": client.app.state.settings.admin_key}
    d = client.get("/webhooks/admin/usage-by-place?days=7", headers=key).json()
    ca = [x for x in d["by_jurisdiction"] if x["jurisdiction"] == "US-CA"][0]
    assert (ca["calls"], ca["users"], ca["cases"]) == (4, 2, 2)
