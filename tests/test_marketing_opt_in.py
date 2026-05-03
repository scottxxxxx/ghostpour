"""Tests for the marketing opt-in service module: token gen/verify
and the user CRUD helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import aiosqlite
import pytest

from app.services import marketing_opt_in as marketing


# ---------------------------------------------------------------------------
# Token gen / verify
# ---------------------------------------------------------------------------

class TestTokenGen:
    def test_generate_then_verify_round_trips_user_id(self):
        secret = "test-jwt-secret-32-chars-long-yes-it-is"
        tok = marketing.generate_unsubscribe_token("user-123", secret)
        assert marketing.verify_unsubscribe_token(tok, secret) == "user-123"

    def test_verify_rejects_wrong_secret(self):
        tok = marketing.generate_unsubscribe_token("user-1", "secret-A")
        assert marketing.verify_unsubscribe_token(tok, "secret-B") is None

    def test_verify_rejects_tampered_user_id(self):
        secret = "test-secret"
        tok = marketing.generate_unsubscribe_token("user-1", secret)
        # Mangle the user_id portion — signature won't match
        mangled = tok.replace(tok.split(".")[0], "Zm9yZ2VkLXVpZA")
        assert marketing.verify_unsubscribe_token(mangled, secret) is None

    def test_verify_rejects_garbage(self):
        secret = "test-secret"
        for bad in ["", "not-a-token", "x.y", "..", "abc.def.ghi"]:
            assert marketing.verify_unsubscribe_token(bad, secret) is None

    def test_token_does_not_contain_user_id_in_plaintext(self):
        # Encoded but not plain — paranoid check
        tok = marketing.generate_unsubscribe_token("alice@example.com", "s")
        assert "alice@example.com" not in tok

    def test_two_users_get_different_tokens(self):
        secret = "s"
        t1 = marketing.generate_unsubscribe_token("user-1", secret)
        t2 = marketing.generate_unsubscribe_token("user-2", secret)
        assert t1 != t2

    def test_empty_user_id_or_secret_raises(self):
        with pytest.raises(ValueError):
            marketing.generate_unsubscribe_token("", "secret")
        with pytest.raises(ValueError):
            marketing.generate_unsubscribe_token("user-1", "")


# ---------------------------------------------------------------------------
# DB CRUD
# ---------------------------------------------------------------------------

def _seed_user(db_path: str, user_id: str = "u1", email: str = "u1@example.com") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, tier, created_at, updated_at,
            is_active, monthly_used_usd, overage_balance_usd, marketing_opt_in)
           VALUES (?, ?, ?, 'free', ?, ?, 1, 0, 0, 0)""",
        (user_id, f"sub_{user_id}", email, now, now),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_default_opt_in_is_off(client, tmp_db_path):
    _seed_user(tmp_db_path, "u-default")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        state = await marketing.get_marketing_opt_in(db, "u-default")
    assert state == {"opt_in": False, "updated_at": None, "source": None}


@pytest.mark.asyncio
async def test_set_opt_in_returns_changed_first_time(client, tmp_db_path):
    _seed_user(tmp_db_path, "u-set")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        changed = await marketing.set_marketing_opt_in(
            db, "u-set", opt_in=True, source=marketing.SOURCE_IOS,
        )
        assert changed is True
        # Same value again: no-op
        changed2 = await marketing.set_marketing_opt_in(
            db, "u-set", opt_in=True, source=marketing.SOURCE_IOS,
        )
        assert changed2 is False


@pytest.mark.asyncio
async def test_set_records_source_and_timestamp(client, tmp_db_path):
    _seed_user(tmp_db_path, "u-meta")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        await marketing.set_marketing_opt_in(
            db, "u-meta", opt_in=True, source=marketing.SOURCE_IOS,
        )
        state = await marketing.get_marketing_opt_in(db, "u-meta")
    assert state["opt_in"] is True
    assert state["source"] == marketing.SOURCE_IOS
    assert state["updated_at"] is not None


@pytest.mark.asyncio
async def test_set_unknown_user_returns_false(client, tmp_db_path):
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        changed = await marketing.set_marketing_opt_in(
            db, "no-such-user", opt_in=True, source="ios_toggle",
        )
    assert changed is False


@pytest.mark.asyncio
async def test_opt_out_by_recipient_matches_case_insensitive(client, tmp_db_path):
    _seed_user(tmp_db_path, "u-bymail", email="MixedCase@Example.com")
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        # First opt them in so we can verify opt_out flips it
        await marketing.set_marketing_opt_in(
            db, "u-bymail", opt_in=True, source=marketing.SOURCE_IOS,
        )
        flipped = await marketing.opt_out_by_recipient(
            db, "MIXEDCASE@EXAMPLE.COM",
            source=marketing.SOURCE_SPAM_COMPLAINT,
        )
        assert flipped is True
        state = await marketing.get_marketing_opt_in(db, "u-bymail")
    assert state["opt_in"] is False
    assert state["source"] == marketing.SOURCE_SPAM_COMPLAINT


@pytest.mark.asyncio
async def test_opt_out_unknown_recipient_is_noop(client, tmp_db_path):
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        flipped = await marketing.opt_out_by_recipient(
            db, "no-such-user@example.com", source=marketing.SOURCE_SPAM_COMPLAINT,
        )
    assert flipped is False
