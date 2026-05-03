"""Tests for the Resend send wrapper.

We don't hit the Resend API in tests — `httpx.AsyncClient` is patched
to return canned responses. Goal is to pin pre-send guards (suppression
check, missing API key) and the request shape we send to Resend.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from app.services import email_send


def _seed_suppression(db_path: str, recipient: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO email_suppression (recipient, reason, source_event_id, suppressed_at)"
        " VALUES (?, ?, ?, ?)",
        (recipient.lower(), "hard_bounce", "msg_test", now),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_skips_suppressed_recipient(client, tmp_db_path, monkeypatch):
    """If the recipient is on the suppression list, Resend is NOT called."""
    _seed_suppression(tmp_db_path, "blocked@example.com")
    monkeypatch.setenv("CZ_RESEND_API_KEY", "re_test_key")

    fake_post = AsyncMock()
    with patch("httpx.AsyncClient") as fake_client_cls:
        fake_client = AsyncMock()
        fake_client.post = fake_post
        fake_client_cls.return_value.__aenter__.return_value = fake_client

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            result = await email_send.send_email(
                db,
                to="blocked@example.com",
                subject="Tips digest",
                html="<p>hi</p>",
                from_addr="tips@example.com",
            )

    assert result.sent is False
    assert result.skipped_reason == "suppressed"
    fake_post.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_api_key_missing(client, tmp_db_path, monkeypatch):
    monkeypatch.delenv("CZ_RESEND_API_KEY", raising=False)
    from app import secrets as app_secrets
    monkeypatch.setattr(app_secrets, "_from_secret_manager", lambda name: "")
    app_secrets.get_secret.cache_clear()

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        result = await email_send.send_email(
            db,
            to="ok@example.com",
            subject="Tips digest",
            html="<p>hi</p>",
            from_addr="tips@example.com",
        )

    assert result.sent is False
    assert result.skipped_reason == "no_api_key"
    app_secrets.get_secret.cache_clear()


@pytest.mark.asyncio
async def test_successful_send_returns_resend_id(client, tmp_db_path, monkeypatch):
    monkeypatch.setenv("CZ_RESEND_API_KEY", "re_test_key")
    from app import secrets as app_secrets
    app_secrets.get_secret.cache_clear()

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b'{"id":"em_abc123"}'
    fake_resp.json.return_value = {"id": "em_abc123"}

    fake_post = AsyncMock(return_value=fake_resp)
    with patch("httpx.AsyncClient") as fake_client_cls:
        fake_client = AsyncMock()
        fake_client.post = fake_post
        fake_client_cls.return_value.__aenter__.return_value = fake_client

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            result = await email_send.send_email(
                db,
                to="ok@example.com",
                subject="Tips digest",
                html="<p>hi</p>",
                text="hi",
                from_addr="Tips <tips@example.com>",
                tags=[{"name": "campaign", "value": "weekly_tips"}],
            )

    assert result.sent is True
    assert result.resend_id == "em_abc123"
    assert result.status_code == 200

    # Verify the request shape sent to Resend
    fake_post.assert_called_once()
    _, kwargs = fake_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer re_test_key"
    body = kwargs["json"]
    assert body["from"] == "Tips <tips@example.com>"
    assert body["to"] == ["ok@example.com"]
    assert body["subject"] == "Tips digest"
    assert body["html"] == "<p>hi</p>"
    assert body["text"] == "hi"
    assert body["tags"] == [{"name": "campaign", "value": "weekly_tips"}]
    app_secrets.get_secret.cache_clear()


@pytest.mark.asyncio
async def test_provider_error_surfaces_status(client, tmp_db_path, monkeypatch):
    monkeypatch.setenv("CZ_RESEND_API_KEY", "re_test_key")
    from app import secrets as app_secrets
    app_secrets.get_secret.cache_clear()

    fake_resp = MagicMock()
    fake_resp.status_code = 422
    fake_resp.text = '{"name":"validation_error"}'
    fake_resp.content = b'{"name":"validation_error"}'

    with patch("httpx.AsyncClient") as fake_client_cls:
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=fake_resp)
        fake_client_cls.return_value.__aenter__.return_value = fake_client

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            result = await email_send.send_email(
                db,
                to="ok@example.com",
                subject="x",
                html="x",
                from_addr="from@example.com",
            )

    assert result.sent is False
    assert result.status_code == 422
    assert "validation_error" in (result.error or "")
    app_secrets.get_secret.cache_clear()
