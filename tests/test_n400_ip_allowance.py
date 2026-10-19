"""The per-IP anonymous allowance (Scott, 2026-10-19: "block on ip if they go
over $2"). Other unflagged anonymous accounts on the same network spending
the allowance refuses a NEW account there; the caller's own spend never
does, so its own per-application and per-install allowance still rules.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from app.services import anonymous_budget
from app.services.anonymous_budget import network_key
from tests.test_n400_anonymous import _anon_user, _chat, _plan, _served, _state

DOC = {
    "per_application_usd": 2,
    "per_install_lifetime_usd": 10,
    "daily_all_installs_usd": 50,
    "per_ip_usd": 2,
    "per_ip_window_days": 30,
    "install_allowance_used": {"kind": "install_allowance_used", "text": {"en": "Used."}},
    "daily_capacity_reached": {"kind": "daily_capacity_reached", "text": {"en": "Tomorrow."}},
}
HOME = "203.0.113.7"


def _spend_on(db, user_id, ip, amount, days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO usage_log (id, user_id, app_id, provider, model, estimated_cost_usd, "
        "request_timestamp, status, ip_hash) VALUES (?,?,?,?,?,?,?, 'success', ?)",
        (str(uuid.uuid4()), user_id, "n400", "anthropic", "m", amount, ts, network_key(ip)))
    conn.commit(); conn.close()


def _from(client, headers, ip=HOME, **meta):
    return _chat(client, {**headers, "X-Forwarded-For": ip}, **meta)


def _refused(r):
    st = _state(r)
    return st.get("budget_exhausted") is True and st.get("code") == "n400_install_allowance_used"


# --- the key -------------------------------------------------------------------

def test_the_key_is_the_ipv4_address_or_the_ipv6_slash_64():
    assert network_key("203.0.113.7") == network_key(" 203.0.113.7 ")
    assert network_key("203.0.113.7") != network_key("203.0.113.8")
    # A phone's rotating IPv6 interface id stays on one network.
    assert network_key("2001:db8:1:2:aaaa::1") == network_key("2001:db8:1:2:bbbb::9")
    assert network_key("2001:db8:1:2::1") != network_key("2001:db8:1:3::1")
    assert network_key("::ffff:203.0.113.7") == network_key("203.0.113.7")
    assert network_key("") is None and network_key("not an ip") is None
    assert "203.0.113.7" not in network_key("203.0.113.7")


# --- the rule ------------------------------------------------------------------

def test_a_new_account_on_a_network_that_used_the_allowance_is_refused(client, tmp_db_path):
    _anon_user(tmp_db_path, "first")
    _spend_on(tmp_db_path, "first", HOME, 2.0)
    h = _anon_user(tmp_db_path, "reinstall")
    with _served(client, DOC):
        assert _refused(_from(client, h))


def test_under_the_allowance_the_new_account_is_served(client, tmp_db_path):
    _anon_user(tmp_db_path, "first")
    _spend_on(tmp_db_path, "first", HOME, 1.99)
    h = _anon_user(tmp_db_path, "second")
    with _served(client, DOC):
        r = _from(client, h)
    assert r.status_code == 200 and not _refused(r) and r.json()["text"]


def test_an_account_is_never_refused_by_its_own_spend(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "spouse-too")
    # Over the $2 line, so this fails if the rule ever counts the caller: a
    # first application that used $2.50, then a spouse's on the same install,
    # where only the per-application and per-install allowances may rule.
    _spend_on(tmp_db_path, "spouse-too", HOME, 2.5)
    with _served(client, DOC):
        r = _from(client, h, case_id="b2e0ac63")
    assert not _refused(r) and r.json()["text"]


def test_another_network_is_not_refused(client, tmp_db_path):
    _anon_user(tmp_db_path, "first")
    _spend_on(tmp_db_path, "first", HOME, 2.0)
    h = _anon_user(tmp_db_path, "elsewhere")
    with _served(client, DOC):
        assert not _refused(_from(client, h, ip="198.51.100.4"))


def test_spend_older_than_the_window_does_not_count(client, tmp_db_path):
    _anon_user(tmp_db_path, "first")
    _spend_on(tmp_db_path, "first", HOME, 2.0, days_ago=31)
    h = _anon_user(tmp_db_path, "later")
    with _served(client, DOC):
        assert not _refused(_from(client, h))


def test_a_flagged_test_account_never_counts_against_the_network(client, tmp_db_path):
    _anon_user(tmp_db_path, "scotts-phone")
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET exclude_from_stats = 1 WHERE id = 'scotts-phone'")
    conn.commit(); conn.close()
    _spend_on(tmp_db_path, "scotts-phone", HOME, 5.0)
    h = _anon_user(tmp_db_path, "scotts-reinstall")
    with _served(client, DOC):
        assert not _refused(_from(client, h))


def test_no_dial_means_no_ip_rule(client, tmp_db_path):
    _anon_user(tmp_db_path, "first")
    _spend_on(tmp_db_path, "first", HOME, 2.0)
    h = _anon_user(tmp_db_path, "second")
    doc = {k: v for k, v in DOC.items() if k != "per_ip_usd"}
    with _served(client, doc):
        assert not _refused(_from(client, h))


# --- the write -----------------------------------------------------------------

def test_a_turn_stores_the_servers_key_never_the_clients(client, tmp_db_path):
    h = _anon_user(tmp_db_path, "writer")
    with _served(client, DOC):
        _from(client, h, ip_key="forged-by-the-client")
    conn = sqlite3.connect(tmp_db_path)
    rows = conn.execute("SELECT ip_hash FROM usage_log WHERE user_id = 'writer'").fetchall()
    conn.close()
    assert rows == [(network_key(HOME),)]


def test_the_shipped_doc_sets_two_dollars_over_thirty_days():
    import json
    a = json.load(open("config/remote/n400/budget.json"))["anonymous"]
    assert a["per_ip_usd"] == 2 and a["per_ip_window_days"] == 30


def test_the_ip_sum_never_reads_usage_log(client, tmp_db_path):
    plan = _plan(tmp_db_path, anonymous_budget.IP_OTHERS_SPEND_SQL,
                 ("n400", "k", "2026-10-01", "u"))
    assert "USING COVERING INDEX idx_usage_app_ip" in plan, plan
