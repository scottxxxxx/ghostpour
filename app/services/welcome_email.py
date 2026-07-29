"""The subscriber welcome letter (Scott, 2026-07-28).

A personal thank-you from the developer, sent once per customer about an
hour after their first paid subscription event. The delay is the point:
an instant send reads as automation; an hour reads as a person who
noticed. Durable queue + sweep because deploys restart the container
several times a day.

Rules:
- Enqueued from the tier-change chokepoint on paid events (upgrade,
  trial_start, trial_to_paid), NEVER for offer-code gifts (offer_id
  present): "thanks for paying" is the wrong letter for a gifted code.
- users.welcome_email_sent_at is the once-ever guard and outlives the
  queue row.
- Greeting keys off whether we know a NAME, not the email's privacy:
  Apple's relay hides the address, not the name.
- Copy approved verbatim by Scott (v7, 2026-07-28); no em/en dashes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.config import get_settings

logger = logging.getLogger("ghostpour.welcome_email")

PAID_EVENTS = {"upgrade", "trial_start", "trial_to_paid"}
SWEEP_INTERVAL_SECONDS = 300
MAX_ATTEMPTS = 12          # ~1h of retries at sweep cadence, then give up
SUBJECT = "Thank you for subscribing to Shoulder Surf"

_TIER_NAMES = {"plus": "Plus", "pro": "Pro"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def first_name(display_name: str | None) -> str:
    name = (display_name or "").strip().split(" ")[0] if display_name else ""
    return name or "there"


def _opening(tier_label: str, is_trial: bool) -> str:
    if is_trial:
        return (f"I'm Scott, the independent developer behind Shoulder Surf. "
                f"I saw that you started your free trial of {tier_label}, and "
                f"I wanted to thank you personally.")
    return (f"I'm Scott, the independent developer behind Shoulder Surf. I "
            f"saw that you subscribed to {tier_label}, and I wanted to thank "
            f"you personally.")


def render(display_name: str | None, tier: str, is_trial: bool) -> tuple[str, str, str]:
    """Return (subject, html, text) for one subscriber."""
    name = first_name(display_name)
    tier_label = _TIER_NAMES.get(tier, tier.capitalize())
    opening = _opening(tier_label, is_trial)
    value_word = tier_label if not is_trial else "your trial"

    html = f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background-color:#eef2f6;">
  <div style="display:none;max-height:0;overflow:hidden;">A personal thank you, and the one thing worth trying first.</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef2f6;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" width="540" cellpadding="0" cellspacing="0" style="max-width:540px;width:100%;">
        <tr><td style="padding:0 8px 16px 8px;" align="left">
          <img src="https://shouldersurf.com/logo.png" alt="Shoulder Surf" width="40" height="40" style="border-radius:9px;vertical-align:middle;">
          <span style="font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;font-size:18px;font-weight:600;color:#1e6293;vertical-align:middle;padding-left:10px;">Shoulder Surf</span>
        </td></tr>
        <tr><td style="background-color:#ffffff;border-radius:12px;border-top:4px solid #1e6293;padding:30px 38px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#2b3648;" align="left">
          <p style="margin:0 0 14px 0;">Hi {name},</p>
          <p style="margin:0 0 14px 0;">{opening}</p>
          <p style="margin:0 0 18px 0;">As an independent developer, every subscription matters to me. It means someone believes Shoulder Surf can make their work easier, and I take that responsibility seriously.</p>
          <p style="margin:0 0 8px 0;font-size:13px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:#1e6293;">One thing to try</p>
          <p style="margin:0 0 12px 0;">Once you have several related meetings, place them inside the same project and ask Project Chat a question that depends on all of them. For example:</p>
          <p style="margin:0 0 12px 0;padding:10px 14px;background-color:#f0f6fb;border-left:3px solid #1e6293;border-radius:6px;color:#46546b;font-style:italic;font-size:14px;">What decisions have we made, what is still unresolved, and who is responsible for each next step?</p>
          <p style="margin:0 0 18px 0;">That is where Shoulder Surf becomes more than a collection of recordings. It becomes a searchable memory of your work.</p>
          <p style="margin:0 0 18px 0;">If anything is confusing, does not work as expected, or keeps you from getting value from {value_word}, reply directly to this email. Your message comes to me, and I personally read every response.</p>
          <p style="margin:0 0 4px 0;">Thanks again for supporting Shoulder Surf.</p>
          <p style="margin:0 0 2px 0;">Scott</p>
          <p style="margin:0;font-size:13px;"><a href="mailto:scott@shouldersurf.com" style="color:#1e6293;text-decoration:none;">scott@shouldersurf.com</a></p>
          <p style="margin:22px 0 0 0;padding-top:14px;border-top:1px solid #e6ecf3;font-size:13px;color:#56627a;">PS. Guides and a feature tour at <a href="https://shouldersurf.com" style="color:#1e6293;text-decoration:none;">shouldersurf.com</a>, and other subscribers compare notes at <a href="https://www.reddit.com/r/ShoulderSurfers/" style="color:#1e6293;text-decoration:none;">r/ShoulderSurfers</a>.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    text = f"""Hi {name},

{opening}

As an independent developer, every subscription matters to me. It means
someone believes Shoulder Surf can make their work easier, and I take
that responsibility seriously.

ONE THING TO TRY

Once you have several related meetings, place them inside the same
project and ask Project Chat a question that depends on all of them. For
example:

  What decisions have we made, what is still unresolved, and who is
  responsible for each next step?

That is where Shoulder Surf becomes more than a collection of
recordings. It becomes a searchable memory of your work.

If anything is confusing, does not work as expected, or keeps you from
getting value from {value_word}, reply directly to this email. Your
message comes to me, and I personally read every response.

Thanks again for supporting Shoulder Surf.

Scott
scott@shouldersurf.com

PS. Guides and a feature tour: https://shouldersurf.com
Other subscribers compare notes: https://www.reddit.com/r/ShoulderSurfers/
"""
    return SUBJECT, html, text


async def enqueue(user_id: str, new_tier: str, event_type: str,
                  offer_id: str | None = None) -> None:
    """Queue the letter ~1h out for a first paid event. Best-effort; own
    connection (callers run in fire-and-forget tasks). No-ops when the
    feature is disabled, the event isn't a paid one, or the subscription
    came from an offer-code gift."""
    settings = get_settings()
    if not settings.welcome_email_enabled:
        return
    if event_type not in PAID_EVENTS or offer_id:
        return
    try:
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            row = await (await db.execute(
                "SELECT welcome_email_sent_at, is_trial, email FROM users "
                "WHERE id = ?", (user_id,))).fetchone()
            if row is None or row["welcome_email_sent_at"] or not row["email"]:
                return
            due = (_now() + timedelta(
                seconds=settings.welcome_email_delay_seconds)).isoformat()
            await db.execute(
                """INSERT OR IGNORE INTO welcome_email_queue
                   (user_id, tier, is_trial, due_at, enqueued_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, new_tier, int(bool(row["is_trial"])), due,
                 _now().isoformat()))
            await db.commit()
            logger.info("welcome_email enqueued user=%s tier=%s due=%s",
                        user_id[:8], new_tier, due)
        finally:
            await db.close()
    except Exception:
        logger.exception("welcome_email enqueue failed (non-fatal)")


async def sweep_once() -> int:
    """Send every due letter. Returns the number sent. Safe to call from
    startup and the background loop; the sent-at guard makes it idempotent."""
    settings = get_settings()
    if not settings.welcome_email_enabled:
        return 0
    sent = 0
    try:
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            due = await (await db.execute(
                "SELECT q.user_id, q.tier, q.is_trial, q.attempts, "
                "       u.display_name, u.email, u.welcome_email_sent_at "
                "FROM welcome_email_queue q JOIN users u ON u.id = q.user_id "
                "WHERE q.due_at <= ?", (_now().isoformat(),))).fetchall()
            for r in due:
                if r["welcome_email_sent_at"] or not r["email"]:
                    await db.execute(
                        "DELETE FROM welcome_email_queue WHERE user_id = ?",
                        (r["user_id"],))
                    await db.commit()
                    continue
                subject, html, text = render(
                    r["display_name"], r["tier"], bool(r["is_trial"]))
                from app.services.email_send import send_email
                result = await send_email(
                    db, to=r["email"], subject=subject, html=html, text=text,
                    from_addr=settings.welcome_email_from,
                    reply_to=settings.welcome_email_reply_to,
                    tags=[{"name": "kind", "value": "welcome"}])
                if result.sent or result.skipped_reason == "suppressed":
                    await db.execute(
                        "UPDATE users SET welcome_email_sent_at = ? WHERE id = ?",
                        (_now().isoformat(), r["user_id"]))
                    await db.execute(
                        "DELETE FROM welcome_email_queue WHERE user_id = ?",
                        (r["user_id"],))
                    await db.commit()
                    if result.sent:
                        sent += 1
                    logger.info("welcome_email %s user=%s",
                                "sent" if result.sent else "suppressed",
                                r["user_id"][:8])
                else:
                    attempts = (r["attempts"] or 0) + 1
                    if attempts >= MAX_ATTEMPTS:
                        await db.execute(
                            "DELETE FROM welcome_email_queue WHERE user_id = ?",
                            (r["user_id"],))
                        logger.warning(
                            "welcome_email giving up user=%s after %d attempts "
                            "(last: %s)", r["user_id"][:8], attempts,
                            result.skipped_reason or result.error)
                    else:
                        await db.execute(
                            "UPDATE welcome_email_queue SET attempts = ? "
                            "WHERE user_id = ?", (attempts, r["user_id"]))
                    await db.commit()
        finally:
            await db.close()
    except Exception:
        logger.exception("welcome_email sweep failed (non-fatal)")
    return sent


async def sweep_loop() -> None:
    """Background loop for the app lifespan."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        await sweep_once()
