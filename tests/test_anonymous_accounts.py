"""Anonymous purchase accounts (2026-09-26).

Apple rejected iOS 1.18 three times under 5.1.1(v): buying a plan may not
require registration, and the inline Sign in with Apple counted as
registration. A signed-out plan tap now gets a silent account that holds no
personal data. Scott's rulings: it gets NO allowance until a verified
purchase binds a plan (creation costs one unauthenticated call, so a free
allowance on it could be farmed), and Sign in with Apple stays optional.

Assertions are on STORED STATE and on what the client decodes, not on a 200.
"""

import logging
import sqlite3
import uuid
from unittest.mock import patch

import pytest

from app.models.user import ANONYMOUS_SUB_PREFIX
from app.routers.auth import anonymous_sub

SS = {"X-App-ID": "shouldersurf"}
VERIFY = "app.services.apple_auth.AppleAuthVerifier.verify_identity_token"


def _anon(client, install_id=None, headers=SS):
    if install_id is None:
        install_id = str(uuid.uuid4())
    return client.post("/auth/anonymous", json={"install_id": install_id}, headers=headers)


def _row(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    finally:
        conn.close()


# --- A: create, idempotently, with nothing personal stored --------------------------

def test_an_anonymous_account_is_created_with_nothing_personal(client, tmp_db_path):
    install = str(uuid.uuid4())
    r = _anon(client, install)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["is_anonymous"] is True and user["tier"] == "free"
    assert user["email"] is None and user["display_name"] is None
    assert r.json()["access_token"] and r.json()["refresh_token"]
    row = _row(tmp_db_path, user["id"])
    assert row["apple_sub"] == anonymous_sub(install)
    assert row["apple_sub"].startswith(ANONYMOUS_SUB_PREFIX)
    # The install id is a credential: only its hash is stored.
    assert install not in row["apple_sub"] and install.upper() not in row["apple_sub"]
    assert row["email"] is None


def test_the_same_install_is_the_same_account_in_either_case(client):
    install = str(uuid.uuid4())
    first = _anon(client, install).json()["user"]["id"]
    assert _anon(client, install).json()["user"]["id"] == first
    # Swift's uuidString is uppercase; it must not mint a second account.
    assert _anon(client, install.upper()).json()["user"]["id"] == first
    assert _anon(client).json()["user"]["id"] != first


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "1234", "../etc/passwd"])
def test_a_non_uuid_install_id_is_refused(client, bad):
    r = _anon(client, bad)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_request"


@pytest.mark.parametrize("headers", [{}, {"X-App-ID": "techrehearsal"}])
def test_only_the_anonymous_apps_are_offered_anonymous_accounts(client, headers):
    """shouldersurf, n400 and (2026-10-09) i765; techrehearsal and a headerless
    caller are refused."""
    r = _anon(client, headers=headers)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "anonymous_not_offered"


def test_a_closed_anonymous_account_answers_410_not_a_new_account(client, tmp_db_path):
    install = str(uuid.uuid4())
    uid = _anon(client, install).json()["user"]["id"]
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
    conn.commit()
    conn.close()
    r = _anon(client, install)
    assert r.status_code == 410 and r.json()["detail"]["code"] == "anonymous_account_closed"


def test_the_install_id_is_never_logged(client, caplog):
    install = str(uuid.uuid4())
    with caplog.at_level(logging.DEBUG):
        _anon(client, install)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert install not in text and install.upper() not in text


def test_creation_is_rate_limited_per_ip(client):
    codes = [_anon(client).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and 429 in codes[5:]


# --- G: refresh keeps the account anonymous -----------------------------------------

def test_refresh_keeps_is_anonymous(client):
    first = _anon(client).json()
    r = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}, headers=SS)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["is_anonymous"] is True
    assert r.json()["user"]["id"] == first["user"]["id"]


def test_an_apple_account_is_not_anonymous(client):
    with patch(VERIFY, return_value={"sub": "sub-anon-test", "email": "x@privaterelay.appleid.com"}):
        r = client.post("/auth/apple", json={"identity_token": "mock"}, headers=SS)
    assert r.status_code == 200 and r.json()["user"]["is_anonymous"] is False


# --- B: no plan, no allowance -----------------------------------------------------------

def _bearer(client):
    body = _anon(client).json()
    return body["user"]["id"], {**SS, "Authorization": f"Bearer {body['access_token']}"}


def test_usage_reports_zero_allowance_without_a_plan(client):
    _, hdr = _bearer(client)
    r = client.get("/v1/usage/me", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allocation"]["monthly_limit_usd"] == 0
    assert body["allocation"]["monthly_remaining_usd"] == 0
    assert body["credits"]["total"] == 0
    assert body["credits"]["remaining"] == 0
    # Every hours view says 0, and never -1, which means UNLIMITED. The
    # first version reported the free tier's marketed 5 hours here while
    # chat refused, and -1 for hours used and remaining.
    assert body["credits"]["hours_total"] == 0
    assert body["credits"]["hours_used"] == 0
    assert body["credits"]["hours_remaining"] == 0
    assert body["hours"]["limit"] == 0 and body["hours"]["remaining"] == 0
    assert body["search"]["total"] == 0


def test_chat_is_blocked_before_any_model_call_without_a_plan(client):
    from tests.conftest import chat_request
    _, hdr = _bearer(client)

    async def must_not_run(*a, **k):
        raise AssertionError("a model was called for an anonymous account with no plan")
    with patch("app.services.anthropic_or_fallback.route_with_fallback", must_not_run):
        r = client.post("/v1/chat", headers=hdr, json=chat_request(user_content="hello"))
    assert r.status_code == 200, r.text
    fs = r.json()["feature_state"]
    assert fs["credits_total"] == 0 and fs["credits_remaining"] == 0
    assert fs["cta"] is not None


def test_a_paid_anonymous_account_gets_its_plan_allowance(client, tmp_db_path):
    uid, hdr = _bearer(client)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET tier = 'plus' WHERE id = ?", (uid,))
    conn.commit()
    conn.close()
    body = client.get("/v1/usage/me", headers=hdr).json()
    assert body["allocation"]["monthly_limit_usd"] != 0


# --- D: a VERIFIED restore moves the plan, and only then --------------------------------

OTID = "2000001211148772"
PLUS = "com.weirtech.shouldersurf.sub.plus.monthly"


def _paid_holder(db_path, used=1.23):
    """An account (the first install) holding a Plus plan with usage."""
    from tests.conftest import _insert_user
    uid = f"holder-{uuid.uuid4().hex[:8]}"
    _insert_user(db_path, user_id=uid, tier="plus", monthly_limit=-1, monthly_used=used)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET original_transaction_id = ?, searches_used = 7 WHERE id = ?",
                 (OTID, uid))
    conn.commit()
    conn.close()
    return uid


def _sign(monkeypatch):
    from app.services import receipt_verification as _rv
    monkeypatch.setattr(_rv, "verify_signed_transaction", lambda jws, bundle_ids: {
        "bundleId": "com.shouldersurf.ShoulderSurf", "originalTransactionId": OTID,
        "transactionId": OTID, "environment": "Sandbox", "productId": PLUS})


def _restore(client, hdr, signed):
    body = {"product_id": PLUS, "transaction_id": OTID}
    if signed:
        body["signed_transaction"] = "j.w.s"
    return client.post("/v1/verify-receipt", json=body, headers=hdr)


def test_a_verified_restore_moves_the_plan_and_its_usage(client, tmp_db_path, monkeypatch):
    holder = _paid_holder(tmp_db_path)
    _sign(monkeypatch)
    uid, hdr = _bearer(client)                      # a second install, a new anonymous account
    r = _restore(client, hdr, signed=True)
    assert r.status_code == 200, r.text
    assert r.json()["moved_from_other_account"] is True
    new, old = _row(tmp_db_path, uid), _row(tmp_db_path, holder)
    assert new["tier"] == "plus" and new["original_transaction_id"] == OTID
    # The period's usage came with it: restoring cannot mint a fresh month.
    assert new["monthly_used_usd"] == pytest.approx(1.23) and new["searches_used"] == 7
    assert old["tier"] == "free" and old["original_transaction_id"] is None


def test_a_moved_plan_takes_its_subscription_records_and_counts_once(client, tmp_db_path, monkeypatch):
    """The first real sandbox restore (2026-09-26) left the per-subscriber
    status row on the account the plan had left, and two "subscribed" events
    for one purchase, one per account: every restore would have counted as a
    new subscriber on the dashboard."""
    holder = _paid_holder(tmp_db_path)
    now = "2026-09-26T19:51:35+00:00"
    _exec(tmp_db_path, "INSERT INTO subscription_status (original_transaction_id, user_id, environment, "
          "status, checked_at, source) VALUES (?, ?, 'Sandbox', 1, ?, 'assn')", (OTID, holder, now))
    _exec(tmp_db_path, "INSERT INTO subscription_events (id, user_id, event_type, to_tier, original_transaction_id, "
          "source, effective_at, recorded_at) VALUES ('evt-assn-1', ?, 'subscribed', 'plus', ?, 'assn', ?, ?)",
          (holder, OTID, now, now))
    _sign(monkeypatch)
    uid, hdr = _bearer(client)
    assert _restore(client, hdr, signed=True).json()["moved_from_other_account"] is True

    status = _q(tmp_db_path, "SELECT user_id FROM subscription_status WHERE original_transaction_id = ?", (OTID,))
    assert [r["user_id"] for r in status] == [uid]
    events = _q(tmp_db_path, "SELECT user_id, event_type, subtype FROM subscription_events "
                "WHERE original_transaction_id = ? ORDER BY recorded_at", (OTID,))
    assert {e["user_id"] for e in events} == {uid}
    assert [e["event_type"] for e in events].count("subscribed") == 1
    assert {"event_type": "reconciled", "subtype": "moved", "user_id": uid} in events


def test_an_unverified_claim_downgrades_nobody(client, tmp_db_path):
    """Receipt enforcement is off, so an unsigned claim is still accepted for
    the requester. It must never strip someone else's plan."""
    holder = _paid_holder(tmp_db_path)
    uid, hdr = _bearer(client)
    r = _restore(client, hdr, signed=False)
    assert r.status_code == 200, r.text
    assert r.json()["moved_from_other_account"] is False
    assert _row(tmp_db_path, holder)["tier"] == "plus"


# --- E: Sign in with Apple converts or merges the anonymous account ----------------------

def _apple_signin(client, sub, anonymous_token=None):
    body = {"identity_token": "mock"}
    if anonymous_token is not None:
        body["anonymous_token"] = anonymous_token
    with patch(VERIFY, return_value={"sub": sub, "email": f"{sub}@privaterelay.appleid.com"}):
        return client.post("/auth/apple", json=body, headers=SS)


def _exec(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _q(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _paid_anonymous(client, db_path, install):
    body = _anon(client, install).json()
    uid = body["user"]["id"]
    _exec(db_path, "UPDATE users SET tier='plus', original_transaction_id=?, monthly_used_usd=0.4 WHERE id=?",
          (OTID, uid))
    now = "2026-09-26T00:00:00+00:00"
    _exec(db_path, "INSERT INTO device_tokens (device_token, user_id, environment, bundle_id, created_at, last_seen_at)"
          " VALUES (?, ?, 'production', 'com.shouldersurf.ShoulderSurf', ?, ?)", (f"tok-{uid}", uid, now, now))
    _exec(db_path, "INSERT INTO project_prefs (user_id, project_id, key, value, updated_at) VALUES (?, 'p1', 'k', 'anon', ?)",
          (uid, now))
    _exec(db_path, "INSERT INTO project_prefs (user_id, project_id, key, value, updated_at) VALUES (?, 'p2', 'k', 'anon-only', ?)",
          (uid, now))
    return uid, body["access_token"]


def test_a_new_apple_id_converts_the_anonymous_account_in_place(client, tmp_db_path):
    install = str(uuid.uuid4())
    uid, token = _paid_anonymous(client, tmp_db_path, install)
    r = _apple_signin(client, "sub-new-1", token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merge"] == "converted" and body["merge_reason"] is None
    assert body["user"]["id"] == uid and body["user"]["is_anonymous"] is False
    assert body["user"]["tier"] == "plus"
    row = _row(tmp_db_path, uid)
    assert row["apple_sub"] == "sub-new-1" and row["original_transaction_id"] == OTID
    assert row["is_active"] == 1


def test_an_existing_apple_account_wins_and_takes_the_plan_and_history(client, tmp_db_path):
    apple_id = _apple_signin(client, "sub-existing-1").json()["user"]["id"]
    _exec(tmp_db_path, "INSERT INTO project_prefs (user_id, project_id, key, value, updated_at) VALUES (?, 'p1', 'k', 'apple', 'x')",
          (apple_id,))
    install = str(uuid.uuid4())
    anon_id, token = _paid_anonymous(client, tmp_db_path, install)

    r = _apple_signin(client, "sub-existing-1", token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merge"] == "merged" and body["plan_conflict"] is False
    assert body["user"]["id"] == apple_id and body["user"]["tier"] == "plus"

    apple, anon = _row(tmp_db_path, apple_id), _row(tmp_db_path, anon_id)
    assert apple["tier"] == "plus" and apple["original_transaction_id"] == OTID
    assert apple["monthly_used_usd"] == pytest.approx(0.4)
    assert anon["is_active"] == 0 and anon["tier"] == "free" and anon["original_transaction_id"] is None
    # History moved; the colliding pref is the Apple account's, and the
    # anonymous one stayed behind on the closed account, not deleted.
    assert _q(tmp_db_path, "SELECT user_id FROM device_tokens WHERE device_token = ?",
              (f"tok-{anon_id}",))[0]["user_id"] == apple_id
    prefs = {(p["project_id"], p["user_id"]): p["value"]
             for p in _q(tmp_db_path, "SELECT * FROM project_prefs")}
    assert prefs[("p1", apple_id)] == "apple" and prefs[("p1", anon_id)] == "anon"
    assert prefs[("p2", apple_id)] == "anon-only"
    rec = _q(tmp_db_path, "SELECT * FROM anonymous_merges WHERE anonymous_user_id = ?", (anon_id,))[0]
    assert rec["into_user_id"] == apple_id and rec["plan_moved"] == 1 and rec["cq_merged_at"] is None
    # The closed account cannot be reopened from its install, and its sessions are dead.
    assert _anon(client, install).status_code == 410
    assert _q(tmp_db_path, "SELECT COUNT(*) n FROM refresh_tokens WHERE user_id = ? AND revoked = 0",
              (anon_id,))[0]["n"] == 0


def test_when_both_are_paid_the_apple_account_keeps_its_own_plan(client, tmp_db_path):
    apple_id = _apple_signin(client, "sub-existing-2").json()["user"]["id"]
    _exec(tmp_db_path, "UPDATE users SET tier='pro', original_transaction_id='2000009999999999' WHERE id=?", (apple_id,))
    anon_id, token = _paid_anonymous(client, tmp_db_path, str(uuid.uuid4()))
    body = _apple_signin(client, "sub-existing-2", token).json()
    assert body["merge"] == "merged" and body["plan_conflict"] is True
    assert _row(tmp_db_path, apple_id)["tier"] == "pro"
    assert _row(tmp_db_path, apple_id)["original_transaction_id"] == "2000009999999999"
    # The anonymous purchase stays on the closed row, so its notifications
    # can never change the Apple account's tier.
    assert _row(tmp_db_path, anon_id)["original_transaction_id"] == OTID


def test_an_expired_token_merges_nothing_and_says_why(client, tmp_db_path):
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    anon_id, _ = _paid_anonymous(client, tmp_db_path, str(uuid.uuid4()))
    js = client.app.state.jwt_service
    expired = pyjwt.encode({"sub": anon_id, "type": "access",
                            "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
                           js.secret, algorithm=js.algorithm)
    body = _apple_signin(client, "sub-new-3", expired).json()
    assert body["merge"] == "none" and body["merge_reason"] == "anonymous_token_expired"
    assert body["user"]["id"] != anon_id
    assert _row(tmp_db_path, anon_id)["is_active"] == 1


def test_an_apple_accounts_token_is_not_anonymous(client):
    other = _apple_signin(client, "sub-other-4").json()["access_token"]
    body = _apple_signin(client, "sub-new-4", other).json()
    assert body["merge"] == "none" and body["merge_reason"] == "not_anonymous"


def test_a_plain_sign_in_reports_no_merge(client):
    body = _apple_signin(client, "sub-plain-5").json()
    assert body["merge"] == "none" and body["merge_reason"] is None


# --- F: an anonymous account can be deleted -------------------------------------------

def test_an_anonymous_account_can_delete_itself(client, tmp_db_path):
    """5.1.1(v) covers every account the app creates, including this one.
    There is no Apple code, so revocation is skipped by design."""
    uid, hdr = _bearer(client)
    r = client.post("/v1/account/delete", headers=hdr, json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"
    assert not _q(tmp_db_path, "SELECT id FROM users WHERE id = ? AND is_active = 1", (uid,))



# --- the anonymous CTA (copy Scott approved via Social, 2026-09-26) ---------------------

ANON_CTA = {
    "en": "You've used the Shoulder Surf AI on this plan. See plans to keep going.",
    "es": "Ya usaste la Shoulder Surf AI de este plan. Ve los planes para seguir.",
    "fr": "Vous avez utilisé la Shoulder Surf AI de ce forfait. Voyez les forfaits pour continuer.",
    "ja": "このプランのShoulder Surf AIを使い切りました。続けるにはプランを確認してください。",
}


@pytest.mark.parametrize("lang", sorted(ANON_CTA))
def test_an_anonymous_account_is_never_told_it_used_free_ai(client, lang):
    from tests.conftest import chat_request
    _, hdr = _bearer(client)
    hdr = {**hdr, "Accept-Language": lang}

    async def nope(*a, **k):
        raise AssertionError("no model call")
    with patch("app.services.anthropic_or_fallback.route_with_fallback", nope):
        chat = client.post("/v1/chat", headers=hdr, json=chat_request(user_content="hi")).json()
    usage = client.get("/v1/usage/me", headers=hdr).json()
    for cta in (chat["feature_state"]["cta"], usage["budget_exhausted_cta"]):
        assert cta["text"] == ANON_CTA[lang]
        assert cta["action"] == "open_paywall" and cta["kind"] == "budget_exhausted"


def test_a_signed_in_free_account_keeps_the_free_line(client, free_user):
    from app.services.budget_cta import get_budget_exhausted_cta
    cfg = client.app.state.remote_configs
    assert "free AI" in get_budget_exhausted_cta(cfg, "free", None)["text"]
    assert get_budget_exhausted_cta(cfg, "free", None, anonymous=True)["text"] == ANON_CTA["en"]


# --- one person, several devices (Scott, 2026-09-26) ------------------------------------

ADMIN = {"X-Admin-Key": "test-admin-key"}


def test_a_restore_links_the_two_devices_as_one_person(client, tmp_db_path, monkeypatch):
    """ "Show them, but track it is the same account on two devices." """
    first_id, first_hdr = _bearer(client)                 # device 1, anonymous
    _exec(tmp_db_path, "UPDATE users SET tier='plus', original_transaction_id=? WHERE id=?",
          (OTID, first_id))
    _sign(monkeypatch)
    second_id, second_hdr = _bearer(client)               # device 2 restores
    assert _restore(client, second_hdr, signed=True).json()["moved_from_other_account"] is True

    links = _q(tmp_db_path, "SELECT from_user_id, to_user_id, reason FROM account_links")
    assert links == [{"from_user_id": first_id, "to_user_id": second_id, "reason": "restore"}]

    rows = {u["id"]: u for u in client.get("/webhooks/admin/users", headers=ADMIN).json()["users"]}
    for uid in (first_id, second_id):
        assert rows[uid]["is_anonymous"] is True and rows[uid]["linked_devices"] == 2
    # The group is keyed by the account that holds the plan now.
    assert rows[first_id]["account_group"] == rows[second_id]["account_group"] == second_id

    users = client.get("/webhooks/admin/dashboard", headers=ADMIN).json()["users"]
    assert users["anonymous"] == {"accounts": 2, "people": 1, "with_plan": 1}
    assert users["people"] == users["active"] - 1


def test_a_merge_links_the_anonymous_account_to_the_apple_account(client, tmp_db_path):
    apple_id = _apple_signin(client, "sub-link-1").json()["user"]["id"]
    anon_id, token = _paid_anonymous(client, tmp_db_path, str(uuid.uuid4()))
    assert _apple_signin(client, "sub-link-1", token).json()["merge"] == "merged"
    assert _q(tmp_db_path, "SELECT from_user_id, to_user_id, reason FROM account_links") == [
        {"from_user_id": anon_id, "to_user_id": apple_id, "reason": "merge"}]
    rows = {u["id"]: u for u in client.get("/webhooks/admin/users", headers=ADMIN).json()["users"]}
    assert rows[apple_id]["linked_devices"] == 2 and rows[apple_id]["is_anonymous"] is False


def test_an_unlinked_anonymous_account_is_its_own_person(client):
    uid, _ = _bearer(client)
    rows = {u["id"]: u for u in client.get("/webhooks/admin/users", headers=ADMIN).json()["users"]}
    assert rows[uid]["is_anonymous"] is True and rows[uid]["linked_devices"] == 1
    assert rows[uid]["account_group"] is None


def test_the_replay_after_a_conflict_merge_never_replaces_the_apple_accounts_plan(
        client, tmp_db_path, monkeypatch):
    """Prod, 2026-09-26 20:15Z: Scott signed in with his Pro Apple ID from an
    anonymous account holding a sandbox Plus. The merge kept his Pro
    (plan_conflict), then StoreKit replayed the Plus from his Apple account
    half a second later, and that replay moved it onto him: Pro became a Plus
    trial. The replay must answer with the plan he already has."""
    signin = _apple_signin(client, "sub-conflict-replay").json()
    apple_id = signin["user"]["id"]
    _exec(tmp_db_path, "UPDATE users SET tier='pro', original_transaction_id=NULL WHERE id=?", (apple_id,))
    anon_id, token = _paid_anonymous(client, tmp_db_path, str(uuid.uuid4()))
    merged = _apple_signin(client, "sub-conflict-replay", token).json()
    assert merged["merge"] == "merged" and merged["plan_conflict"] is True

    _sign(monkeypatch)
    hdr = {**SS, "Authorization": f"Bearer {merged['access_token']}"}
    r = _restore(client, hdr, signed=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_tier"] == "pro" and body["kept_existing_plan"] is True
    assert body["moved_from_other_account"] is False
    apple = _row(tmp_db_path, apple_id)
    assert apple["tier"] == "pro" and apple["is_trial"] == 0
    assert apple["original_transaction_id"] is None
    # The anonymous purchase stays where the conflict rule put it.
    assert _row(tmp_db_path, anon_id)["original_transaction_id"] == OTID
