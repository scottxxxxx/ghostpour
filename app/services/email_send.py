"""Outbound email sending via Resend.

Wraps the Resend HTTP API. Pre-send checks against `email_suppression`
so we never send to addresses we've already had hard-bounce or
spam-complaint signals from. The webhook ingest in
`app/routers/resend_webhooks.py` is the source of those rows.

API key is resolved through `app.secrets.get_secret` — env var first,
then GCP Secret Manager. Sender domain is read from settings; the
domain MUST be verified in the Resend dashboard with DKIM/SPF set up
before any production sends.

This module is the only place `httpx` should call api.resend.com.
Callers go through `send_email(...)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite
import httpx

from app.secrets import get_secret
from app.services import email_suppression

logger = logging.getLogger(__name__)

_RESEND_BASE = "https://api.resend.com"


@dataclass
class SendResult:
    sent: bool
    skipped_reason: str | None = None  # e.g. "suppressed", "no_api_key"
    resend_id: str | None = None       # Resend's email_id on success
    status_code: int | None = None
    error: str | None = None


async def send_email(
    db: aiosqlite.Connection,
    *,
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    from_addr: str,
    headers: dict[str, str] | None = None,
    tags: list[dict[str, str]] | None = None,
    timeout_seconds: float = 10.0,
) -> SendResult:
    """Send a single email via Resend.

    Pre-send guards:
    - If `to` is in the suppression list → skip without calling Resend.
    - If the API key is unavailable → skip with `no_api_key`. The
      webhook secret is configured separately and isn't required for
      sending.

    Returns a `SendResult` describing what happened. Outbound logs
    aren't persisted here — they show up in `email_events` once Resend
    fires the webhook callback for the send.
    """
    if await email_suppression.is_suppressed(db, to):
        logger.info("email_send: skipped (suppressed): to=%s subject=%r", to, subject)
        return SendResult(sent=False, skipped_reason="suppressed")

    api_key = get_secret("resend-api-key", env_var="CZ_RESEND_API_KEY")
    if not api_key:
        logger.error(
            "email_send: skipped (no resend api key): to=%s subject=%r",
            to, subject,
        )
        return SendResult(sent=False, skipped_reason="no_api_key")

    payload: dict[str, object] = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if headers:
        payload["headers"] = headers
    if tags:
        payload["tags"] = tags

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                f"{_RESEND_BASE}/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning("email_send: transport error: %s", exc)
        return SendResult(sent=False, error=str(exc))

    if resp.status_code >= 400:
        logger.warning(
            "email_send: provider error %s: %s",
            resp.status_code, resp.text[:500],
        )
        return SendResult(
            sent=False,
            status_code=resp.status_code,
            error=resp.text,
        )

    body = resp.json() if resp.content else {}
    resend_id = body.get("id")
    logger.info(
        "email_send: ok to=%s subject=%r resend_id=%s",
        to, subject, resend_id,
    )
    return SendResult(sent=True, resend_id=resend_id, status_code=resp.status_code)
