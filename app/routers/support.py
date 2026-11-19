"""SS Help "Report a problem": POST /v1/support/message emails support.

Contract from SS Dev (2026-11-19). Auth is OPTIONAL: Help works signed out,
and an invalid or expired token is treated as anonymous, never a 401, so a
stale token cannot eat a bug report. The message body goes to our own inbox
only and is never logged here; logs carry the outcome, length and ids.

Errors are top-level {"error": ...} as the client expects, so the body is
validated by hand rather than by a Pydantic model (which would answer 422).
"""
from __future__ import annotations

import html
import logging
import re
import time
import uuid
from collections import defaultdict, deque

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user import UserRecord
from app.routers.telemetry import _client_ip, _ip_hash
from app.services.email_send import send_email

logger = logging.getLogger("ghostpour.support")
router = APIRouter()

SUPPORT_TO = "support@shouldersurf.com"
SUPPORT_FROM = "Shoulder Surf Help <noreply@mail.shouldersurf.com>"
MAX_MESSAGE = 4000
PER_HOUR = 5
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_META = ("source", "article_id", "app", "app_version", "os_version", "device", "locale")
_hits: dict[str, deque] = defaultdict(deque)


def _allowed(key: str, now: float | None = None) -> bool:
    """At most PER_HOUR sends per key in a rolling hour (in-memory)."""
    now = time.time() if now is None else now
    q = _hits[key]
    while q and q[0] <= now - 3600:
        q.popleft()
    if len(q) >= PER_HOUR:
        return False
    q.append(now)
    return True


def _err(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


@router.post("/support/message")
async def support_message(
    request: Request,
    user: UserRecord | None = Depends(get_current_user_optional),
    db: aiosqlite.Connection = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        return _err("invalid_message")
    if not isinstance(body, dict):
        return _err("invalid_message")
    message = body.get("message")
    message = message.strip() if isinstance(message, str) else ""
    if not 1 <= len(message) <= MAX_MESSAGE:
        return _err("invalid_message")
    reply_email = body.get("reply_email")
    if reply_email is not None:
        reply_email = reply_email.strip() if isinstance(reply_email, str) else None
        if not reply_email or len(reply_email) > 254 or not _EMAIL.match(reply_email):
            return _err("invalid_email")
    meta = {k: str(body.get(k))[:200] for k in _META if body.get(k) is not None}

    key = f"user:{user.id}" if user else f"ip:{_ip_hash(_client_ip(request)) or 'unknown'}"
    if not _allowed(key):
        return _err("rate_limited", 429)

    msg_id = uuid.uuid4().hex[:12]
    rows = [("id", msg_id), ("reply_email", reply_email or "(none)")]
    rows += [(k, v) for k, v in meta.items()]
    if user:
        rows += [("user_id", user.id), ("tier", user.effective_tier)]
    first = " ".join(message.split())[:60]
    text = message + "\n\n" + "\n".join(f"{k}: {v}" for k, v in rows)
    page = (f"<p style=\"white-space:pre-wrap\">{html.escape(message)}</p><hr>"
            + "".join(f"<div><b>{html.escape(k)}</b>: {html.escape(str(v))}</div>" for k, v in rows))
    result = await send_email(
        db, to=SUPPORT_TO, subject=f"[SS Help] {first}", html=page, text=text,
        from_addr=SUPPORT_FROM, reply_to=reply_email,
        tags=[{"name": "kind", "value": "support"}])
    logger.info("support_message id=%s sent=%s len=%d signed_in=%s source=%s",
                msg_id, result.sent, len(message), bool(user), meta.get("source"))
    if not result.sent:
        return _err("send_failed", 502)
    return {"status": "sent", "id": msg_id, "reply_email": reply_email}
