"""Critical alerts pushed to the operator's iPhone through the SS app
(Scott, 2026-09-29, after the spend-cap outage).

Email alone let the Anthropic spend cap take production down unnoticed. So
every NEW incident in a pushed category also sends an APNs alert to the
devices of the users named in the server-only `operator-alerts` config. The
recipients and categories live in that doc, so changing either is a config
edit, not a deploy. Read from disk at send time: incidents are rare.

Plain title and body (no loc-key): these are operator messages in English,
and SS's String Catalog has no strings for them. No routing keys, so a tap
just opens the app.

Never raises. An alert that fails to push must not affect the request that
raised the incident, and the email path runs regardless.
"""
from __future__ import annotations

import json
import logging
import time

import aiosqlite
import httpx

from app.services import apns, device_tokens

logger = logging.getLogger(__name__)

DOC = "operator-alerts"


def _config() -> dict:
    from app.routers.config import CONFIG_DIR
    try:
        return json.loads((CONFIG_DIR / f"{DOC}.json").read_text())
    except (OSError, ValueError):
        return {}


def payload_for(category: str, label: str, subject: str) -> dict:
    return {"aps": {"alert": {"title": f"GhostPour: {label}", "body": subject[:180]},
                    "sound": "default", "interruption-level": "time-sensitive"},
            "operator_alert": category}


async def push_incident(db: aiosqlite.Connection, *, category: str, label: str,
                        subject: str, settings, collapse_key: str | None = None) -> dict:
    """Push one new incident to the operator's phones. Returns the outcome."""
    out: dict = {"sent": 0, "skipped": None, "results": []}
    try:
        cfg = _config()
        if category not in set(cfg.get("push_categories") or []):
            out["skipped"] = "category_not_pushed"
            return out
        if not apns.configured(settings):
            out["skipped"] = "apns_not_configured"
            return out
        rows = []
        for uid in cfg.get("push_user_ids") or []:
            rows += await device_tokens.for_user(db, uid)
        if not rows:
            out["skipped"] = "no_operator_devices"
            logger.warning("operator_push: no registered devices for the configured operators")
            return out
        payload = payload_for(category, label, subject)
        async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
            for row in rows:
                try:
                    r = await apns.send_to_token(client, db, row=row, settings=settings, payload=payload,
                                                 expiration=int(time.time()) + 6 * 3600,
                                                 collapse_id=f"op-{category}-{collapse_key or subject}")
                except Exception as e:  # noqa: BLE001
                    r = f"exception_{type(e).__name__}"
                out["results"].append(r)
                if r.startswith("sent"):
                    out["sent"] += 1
        logger.info("operator_push category=%s subject=%s devices=%d sent=%d results=%s",
                    category, subject, len(rows), out["sent"], ",".join(out["results"]))
    except Exception:  # noqa: BLE001
        logger.exception("operator_push failed category=%s", category)
        out["skipped"] = "exception"
    return out
