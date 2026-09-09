"""Apple Ads install attribution: AdServices token exchange.

iOS grabs an attribution token via AAAttribution.attributionToken() on first
launch and POSTs it to POST /v1/attribution (app/routers/acquisition.py).
This service exchanges the token at Apple's AdServices API for the campaign,
ad group, and keyword that drove the install, and persists the outcome on
the ad_attribution row.

Exchange contract (Apple AdServices):
  POST https://api-adservices.apple.com/api/v1/   body = raw token, text/plain
    200: JSON payload. attribution=false means the install did not come from
         an Apple Ads tap (the organic denominator). attribution=true carries
         campaignId / adGroupId / keywordId / adId / conversionType /
         clickDate / countryOrRegion.
    404: attribution record not materialized yet (normal in the first
         seconds after install); retry later.
    400: token invalid; terminal error.
Tokens are exchangeable for 24h after generation. Rows still pending past
that window flip to 'expired'.

Personalized-ads-off devices return attribution=true with every id set to
the literal placeholder 1234567890. Those rows get standard_payload=1 and
read as "Apple Ads install, keyword unknown" in the dashboard.

There is no immediate exchange at ingest: the sweep daemon (60s cadence,
fail-soft, same shape as the other run_daemon services) is the single code
path, which also makes it restart-safe. Latency of up to a minute is fine
for analytics, and it satisfies Apple's "retry on 404" guidance for free.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

logger = logging.getLogger("ghostpour.apple_ads_attribution")

ADSERVICES_URL = "https://api-adservices.apple.com/api/v1/"
# Standard-payload marker: every id in the response equals this literal when
# the device has personalized ads off.
PLACEHOLDER_ID = 1234567890
TOKEN_TTL_HOURS = 24
SWEEP_INTERVAL_SECONDS = 60
_HTTP_TIMEOUT = 15.0


async def _post_token(token: str) -> tuple[int, dict | None]:
    """One exchange attempt. Returns (status_code, payload-or-None).
    The only place that talks to Apple, so tests patch here."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            ADSERVICES_URL,
            content=token,
            headers={"Content-Type": "text/plain"},
        )
    if resp.status_code == 200:
        try:
            return 200, resp.json()
        except ValueError:
            return 500, None
    return resp.status_code, None


async def exchange_row(db: aiosqlite.Connection, row: aiosqlite.Row) -> str:
    """Exchange one pending row's token and persist the outcome. Returns the
    resulting status; 'pending' means retry on a later sweep."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        code, payload = await _post_token(row["token"])
    except (httpx.HTTPError, OSError) as e:
        logger.warning(
            "adservices exchange transient failure id=%s: %s", row["id"], e
        )
        return "pending"

    if code == 404:
        return "pending"
    if code == 400:
        await db.execute(
            "UPDATE ad_attribution SET status='error', token=NULL,"
            " exchanged_at=? WHERE id=?",
            (now, row["id"]),
        )
        await db.commit()
        return "error"
    if code != 200 or payload is None:
        logger.warning(
            "adservices exchange unexpected status=%s id=%s", code, row["id"]
        )
        return "pending"

    if not payload.get("attribution"):
        await db.execute(
            "UPDATE ad_attribution SET status='organic', attribution=0,"
            " token=NULL, exchanged_at=? WHERE id=?",
            (now, row["id"]),
        )
        await db.commit()
        return "organic"

    standard = 1 if payload.get("campaignId") == PLACEHOLDER_ID else 0
    await db.execute(
        """UPDATE ad_attribution SET
             status='attributed', attribution=1,
             campaign_id=?, ad_group_id=?, keyword_id=?, ad_id=?,
             conversion_type=?, click_date=?, country_or_region=?,
             standard_payload=?, token=NULL, exchanged_at=?
           WHERE id=?""",
        (
            payload.get("campaignId"),
            payload.get("adGroupId"),
            payload.get("keywordId"),
            payload.get("adId"),
            payload.get("conversionType"),
            payload.get("clickDate"),
            payload.get("countryOrRegion"),
            standard,
            now,
            row["id"],
        ),
    )
    await db.commit()
    return "attributed"


# How stale the oldest unexchanged row may get before the sweep is presumed
# dead. The sweep runs every 60s, so anything past a few minutes means it is
# not running or not clearing. Deliberately well under TOKEN_TTL_HOURS: the
# point is to warn while the tokens are still exchangeable, not to announce
# the loss afterwards.
STALE_PENDING_SECONDS = 15 * 60


async def oldest_pending_age_seconds(
    db: aiosqlite.Connection, app_id: str | None = None
) -> float | None:
    """Age of the oldest row still waiting to be exchanged, or None if none.

    Derived entirely from the table rather than from a heartbeat the daemon
    writes, and that is the point: a process cannot be trusted to report its
    own death. A heartbeat also resets on restart and would read healthy
    exactly when a crash loop was losing tokens.
    """
    sql = ("SELECT MIN(created_at) AS oldest FROM ad_attribution "
           "WHERE status='pending'")
    params: tuple = ()
    if app_id:
        sql += " AND app_id = ?"
        params = (app_id,)
    cur = await db.execute(sql, params)
    row = await cur.fetchone()
    oldest = row["oldest"] if row else None
    if not oldest:
        return None
    try:
        ts = datetime.fromisoformat(oldest)
    except (TypeError, ValueError):
        logger.warning("ad_attribution unparseable created_at %r", oldest)
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


async def check_sweep_liveness(
    db: aiosqlite.Connection, app_id: str, *, from_addr: str = "alerts@noreply.invalid"
) -> float | None:
    """Alert if tokens are arriving and the sweep is not clearing them.

    ⚠ Called from the INGEST path, not from the daemon. A dead daemon cannot
    report itself, which is the whole failure this exists to catch. Ingest is
    also the right trigger by cost: with no tokens arriving there is nothing
    to lose, and every arriving token starts a 24-hour clock.

    Never raises: an alert must not fail the request that noticed the problem.
    """
    try:
        age = await oldest_pending_age_seconds(db, app_id)
        if age is None or age < STALE_PENDING_SECONDS:
            return age
        from app.services.alerting import report_incident
        logger.error(
            "attribution_sweep_stalled app=%s oldest_pending_age_s=%.0f",
            app_id, age,
        )
        await report_incident(
            db,
            category="attribution_sweep_stalled",
            subject=app_id,
            details={
                "app_id": app_id,
                "oldest_pending_age_seconds": int(age),
                "token_ttl_hours": TOKEN_TTL_HOURS,
                "sweep_interval_seconds": SWEEP_INTERVAL_SECONDS,
            },
            from_addr=from_addr,
        )
        return age
    except Exception as e:  # noqa: BLE001 — never break ingest
        logger.warning("attribution liveness check failed (non-fatal): %s", e)
        return None


async def sweep_pending(db: aiosqlite.Connection) -> dict[str, int]:
    """Process every pending row: expire past-TTL ones, exchange the rest.
    Returns outcome counters for logging/tests."""
    counts = {"expired": 0, "attributed": 0, "organic": 0, "error": 0, "pending": 0}

    ttl_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=TOKEN_TTL_HOURS)
    ).isoformat()
    cur = await db.execute(
        "UPDATE ad_attribution SET status='expired', token=NULL"
        " WHERE status='pending' AND created_at < ?",
        (ttl_cutoff,),
    )
    counts["expired"] = cur.rowcount or 0
    await db.commit()

    # Expiry is unrecoverable: the campaign and keyword behind those installs
    # can never be learned. Loud, not a debug counter, because the sweep
    # writing this number is also the only thing that ever sees it.
    if counts["expired"]:
        logger.error(
            "attribution_tokens_expired count=%d — these installs have no "
            "recoverable source", counts["expired"],
        )
        try:
            from app.services.alerting import report_incident
            await report_incident(
                db,
                category="attribution_tokens_expired",
                subject="apple_ads",
                details={"expired": counts["expired"],
                         "token_ttl_hours": TOKEN_TTL_HOURS},
            )
        except Exception as e:  # noqa: BLE001 — the sweep must never die
            logger.warning("expired-token alert failed (non-fatal): %s", e)

    cur = await db.execute(
        "SELECT * FROM ad_attribution WHERE status='pending' AND token IS NOT NULL"
    )
    rows = await cur.fetchall()
    for row in rows:
        outcome = await exchange_row(db, row)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


async def run_daemon(app) -> None:
    """Periodic exchange sweep. Fail-soft: an iteration failure logs and the
    loop continues; cancellation propagates for clean shutdown."""
    db_path = app.state.settings.database_url.replace("sqlite+aiosqlite:///", "")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                counts = await sweep_pending(db)
                if any(v for k, v in counts.items() if k != "pending"):
                    logger.info("ad_attribution sweep %s", counts)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 sweep must never die
            logger.warning("ad_attribution sweep failed: %s", e)
