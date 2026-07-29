"""Subscriber welcome letter: enqueue rules, once-ever guard, sweep,
template variants. Copy approved verbatim by Scott 2026-07-28."""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _insert_user


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _queue(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT user_id, tier, is_trial FROM welcome_email_queue").fetchall()
    finally:
        conn.close()


def _set(db_path, sql, *args):
    conn = sqlite3.connect(db_path)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _enable(monkeypatch, delay=0):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "welcome_email_enabled", True, raising=False)
    monkeypatch.setattr(s, "welcome_email_delay_seconds", delay, raising=False)


# --- template ---------------------------------------------------------------

def test_render_variants_and_no_dashes():
    from app.services.welcome_email import render
    for name, tier, trial, expect in [
        ("Elijah Capudoy", "plus", True, "started your free trial of Plus"),
        ("John Kirker", "pro", False, "subscribed to Pro"),
        (None, "plus", False, "Hi there,"),
    ]:
        subject, html, text = render(name, tier, trial)
        assert "—" not in html + text + subject
        assert "–" not in html + text + subject
        assert expect in html or expect in text
    subject, html, _ = render("Elijah Capudoy", "plus", True)
    assert "Hi Elijah," in html
    assert "scott@shouldersurf.com" in html
    # tier-exclusive tips (Scott 2026-07-29): Pro showcases file
    # generation; Plus keeps Project Chat.
    _, pro_html, pro_text = render("John Kirker", "pro", False)
    assert "Word document" in pro_html and "follow-up work" in pro_html
    assert "Project Chat" not in pro_html
    _, plus_html, _ = render("Elijah Capudoy", "plus", False)
    assert "Project Chat" in plus_html
    assert "Word document" not in plus_html


# --- enqueue rules ----------------------------------------------------------

@pytest.mark.anyio
async def test_enqueue_on_paid_event_and_not_for_gifts(client, app_env, monkeypatch):
    from app.services.welcome_email import enqueue
    _enable(monkeypatch)
    _insert_user(_db(app_env), "w-payer")
    _insert_user(_db(app_env), "w-gifted")
    _set(_db(app_env), "UPDATE users SET email='p@example.com' WHERE id='w-payer'")
    _set(_db(app_env), "UPDATE users SET email='g@example.com' WHERE id='w-gifted'")

    await enqueue("w-payer", "plus", "upgrade")
    await enqueue("w-gifted", "pro", "upgrade", offer_id="friend-x")
    await enqueue("w-payer", "plus", "downgrade")

    rows = _queue(_db(app_env))
    assert rows == [("w-payer", "plus", 0)]


@pytest.mark.anyio
async def test_enqueue_noops_when_disabled_or_already_welcomed(client, app_env, monkeypatch):
    from app.services.welcome_email import enqueue
    _insert_user(_db(app_env), "w-dark")
    _set(_db(app_env), "UPDATE users SET email='d@example.com' WHERE id='w-dark'")
    await enqueue("w-dark", "plus", "upgrade")          # disabled by default
    assert _queue(_db(app_env)) == []

    _enable(monkeypatch)
    _set(_db(app_env),
         "UPDATE users SET welcome_email_sent_at='2026-07-01T00:00:00Z' "
         "WHERE id='w-dark'")
    await enqueue("w-dark", "plus", "upgrade")          # guard: once ever
    assert _queue(_db(app_env)) == []


# --- sweep ------------------------------------------------------------------

@pytest.mark.anyio
async def test_sweep_sends_once_and_sets_guard(client, app_env, monkeypatch):
    from app.services import welcome_email as we
    _enable(monkeypatch, delay=0)
    _insert_user(_db(app_env), "w-due")
    _set(_db(app_env),
         "UPDATE users SET email='due@example.com', display_name='Dana Test' "
         "WHERE id='w-due'")
    await we.enqueue("w-due", "plus", "upgrade")
    assert len(_queue(_db(app_env))) == 1

    from app.services.email_send import SendResult
    fake = AsyncMock(return_value=SendResult(
        sent=True, skipped_reason=None, resend_id="em_w1",
        status_code=200, error=None))
    with patch("app.services.email_send.send_email", fake):
        sent = await we.sweep_once()
    assert sent == 1
    kwargs = fake.call_args.kwargs
    assert kwargs["to"] == "due@example.com"
    assert "Hi Dana," in kwargs["html"]
    assert kwargs["reply_to"].endswith("<scott@shouldersurf.com>")

    assert _queue(_db(app_env)) == []                    # row consumed
    conn = sqlite3.connect(_db(app_env))
    guard = conn.execute(
        "SELECT welcome_email_sent_at FROM users WHERE id='w-due'").fetchone()[0]
    conn.close()
    assert guard

    # second sweep: nothing to do, nothing re-sent
    with patch("app.services.email_send.send_email", fake):
        assert await we.sweep_once() == 0
