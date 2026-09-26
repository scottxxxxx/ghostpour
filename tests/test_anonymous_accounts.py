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


@pytest.mark.parametrize("headers", [{}, {"X-App-ID": "techrehearsal"}, {"X-App-ID": "n400"}])
def test_only_shouldersurf_is_offered_anonymous_accounts(client, headers):
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
