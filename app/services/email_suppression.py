"""Email suppression list and webhook event recording.

The suppression list is the single source of truth for "do not send."
Hard bounces and spam complaints add rows here; outbound code checks
`is_suppressed` before invoking the email provider.

`record_event` is called from the webhook router for every accepted
event (after signature verification + idempotency dedupe).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _normalize(recipient: str) -> str:
    return recipient.strip().lower()


async def is_suppressed(db: aiosqlite.Connection, recipient: str) -> bool:
    if not recipient:
        return False
    cursor = await db.execute(
        "SELECT 1 FROM email_suppression WHERE recipient = ? LIMIT 1",
        (_normalize(recipient),),
    )
    row = await cursor.fetchone()
    return row is not None


async def add_suppression(
    db: aiosqlite.Connection,
    recipient: str,
    reason: str,
    source_event_id: str | None = None,
) -> bool:
    """Add a recipient to the suppression list. Returns True if newly added,
    False if already present (no-op for idempotency)."""
    if not recipient:
        return False
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """INSERT INTO email_suppression (recipient, reason, source_event_id, suppressed_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(recipient) DO NOTHING""",
        (_normalize(recipient), reason, source_event_id, now),
    )
    await db.commit()
    return cursor.rowcount > 0


async def already_recorded(db: aiosqlite.Connection, event_id: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM email_events WHERE id = ? LIMIT 1", (event_id,)
    )
    return (await cursor.fetchone()) is not None


async def record_event(
    db: aiosqlite.Connection,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    recipient: str | None = None,
    email_id: str | None = None,
    bounce_type: str | None = None,
) -> None:
    """Idempotent: if event_id is already present, this is a no-op."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO email_events
           (id, event_type, recipient, email_id, bounce_type, payload, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO NOTHING""",
        (
            event_id,
            event_type,
            _normalize(recipient) if recipient else None,
            email_id,
            bounce_type,
            json.dumps(payload),
            now,
        ),
    )
    await db.commit()
