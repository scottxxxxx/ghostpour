"""Critical-failure alerting.

Operator-facing email alerts for system-level outages that have nothing
to do with a single user's query — managed-provider key revoked,
managed-provider monthly limit exhausted, CQ unreachable, etc. Targets
the "silent failure for 2 weeks" footgun that prompted the feature.

## Contract

Callers report an incident with three fields:

  - category: a stable token from `KNOWN_CATEGORIES`. Defines the
    semantic class (CQ unreachable vs provider auth vs budget).
  - subject: a short identifier of WHAT is broken inside that category.
    For provider failures, the provider id ("openai", "anthropic").
    For CQ, fixed string "cq". Becomes part of the dedup fingerprint.
  - details: an arbitrary dict captured for the incident record.
    Stringified to JSON; surfaces in the dashboard. **Must not contain
    secrets** — callers redact at call site.

The service:

  1. Computes `fingerprint = category + ":" + subject`.
  2. Looks for an OPEN incident (resolved_at IS NULL) with this
     fingerprint. If found, increments trigger_count + last_seen_at;
     does NOT re-email. This is the "once per incident" suppression.
  3. If no open incident, creates a row, looks up the recipient list,
     emails everyone subscribed to this category, records who got the
     email.

Auto-resolution: there is no explicit "I'm better now" hook. Instead,
on each `report_incident` call we sweep any open row whose
last_seen_at is older than `INCIDENT_AUTO_RESOLVE_MINUTES` and mark it
resolved. Next failure with the same fingerprint opens a fresh row
(and re-fires the alert). This is the "auto-clear after quiet
window" behavior promised in the PR design.

## Categories

  - cq_unreachable: CQ connection timeouts / connection-refused / 5xx
    from the CQ host. Wired in `app/routers/cq_proxy.py`.
  - provider_auth_failed: 401/403 from one of our MANAGED keys (NOT
    BYOK). Means our key rotated/revoked/billing lapsed.
  - provider_budget_exhausted: provider responded with a billing/quota
    error (HTTP 402, or 429 with `insufficient_quota` shape) on a
    managed-key call.

BYOK failures NEVER trigger an incident — those are end-user issues,
not system-level. Caller must distinguish at the wire site.

## Pure-function vs DB

`report_incident` writes to the DB and may dispatch HTTP (Resend). It
should be called from request-path code with the existing connection.
The auto-resolve sweep is bounded — a few rows max per call.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.services.email_send import send_email

logger = logging.getLogger(__name__)


# Auto-resolve any open incident whose last_seen_at is older than this.
# Chosen so a real outage that re-fires (last_seen rolls forward) stays
# open until ~30 min of quiet, then closes. Re-fire after auto-close
# re-emails — that's the "alert me again if it comes back" behavior.
INCIDENT_AUTO_RESOLVE_MINUTES = 30


# Single source of truth for the categories the dashboard renders, the
# recipient picker filters against, and the wire-site callers reference.
# Add a category here, wire it at the failure site, and the UI picks it
# up automatically.
KNOWN_CATEGORIES: dict[str, dict] = {
    "cq_unreachable": {
        "label": "Context Quilt unreachable",
        "description": (
            "GP's connection to the Context Quilt API is timing out or "
            "refused. Affects recall + capture for all users."
        ),
    },
    "provider_auth_failed": {
        "label": "Managed LLM key rejected",
        "description": (
            "A 401/403 from one of our managed provider keys. Likely "
            "the key was rotated/revoked, or billing on that provider "
            "account lapsed. Does NOT include BYOK auth failures."
        ),
    },
    "provider_budget_exhausted": {
        "label": "Managed LLM budget exhausted",
        "description": (
            "A managed provider returned a quota/billing exceeded "
            "error (HTTP 402 or 429 with insufficient_quota / "
            "credit_balance_too_low). Time to top up that provider."
        ),
    },
    "cert_pin_auto_republish": {
        "label": "Cert pin auto-republish needs attention",
        "description": (
            "The daily auto-republish task either failed outright or "
            "detected that the live TLS chain pins differ from the "
            "previously published manifest (LE rotated, or worse a MITM). "
            "Check /admin and the cert pin runbook before iOS picks up "
            "the new manifest."
        ),
    },
}


@dataclass
class IncidentReport:
    """Result of `report_incident`. Mostly for tests + dashboard
    feedback; production callers can ignore it."""
    incident_id: str
    is_new: bool
    emailed_to: list[str]
    suppressed_reason: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(category: str, subject: str) -> str:
    return f"{category}:{subject}"


async def _sweep_stale_incidents(db: aiosqlite.Connection) -> int:
    """Mark any open incident as resolved when its last_seen is older
    than INCIDENT_AUTO_RESOLVE_MINUTES. Returns count resolved.

    Called at the top of report_incident so a re-fire after a quiet
    window starts a fresh row (and re-emails). Cheap because the
    `idx_alert_incidents_open` partial index covers WHERE resolved_at
    IS NULL."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=INCIDENT_AUTO_RESOLVE_MINUTES)
    ).isoformat()
    cursor = await db.execute(
        "UPDATE alert_incidents SET resolved_at = ? "
        "WHERE resolved_at IS NULL AND last_seen_at < ?",
        (_utcnow_iso(), cutoff),
    )
    return cursor.rowcount or 0


async def _active_recipients_for(
    db: aiosqlite.Connection, category: str,
) -> list[tuple[str, str | None]]:
    """Return (email, display_name) tuples for active recipients
    subscribed to `category`. Recipients with no category filter
    (NULL or "[]") receive everything."""
    cursor = await db.execute(
        "SELECT email, display_name, categories FROM alert_recipients "
        "WHERE active = 1 ORDER BY email"
    )
    rows = await cursor.fetchall()
    out: list[tuple[str, str | None]] = []
    for row in rows:
        raw = row[2] if not isinstance(row, dict) else row["categories"]
        if raw is None or raw == "":
            out.append((row[0], row[1]))
            continue
        try:
            cats = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            cats = None
        if not cats:
            # Empty list = all categories (same as null).
            out.append((row[0], row[1]))
        elif category in cats:
            out.append((row[0], row[1]))
    return out


def _render_email_html(
    category: str, subject: str, details: dict,
    incident_id: str, first_seen_at: str,
) -> tuple[str, str]:
    """Return (subject_line, html_body) for the outgoing alert email."""
    cat_label = KNOWN_CATEGORIES.get(category, {}).get("label", category)
    cat_desc = KNOWN_CATEGORIES.get(category, {}).get("description", "")
    subject_line = f"[GhostPour] {cat_label} — {subject}"

    detail_lines = ""
    for k, v in (details or {}).items():
        val = str(v)
        if len(val) > 500:
            val = val[:500] + "…"
        detail_lines += (
            f"<tr><td style='padding:4px 12px;color:#666;vertical-align:top'>"
            f"{k}</td><td style='padding:4px 12px;font-family:monospace;"
            f"word-break:break-all'>{val}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222">
<div style="max-width:600px;margin:0 auto;padding:24px">
  <div style="background:#fff4e5;border-left:4px solid #f59e0b;padding:16px 20px;margin-bottom:24px">
    <div style="font-size:14px;color:#92400e;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">
      Critical failure detected
    </div>
    <div style="font-size:18px;font-weight:600;margin-top:6px">{cat_label}</div>
    <div style="font-size:14px;color:#555;margin-top:4px">Subject: {subject}</div>
  </div>
  <p style="color:#444;line-height:1.5">{cat_desc}</p>
  <div style="border:1px solid #e5e5e5;border-radius:4px;margin-top:20px">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <tr><td style="padding:4px 12px;color:#666">First seen</td><td style="padding:4px 12px">{first_seen_at}</td></tr>
      <tr><td style="padding:4px 12px;color:#666">Incident ID</td><td style="padding:4px 12px;font-family:monospace">{incident_id}</td></tr>
      <tr><td style="padding:4px 12px;color:#666">Category</td><td style="padding:4px 12px;font-family:monospace">{category}</td></tr>
      {detail_lines}
    </table>
  </div>
  <p style="color:#888;font-size:12px;margin-top:32px">
    Sent because you're subscribed to GhostPour critical-failure alerts. The
    next email for this same fingerprint won't fire until at least
    {INCIDENT_AUTO_RESOLVE_MINUTES} minutes of quiet pass.
  </p>
</div>
</body></html>
"""
    return subject_line, html


async def report_incident(
    db: aiosqlite.Connection,
    *,
    category: str,
    subject: str,
    details: dict | None = None,
    from_addr: str = "alerts@noreply.invalid",
) -> IncidentReport:
    """Report a critical failure. Idempotent per (category, subject)
    fingerprint while the incident is open.

    Returns an `IncidentReport` describing what happened — useful for
    tests, dashboard wiring, and unit-level verification. Production
    callers can call-and-forget; this function never raises out of
    band for downstream failures (e.g., Resend transport errors are
    logged-but-swallowed because alerting must not break the
    request that triggered it).

    `from_addr` defaults to a noreply placeholder; production callers
    should pass settings.alert_email_from. We accept the placeholder
    so unit tests don't need to wire env."""
    if category not in KNOWN_CATEGORIES:
        logger.warning(
            "alerting.report_incident: unknown category=%r — recording anyway",
            category,
        )

    details = details or {}
    fingerprint = _fingerprint(category, subject)
    now = _utcnow_iso()

    # 1) Auto-resolve stale opens so a re-fire after quiet starts fresh.
    try:
        await _sweep_stale_incidents(db)
    except Exception as exc:
        logger.warning("alerting.sweep_failed reason=%s", exc)

    # 2) Look up open incident by fingerprint.
    cursor = await db.execute(
        "SELECT id, trigger_count, first_seen_at FROM alert_incidents "
        "WHERE fingerprint = ? AND resolved_at IS NULL",
        (fingerprint,),
    )
    row = await cursor.fetchone()

    if row is not None:
        # Existing open incident — bump counter, no re-email.
        await db.execute(
            "UPDATE alert_incidents "
            "SET last_seen_at = ?, trigger_count = trigger_count + 1, "
            "    details_json = ? "
            "WHERE id = ?",
            (now, json.dumps(details), row[0]),
        )
        await db.commit()
        return IncidentReport(
            incident_id=row[0],
            is_new=False,
            emailed_to=[],
            suppressed_reason="incident_already_open",
        )

    # 3) New incident — insert row.
    incident_id = str(uuid.uuid4())
    recipients = await _active_recipients_for(db, category)

    await db.execute(
        "INSERT INTO alert_incidents "
        "(id, category, subject, fingerprint, first_seen_at, last_seen_at, "
        " trigger_count, details_json) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
        (
            incident_id, category, subject, fingerprint,
            now, now, json.dumps(details),
        ),
    )
    await db.commit()

    if not recipients:
        return IncidentReport(
            incident_id=incident_id,
            is_new=True,
            emailed_to=[],
            suppressed_reason="no_recipients",
        )

    subject_line, html = _render_email_html(
        category, subject, details, incident_id, now,
    )

    emailed: list[str] = []
    for email, _name in recipients:
        try:
            result = await send_email(
                db,
                to=email,
                subject=subject_line,
                html=html,
                from_addr=from_addr,
                tags=[
                    {"name": "purpose", "value": "critical-alert"},
                    {"name": "category", "value": category},
                    # `stack` tag partitions traffic on the shared
                    # Resend account so analytics can cleanly split
                    # GP alerts from CQ alerts (CQ uses stack=cq on
                    # their side; coordinated with CQ team 2026-05-21).
                    {"name": "stack", "value": "gp"},
                ],
            )
            if result.sent:
                emailed.append(email)
        except Exception as exc:
            # Never propagate alerting failures out — log loudly so
            # we don't swallow forever, but the request that triggered
            # this alert must not be impacted by Resend transport
            # issues or anything else downstream.
            logger.exception(
                "alerting.send_failed category=%s subject=%s recipient=%s reason=%s",
                category, subject, email, exc,
            )

    if emailed:
        await db.execute(
            "UPDATE alert_incidents SET email_sent_at = ?, "
            "emailed_recipients = ? WHERE id = ?",
            (now, json.dumps(emailed), incident_id),
        )
        await db.commit()
        logger.info(
            "alerting.incident_opened category=%s subject=%s incident_id=%s "
            "emailed=%d",
            category, subject, incident_id, len(emailed),
        )

    return IncidentReport(
        incident_id=incident_id,
        is_new=True,
        emailed_to=emailed,
    )


async def list_incidents(
    db: aiosqlite.Connection, *, limit: int = 100,
) -> list[dict]:
    """For dashboard history view. Includes open + resolved, newest first."""
    cursor = await db.execute(
        "SELECT id, category, subject, fingerprint, first_seen_at, "
        "       last_seen_at, trigger_count, details_json, "
        "       email_sent_at, emailed_recipients, resolved_at "
        "FROM alert_incidents ORDER BY first_seen_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": r[0],
            "category": r[1],
            "subject": r[2],
            "fingerprint": r[3],
            "first_seen_at": r[4],
            "last_seen_at": r[5],
            "trigger_count": r[6],
            "details": json.loads(r[7]) if r[7] else None,
            "email_sent_at": r[8],
            "emailed_recipients": json.loads(r[9]) if r[9] else [],
            "resolved_at": r[10],
            "status": "open" if r[10] is None else "resolved",
        })
    return out
