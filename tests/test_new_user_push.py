"""A push to Scott's phones for every new account, with user counts
(Scott, 2026-10-02). Hooks: new Sign in with Apple, new anonymous purchase
account. Not: a returning sign-in, a repeat anonymous call, an anonymous
account converting to Apple."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import aiosqlite
import pytest

from app.services import new_user_push, operator_push

SS = {"X-App-ID": "shouldersurf"}
VERIFY = "app.services.apple_auth.AppleAuthVerifier.verify_identity_token"


@pytest.fixture
def scheduled(monkeypatch):
    calls = []
    monkeypatch.setattr(new_user_push, "schedule",
                        lambda uid, kind, app_id, settings: calls.append((kind, app_id)))
    return calls


def _apple(client, sub, anonymous_token=None):
    body = {"identity_token": "mock"}
    if anonymous_token:
        body["anonymous_token"] = anonymous_token
    with patch(VERIFY, return_value={"sub": sub, "email": f"{sub}@privaterelay.appleid.com"}):
        return client.post("/auth/apple", json=body, headers=SS)


def _anon(client, install):
    return client.post("/auth/anonymous", json={"install_id": install}, headers=SS)


def test_a_new_apple_account_pushes_once_and_a_returning_one_never(client, scheduled):
    assert _apple(client, "apple-new-1").status_code == 200
    assert _apple(client, "apple-new-1").status_code == 200
    assert scheduled == [("apple", "shouldersurf")]


def test_a_new_anonymous_account_pushes_once(client, scheduled):
    install = str(uuid.uuid4())
    assert _anon(client, install).status_code == 200
    assert _anon(client, install).status_code == 200
    assert scheduled == [("anonymous", "shouldersurf")]


def test_converting_an_anonymous_account_to_apple_is_not_a_new_user(client, scheduled):
    token = _anon(client, str(uuid.uuid4())).json()["access_token"]
    assert _apple(client, "apple-convert-1", anonymous_token=token).json()["merge"] == "converted"
    assert scheduled == [("anonymous", "shouldersurf")]


# --- counts and message -------------------------------------------------------------

def _seed(db_path):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=3)).isoformat()
    conn = sqlite3.connect(db_path)
    users = [("u-old", "free", old), ("u-new", "free", now.isoformat()),
             ("u-bot", "automation", now.isoformat()), ("u-quiet", "free", old)]
    for uid, tier, created in users:
        conn.execute("INSERT INTO users (id, apple_sub, tier, created_at, updated_at) VALUES (?,?,?,?,?)",
                     (uid, "s-" + uid, tier, created, created))
    for uid, when in (("u-old", now), ("u-old", now), ("u-new", now), ("u-bot", now),
                      ("u-quiet", now - timedelta(days=2))):
        conn.execute("INSERT INTO usage_log (id, user_id, provider, model, request_timestamp, status)"
                     " VALUES (?,?,?,?,?,'success')", (str(uuid.uuid4()), uid, "a", "m", when.isoformat()))
    conn.commit(); conn.close()


def test_counts_leave_out_test_accounts_and_count_people_not_calls(client, tmp_db_path):
    _seed(tmp_db_path)

    async def go():
        async with aiosqlite.connect(tmp_db_path) as db:
            return await new_user_push.counts(db)
    c = asyncio.run(go())
    assert c == {"total": 3, "new_today": 1, "active_today": 2}


def test_the_message_says_how_and_where_and_the_counts():
    m = new_user_push.message("apple", "shouldersurf", {"total": 214, "new_today": 3, "active_today": 17})
    assert m == ("Signed in with Apple on Shoulder Surf. Users: 214 total, 3 new today, "
                 "17 active today.")
    assert new_user_push.message("anonymous", "x", {"total": 1, "new_today": 1, "active_today": 0}
                                 ).startswith("No sign-in (purchase account) on x.")


# --- the push itself ----------------------------------------------------------------

@pytest.fixture
def phone(tmp_path, monkeypatch):
    cfg = tmp_path / "remote-config"
    cfg.mkdir()
    (cfg / "operator-alerts.json").write_text(json.dumps({
        "version": 1, "server_only": True, "push_user_ids": ["op-1"],
        "push_categories": ["new_user"]}))
    monkeypatch.setattr("app.routers.config.CONFIG_DIR", cfg)
    monkeypatch.setattr(operator_push.apns, "configured", lambda s: True)
    sent = []

    async def fake_send(client, db, *, row, settings, payload, expiration, collapse_id):
        sent.append((payload, collapse_id))
        return "sent"
    monkeypatch.setattr(operator_push.apns, "send_to_token", fake_send)

    async def for_user(db, uid):
        return [{"device_token": "tok", "bundle_id": "com.ss", "environment": "sandbox"}] if uid == "op-1" else []
    monkeypatch.setattr(operator_push.device_tokens, "for_user", for_user)
    new_user_push._sent_at.clear()
    return sent


def test_a_new_user_reaches_the_phone_with_the_counts(client, tmp_db_path, phone):
    _seed(tmp_db_path)
    out = asyncio.run(new_user_push.run("u-new", "apple", "shouldersurf", settings=None))
    assert out["sent"] == 1
    payload, collapse_id = phone[0]
    assert payload["aps"]["alert"]["title"] == "GhostPour: New user"
    assert payload["aps"]["alert"]["body"].endswith("3 total, 1 new today, 2 active today.")
    assert collapse_id == "op-new_user-u-new"  # one per person, never collapsed together


def test_the_hourly_cap_stops_a_flood(client, tmp_db_path, phone, monkeypatch):
    monkeypatch.setattr(new_user_push, "MAX_PER_HOUR", 2)
    outs = [asyncio.run(new_user_push.run(f"u{i}", "anonymous", "shouldersurf", settings=None))
            for i in range(3)]
    assert [o["sent"] for o in outs] == [1, 1, 0]
    assert outs[2]["skipped"] == "hourly_cap"
