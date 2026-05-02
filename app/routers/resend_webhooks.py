"""Resend webhook ingestion.

Single endpoint: POST /webhooks/resend.

Verifies the Svix-style signature on every request, dedupes by svix-id,
records the event in `email_events`, and adds hard bounces / spam
complaints to `email_suppression` so we never send to those addresses
again.

This router is read-only with respect to user state — it doesn't flip
`marketing_opt_in` or any user-facing flag yet. That arrives in the
slice that wires outbound sending and the iOS preference toggle. For
now, the suppression list is the binding "do not send" surface; sending
code (when it exists) MUST consult `email_suppression.is_suppressed`
before calling Resend.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.database import get_db
from app.secrets import get_secret
from app.services import email_suppression

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_signing_secret() -> str:
    secret = get_secret("resend-webhook-secret", env_var="CZ_RESEND_WEBHOOK_SECRET")
    if not secret:
        # Fail closed — refusing to verify is safer than accepting unverified
        # webhooks. This will surface as 503s in logs until the secret is
        # provisioned in Secret Manager / the env.
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    return secret


def _verify_signature(secret: str, body: bytes, headers) -> None:
    """Verify a Svix signature. Raises HTTPException(401) on failure.

    We delegate to the `svix` package for correctness (constant-time
    comparison, timestamp tolerance, multi-signature handling).
    """
    try:
        from svix.webhooks import Webhook, WebhookVerificationError  # type: ignore[import-not-found]
    except ImportError:
        logger.error("svix package not installed; cannot verify Resend webhook")
        raise HTTPException(status_code=503, detail="webhook verification unavailable")

    try:
        wh = Webhook(secret)
        # Svix accepts a dict-like for headers; FastAPI Headers is dict-compatible.
        wh.verify(body, dict(headers))
    except WebhookVerificationError as exc:
        logger.warning("Resend webhook signature verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid signature")


def _extract_recipient(data: dict[str, Any]) -> str | None:
    to = data.get("to")
    if isinstance(to, list) and to:
        return str(to[0])
    if isinstance(to, str):
        return to
    return None


def _extract_bounce_type(data: dict[str, Any]) -> str | None:
    bounce = data.get("bounce")
    if isinstance(bounce, dict):
        bt = bounce.get("type")
        return str(bt) if bt else None
    return None


@router.post("/resend")
async def receive_resend_webhook(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Ingest a Resend webhook event.

    Returns 200 with `{"status": "ok"}` for any event we successfully
    process or recognize as a duplicate. Returns 401 for bad signatures
    and 503 if the secret is unavailable. Unknown event types are
    accepted (recorded + 200) so Resend doesn't retry indefinitely
    while we add support.
    """
    secret = _resolve_signing_secret()
    body = await request.body()
    _verify_signature(secret, body, request.headers)

    try:
        event: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    # Svix message id — globally unique, used as our idempotency key.
    event_id = request.headers.get("svix-id")
    if not event_id:
        raise HTTPException(status_code=400, detail="missing svix-id header")

    if await email_suppression.already_recorded(db, event_id):
        logger.info("Resend webhook %s already processed; skipping", event_id)
        return {"status": "ok", "duplicate": True}

    event_type = event.get("type", "unknown")
    data = event.get("data", {}) or {}
    recipient = _extract_recipient(data)
    email_id = data.get("email_id") or data.get("id")
    bounce_type = _extract_bounce_type(data) if event_type == "email.bounced" else None

    await email_suppression.record_event(
        db,
        event_id=event_id,
        event_type=event_type,
        payload=event,
        recipient=recipient,
        email_id=email_id if isinstance(email_id, str) else None,
        bounce_type=bounce_type,
    )

    if event_type == "email.bounced":
        if bounce_type == "hard" and recipient:
            added = await email_suppression.add_suppression(
                db, recipient, reason="hard_bounce", source_event_id=event_id
            )
            logger.info(
                "Resend hard bounce: recipient=%s newly_suppressed=%s",
                recipient, added,
            )
        else:
            logger.info(
                "Resend soft bounce: recipient=%s (no suppression)",
                recipient,
            )

    elif event_type == "email.complained":
        if recipient:
            added = await email_suppression.add_suppression(
                db, recipient, reason="spam_complaint", source_event_id=event_id
            )
            logger.warning(
                "Resend spam complaint: recipient=%s newly_suppressed=%s",
                recipient, added,
            )

    elif event_type in ("email.delivered", "email.sent", "email.delivery_delayed"):
        logger.info("Resend %s: recipient=%s email_id=%s", event_type, recipient, email_id)

    else:
        logger.info("Resend webhook unknown event_type=%s id=%s", event_type, event_id)

    return {"status": "ok"}
