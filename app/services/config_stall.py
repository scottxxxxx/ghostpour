"""Detect an iOS client stuck in the poisoned-config-cache loop.

SS confirmed the mechanism (RemoteConfigManager.loadConfig:147/162): if a
cached config fails to decode, the client deletes the cache and falls back
to the copy bundled in the app. The next sync therefore negotiates from
the bundled version, we return the full payload, it fails to decode, and
the cache is deleted again. It repeats on every launch, forever, for as
long as that build is installed. Nothing about it reaches telemetry: the
only trace on the device is a local log line.

We can see it from the server without the client shipping anything,
because config fetches are authenticated and carry `X-Config-Version`:

    healthy   client presents an advancing version -> "not changed"
    poisoned  client presents the SAME version forever -> full payload

So the signature is a user whose presented version never moves while we
keep handing over full payloads. What separates that from the single
full-payload fetch every healthy client makes right after we publish is
repetition over time, which is why the alert needs both an occurrence
count and an elapsed span rather than a bare counter: a client that
fetches three times during one launch has not proven anything.

Deliberately keyed on the presented version rather than the served one.
Publishing a new config changes the server version but a poisoned client
is still stuck on the same bundled number, so the row (and its history)
survives our releases instead of resetting on every publish.

Best-effort throughout: config delivery must never fail because the
bookkeeping did.
"""

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger(__name__)

# A poisoned client re-fetches once per launch, so a handful of launches
# spread over most of a day is the point where "stuck" beats "the user
# opened the app three times this morning".
ALERT_MIN_OCCURRENCES = 4
ALERT_MIN_SPAN_HOURS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record_full_payload(
    db: aiosqlite.Connection,
    *,
    user_id: str | None,
    app_id: str,
    config_name: str,
    client_version: int | None,
    server_version: int,
    app_build: str | None,
    app_version: str | None,
) -> None:
    """Note that `user_id` took a full payload while behind `server_version`.

    Called only when we actually serve the body. A client with no version
    header (first install, pre-login fetch) is not tracked: it has not yet
    claimed to hold anything, so it cannot be stuck holding it.
    """
    if not user_id or client_version is None:
        return
    try:
        now = _now().isoformat()
        # Anything the client has already moved past is proof it decoded and
        # cached successfully, so retire those rows rather than let a healed
        # client keep an aging stall on the books.
        await db.execute(
            "DELETE FROM config_stalls WHERE user_id = ? AND app_id = ? "
            "AND config_name = ? AND client_version < ?",
            (user_id, app_id, config_name, client_version))
        await db.execute(
            """INSERT INTO config_stalls
                 (user_id, app_id, config_name, client_version, server_version,
                  app_build, app_version, occurrences, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,1,?,?)
               ON CONFLICT(user_id, app_id, config_name, client_version) DO UPDATE SET
                 occurrences = occurrences + 1,
                 last_seen_at = excluded.last_seen_at,
                 server_version = excluded.server_version,
                 app_build = excluded.app_build,
                 app_version = excluded.app_version""",
            (user_id, app_id, config_name, client_version, server_version,
             app_build, app_version, now, now))
        await db.commit()
    except Exception:
        logger.exception("config_stall: could not record full payload")


async def clear(
    db: aiosqlite.Connection, *, user_id: str | None, app_id: str,
    config_name: str, client_version: int,
) -> None:
    """The client is holding `client_version` and asked for nothing new, so
    it decoded and cached successfully. Any stall recorded at or below that
    version is resolved."""
    if not user_id:
        return
    try:
        await db.execute(
            "DELETE FROM config_stalls WHERE user_id = ? AND app_id = ? "
            "AND config_name = ? AND client_version <= ?",
            (user_id, app_id, config_name, client_version))
        await db.commit()
    except Exception:
        logger.exception("config_stall: could not clear resolved stall")


async def due_alerts(db: aiosqlite.Connection) -> list[dict]:
    """Stalls that have crossed both thresholds and not yet been alerted."""
    cutoff = (_now() - timedelta(hours=ALERT_MIN_SPAN_HOURS)).isoformat()
    rows = await (await db.execute(
        "SELECT * FROM config_stalls WHERE alerted_at IS NULL "
        "AND occurrences >= ? AND first_seen_at <= ?",
        (ALERT_MIN_OCCURRENCES, cutoff))).fetchall()
    return [dict(r) for r in rows]


async def alert_stalled_clients(db: aiosqlite.Connection) -> int:
    """Email one incident per stuck (user, app, config). Returns the count.

    Separate from recording so the hot config path never pays for alert
    delivery, and so the thresholds can be re-evaluated on a sweep even if
    the client goes quiet.
    """
    from app.config import get_settings
    from app.services.alerting import report_incident

    fired = 0
    for row in await due_alerts(db):
        try:
            await report_incident(
                db,
                category="config_decode_loop",
                subject=f"{row['app_id']}:{row['config_name']}:"
                        f"{row['user_id'][:8]}:v{row['client_version']}",
                details={
                    "user_id": row["user_id"],
                    "app_id": row["app_id"],
                    "config": row["config_name"],
                    "stuck_on_version": row["client_version"],
                    "server_version": row["server_version"],
                    "app_build": row["app_build"],
                    "app_version": row["app_version"],
                    "full_payloads_served": row["occurrences"],
                    "first_seen": row["first_seen_at"],
                    "last_seen": row["last_seen_at"],
                    "meaning": (
                        "This client has taken the full payload repeatedly "
                        "without ever advancing its config version. That is "
                        "the signature of a decode failure looping on every "
                        "launch: it will not recover on its own, and the "
                        "device is running its bundled config. Check what "
                        "changed in this config against what this build "
                        "requires."
                    ),
                },
                from_addr=get_settings().alert_email_from,
            )
            await db.execute(
                "UPDATE config_stalls SET alerted_at = ? WHERE user_id = ? "
                "AND app_id = ? AND config_name = ? AND client_version = ?",
                (_now().isoformat(), row["user_id"], row["app_id"],
                 row["config_name"], row["client_version"]))
            await db.commit()
            fired += 1
        except Exception:
            logger.exception("config_stall: alert failed for %s", row.get("user_id"))
    return fired


async def run_daemon(app) -> None:
    """Lifespan-spawned sweep. Alerting lives here rather than on the config
    path so a hot request never pays for email delivery, and so a client
    that goes quiet after poisoning itself still gets reported.

    Fail-soft: a bad tick must not kill the loop.
    """
    import asyncio

    await asyncio.sleep(45.0)
    while True:
        try:
            settings = app.state.settings
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                n = await alert_stalled_clients(db)
            if n:
                logger.warning(
                    "config_stall: %d client(s) stuck on bundled config", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("config_stall sweep tick failed: %s", e)
        try:
            await asyncio.sleep(
                getattr(app.state.settings,
                        "config_stall_sweep_interval_seconds", 3600))
        except asyncio.CancelledError:
            return
