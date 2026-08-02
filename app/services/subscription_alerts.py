"""Ops email when someone pays (Scott's ask 2026-07-27).

Rides the existing alerting service so recipients are dashboard-
configurable per category like every other alert. Unlike failure
categories, every purchase should email: the incident subject embeds
the event timestamp, so each event gets a fresh fingerprint and the
open-incident dedup never suppresses one.

Fires on the money events only: upgrade (new paid sub or tier move
up), trial_start (a subscription in Apple's eyes), and trial_to_paid
(the conversion). Downgrades, expiries, refunds, cancellations, and
account deletions stay out of this category; the webhook/dashboard
already records those.

Production only (2026-08-02). Sandbox subscriptions renew and lapse on
their own fast cadence, so a handful of TestFlight testers generated a
steady drip of "new Pro subscriber" mail for purchases that were never
real; two such alerts in one morning is what surfaced this. A purchase
whose environment we cannot determine still emails, tagged as unknown:
a missed real sale costs more than a spurious alert.
"""

import logging
from datetime import datetime, timezone

from app.config import get_settings

logger = logging.getLogger(__name__)

PAID_EVENTS = {"upgrade", "trial_start", "trial_to_paid"}


async def notify_purchase(user_id: str, old_tier: str, new_tier: str,
                          event_type: str,
                          offer_id: str | None = None,
                          environment: str | None = None) -> None:
    """Best-effort: never raises into the caller. Opens its own DB
    connection because callers run inside fire-and-forget tasks whose
    request-scoped connection may already be gone."""
    if event_type not in PAID_EVENTS:
        return
    try:
        import aiosqlite

        settings = get_settings()
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            # Callers that know the environment pass it. For the rest, the
            # account's own history answers it: a TestFlight tester has
            # Sandbox events behind them, which is the population this gate
            # exists to silence.
            if environment is None:
                prior = await (await db.execute(
                    "SELECT environment FROM subscription_events "
                    "WHERE user_id = ? AND environment IS NOT NULL "
                    "ORDER BY recorded_at DESC LIMIT 1", (user_id,))).fetchone()
                environment = prior["environment"] if prior else None
            if environment == "Sandbox":
                logger.info(
                    "subscription_alerts: skipping %s alert for %s — Sandbox "
                    "(TestFlight), not a real purchase",
                    event_type, user_id[:8])
                return

            cursor = await db.execute(
                "SELECT email FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            email = row["email"] if row else "(unknown)"
            now = datetime.now(timezone.utc)
            from app.services.alerting import report_incident
            await report_incident(
                db,
                category="subscription_purchase",
                # unique-per-event subject => unique fingerprint => every
                # purchase emails instead of deduping into one incident
                # (uuid tail: a bare-second timestamp collided in tests)
                subject=f"{user_id[:8]}:{event_type}:"
                        f"{now.strftime('%Y%m%dT%H%M%S')}."
                        f"{__import__('uuid').uuid4().hex[:6]}",
                details={
                    "user_id": user_id,
                    "email": email,
                    "event": event_type,
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                    "occurred_at": now.isoformat(),
                    # Surfaced so an unverifiable purchase is visibly
                    # unverified in the inbox rather than silently trusted.
                    "environment": environment or "unknown",
                    # ASC offer reference name when the purchase came from
                    # an offer code — the "which friend/campaign was this"
                    # signal in the operator's inbox.
                    **({"offer": offer_id} if offer_id else {}),
                },
                from_addr=settings.alert_email_from,
            )
        finally:
            await db.close()
    except Exception:
        logger.exception("subscription_alerts: purchase notify failed (non-fatal)")
