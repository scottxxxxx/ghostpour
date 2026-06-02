"""Auto-republish daemon + dashboard banner status for cert pin manifests.

The signed manifest at /v1/config/cert-pins (PR #207) carries an
`expires_at`. iOS treats an expired manifest as fail-open (falls back
to bootstrap pins), so a stale manifest doesn't break anything, but it
does mean we lose the dynamic pin protection. This module keeps the
manifest fresh without anyone watching a calendar.

Two responsibilities:

1. `maybe_auto_republish` — daily background task. If the latest
   manifest expires within `_AUTO_REPUBLISH_THRESHOLD_DAYS`, fetch the
   live TLS chain off our own host, take the intermediate + roots
   (everything above the leaf), and publish a new monotonic version.
   If the resulting pin set differs from the previously published
   manifest, fire an incident — LE may have rotated an intermediate
   (expected, rare) or something more concerning is going on.

2. `compute_status` — read-only, called by the admin status endpoint.
   Returns the banner dict the dashboard renders. Three bands:
   green (>14d to expiry), yellow (in the auto-republish window),
   red (failed last attempt OR signing not configured OR no manifest).

The module holds a small in-memory record of the last check so the
dashboard can show "last auto check N min ago" without us paying for a
DB column. Lost on restart, but the next 24h tick rebuilds it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.config import Settings
from app.services.cert_pin_signing import (
    CertPinSigningError,
    fetch_current_chain_pins,
    latest_manifest,
    publish_manifest,
)

logger = logging.getLogger("ghostpour.cert_pin_auto_republish")

# Republish when the current manifest expires within this many days.
# 7 gives us a week of buffer to react if the auto path hits a snag.
_AUTO_REPUBLISH_THRESHOLD_DAYS = 7

# Show a yellow banner when expiry is within this many days even if we
# haven't crossed the auto-republish line yet — operator visibility.
_BANNER_WARN_THRESHOLD_DAYS = 14

# When auto-republishing, use this validity window. Matches the manual
# default in webhooks.py so the cadence stays consistent.
_AUTO_REPUBLISH_DAYS_VALID = 60

# Daemon tick rate. Daily is plenty — the threshold is in days.
_CHECK_INTERVAL_SECONDS = 86400

# Sentinel value written into cert_pin_manifest.created_by_admin_key_suffix
# so audit queries can tell auto-published rows from manual ones.
_AUTO_KEY_SUFFIX = "auto-republish"


@dataclass
class CheckResult:
    """In-memory record of the most recent auto-republish attempt.
    Surfaced via the admin status endpoint."""
    checked_at: datetime
    action: str  # "republished" | "noop_healthy" | "noop_no_signing" | "failed"
    version_after: int | None
    detail: str

    def to_dict(self) -> dict:
        return {
            "checked_at": self.checked_at.isoformat().replace("+00:00", "Z"),
            "action": self.action,
            "version_after": self.version_after,
            "detail": self.detail,
        }


# Module-level state: only the background task writes; the read endpoint
# reads. No lock needed because Python attribute reads/writes on a dict
# are atomic under the GIL for our access pattern.
_last_check: CheckResult | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime:
    """Parse the ISO strings we ourselves write into the manifest. They
    are always UTC, ending in 'Z' or '+00:00'."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# --- Auto-republish --------------------------------------------------------


async def maybe_auto_republish(
    db: aiosqlite.Connection,
    settings: Settings,
) -> CheckResult:
    """One tick of the daemon. Returns the same CheckResult that's
    cached in module state for the status endpoint."""
    global _last_check

    now = _now()

    # No signing key configured = nothing this module can do. Common in
    # local dev. Don't alert, just record and move on.
    if not (settings.cert_pin_signing_key_raw_b64 or "").strip():
        result = CheckResult(
            checked_at=now,
            action="noop_no_signing",
            version_after=None,
            detail="CZ_CERT_PIN_SIGNING_KEY_RAW_B64 not configured; skipping",
        )
        _last_check = result
        return result

    current = await latest_manifest(db)
    needs_republish = False
    days_remaining: float | None = None

    if current is None:
        # Fresh deployment — go publish v1 right now using the live chain.
        needs_republish = True
        prior_pins: list[str] = []
    else:
        prior_pins = list(current["pins"])
        expires_at = _parse_iso(current["expires_at"])
        days_remaining = (expires_at - now).total_seconds() / 86400.0
        if days_remaining <= _AUTO_REPUBLISH_THRESHOLD_DAYS:
            needs_republish = True

    if not needs_republish:
        result = CheckResult(
            checked_at=now,
            action="noop_healthy",
            version_after=current["version"] if current else None,
            detail=f"manifest healthy, {days_remaining:.1f}d to expiry",
        )
        _last_check = result
        return result

    # Republish path. Anything that goes wrong here gets logged + alerted.
    try:
        host = settings.cert_pin_self_host
        live_pins = await asyncio.to_thread(fetch_current_chain_pins, host)
    except Exception as e:
        logger.error("cert_pin auto-republish: chain fetch failed: %s", e)
        await _alert(
            db, settings,
            subject="chain_fetch_failed",
            details={
                "host": settings.cert_pin_self_host,
                "error": str(e),
                "prior_version": current["version"] if current else None,
            },
        )
        result = CheckResult(
            checked_at=now,
            action="failed",
            version_after=current["version"] if current else None,
            detail=f"chain fetch failed: {e}",
        )
        _last_check = result
        return result

    pins_changed = bool(prior_pins) and set(live_pins) != set(prior_pins)

    try:
        manifest = await publish_manifest(
            db, settings,
            pins=live_pins,
            days_valid=_AUTO_REPUBLISH_DAYS_VALID,
            admin_key_suffix=_AUTO_KEY_SUFFIX,
        )
    except CertPinSigningError as e:
        logger.error("cert_pin auto-republish: signing failed: %s", e)
        await _alert(
            db, settings,
            subject="signing_failed",
            details={
                "error": str(e),
                "prior_version": current["version"] if current else None,
            },
        )
        result = CheckResult(
            checked_at=now,
            action="failed",
            version_after=current["version"] if current else None,
            detail=f"signing failed: {e}",
        )
        _last_check = result
        return result

    if pins_changed:
        # Operator alert: pins are different from last publish. Could
        # be a legitimate LE intermediate rotation, could be something
        # bad. Auto-republish shipped it (per the user's chosen policy)
        # but we want eyes on it.
        await _alert(
            db, settings,
            subject="pin_set_changed",
            details={
                "prior_version": current["version"],
                "new_version": manifest["version"],
                "prior_pins": prior_pins,
                "new_pins": live_pins,
                "host": settings.cert_pin_self_host,
            },
        )

    result = CheckResult(
        checked_at=now,
        action="republished",
        version_after=manifest["version"],
        detail=(
            f"v{manifest['version']} published, {len(live_pins)} pins, "
            f"expires {manifest['expires_at']}, pins_changed={pins_changed}"
        ),
    )
    _last_check = result
    return result


# --- Banner status ---------------------------------------------------------


def compute_status(
    *,
    signing_configured: bool,
    current: dict | None,
    last_check: CheckResult | None,
    now: datetime | None = None,
) -> dict:
    """Pure function for the dashboard banner. No DB access here so the
    admin endpoint can call it after a quick read. Caller passes in the
    bits this module already knows how to fetch."""
    now = now or _now()

    if not signing_configured:
        return {
            "level": "red",
            "text": "Cert pin signing not configured. CZ_CERT_PIN_SIGNING_KEY_RAW_B64 missing.",
            "version": None,
            "expires_at": None,
            "days_remaining": None,
            "last_check": last_check.to_dict() if last_check else None,
        }

    if current is None:
        return {
            "level": "red",
            "text": "No cert pin manifest published yet. iOS is on bootstrap pins.",
            "version": None,
            "expires_at": None,
            "days_remaining": None,
            "last_check": last_check.to_dict() if last_check else None,
        }

    expires_at = _parse_iso(current["expires_at"])
    days_remaining = (expires_at - now).total_seconds() / 86400.0

    # Red trumps yellow trumps green.
    if last_check and last_check.action == "failed":
        level = "red"
        text = f"Auto-republish FAILED at last check. {last_check.detail}"
    elif days_remaining <= 0:
        level = "red"
        text = f"Cert pin manifest EXPIRED {abs(days_remaining):.1f}d ago. Republish now."
    elif days_remaining <= _AUTO_REPUBLISH_THRESHOLD_DAYS:
        level = "yellow"
        text = (
            f"Cert pin manifest v{current['version']} expires in "
            f"{days_remaining:.1f}d. Auto-republish will fire on the next daily tick."
        )
    elif days_remaining <= _BANNER_WARN_THRESHOLD_DAYS:
        level = "yellow"
        text = (
            f"Cert pin manifest v{current['version']} expires in "
            f"{days_remaining:.1f}d. Auto-republish kicks in at "
            f"{_AUTO_REPUBLISH_THRESHOLD_DAYS}d remaining."
        )
    else:
        level = "green"
        text = (
            f"Cert pin manifest v{current['version']} healthy, "
            f"{days_remaining:.1f}d to expiry."
        )

    return {
        "level": level,
        "text": text,
        "version": current["version"],
        "expires_at": current["expires_at"],
        "days_remaining": round(days_remaining, 2),
        "last_check": last_check.to_dict() if last_check else None,
    }


def get_last_check() -> CheckResult | None:
    return _last_check


# --- Background task wiring ------------------------------------------------


async def run_daemon(app) -> None:
    """Lifespan-spawned daemon. Tick on startup (after a brief delay so
    the rest of lifespan finishes first), then every _CHECK_INTERVAL_SECONDS.
    Fail-soft: an exception in any tick must not kill the loop."""
    # Brief delay so init logs aren't tangled with the first check.
    await asyncio.sleep(5.0)
    while True:
        try:
            settings = app.state.settings
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
            async with aiosqlite.connect(db_path) as db:
                result = await maybe_auto_republish(db, settings)
            logger.info(
                "cert_pin auto-check action=%s version_after=%s detail=%s",
                result.action, result.version_after, result.detail,
            )
        except Exception as e:
            logger.warning("cert_pin auto-check tick failed: %s", e)
        try:
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return


# --- Alerting helper -------------------------------------------------------


async def _alert(
    db: aiosqlite.Connection,
    settings: Settings,
    *,
    subject: str,
    details: dict,
) -> None:
    """Wrap alerting.report_incident so a missing alerting config can't
    kill the auto-republish path. Fire and forget."""
    try:
        from app.services.alerting import report_incident
        await report_incident(
            db,
            category="cert_pin_auto_republish",
            subject=subject,
            details=details,
            from_addr=settings.alert_email_from,
        )
    except Exception as e:
        logger.warning("cert_pin auto-republish: alert dispatch failed: %s", e)
