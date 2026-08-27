"""The recall timeout, as a dial you can turn without a deploy.

Scott, 2026-08-27: raised 500 -> 1500 after the degrade data showed the
budget was the binding constraint, not headroom (every degrade in the 3
days since #790 was a TIMEOUT on a FULL-scope recall; 44% of full-scope
recalls degraded on 08-27 against 0% of People-scoped ones, and CQ
measured the matching request at 740ms server-side). He also ruled that
it should be a served dial rather than an env var, so the next move is a
dashboard edit while CQ's traversal work lands, not an SSH session.

Resolution order, and why: the served dial when it is a sane integer,
else `cq_recall_timeout_ms` from settings (env `CZ_CQ_RECALL_TIMEOUT_MS`
in prod, else the code default). The dial wins because it is the thing
an operator can move in seconds; settings remain the floor so a missing
or malformed config can never leave the timeout undefined.

BOUNDS ARE NOT DECORATION. This number is a hold on every chat turn: a
fat-fingered 150000 would make every slow recall hang the user for two
and a half minutes, and a 0 would degrade every recall instantly and
silently (the exact failure #790 exists to surface). Out-of-range values
are refused and logged, and the settings value is used instead.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ghostpour.recall_tuning")

SLUG = "cq-recall"
FIELD = "timeout_ms"
MIN_MS, MAX_MS = 50, 10_000


def recall_timeout_ms(remote_configs: dict, settings) -> int:
    """The recall budget in force for this request, in milliseconds."""
    fallback = int(getattr(settings, "cq_recall_timeout_ms", 500) or 500)
    raw = ((remote_configs or {}).get(SLUG) or {}).get(FIELD)
    if raw is None:
        return fallback
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning("recall_timeout_dial_ignored reason=not_an_int value=%r", raw)
        return fallback
    if not (MIN_MS <= raw <= MAX_MS):
        logger.warning("recall_timeout_dial_ignored reason=out_of_bounds value=%d bounds=%d-%d",
                       raw, MIN_MS, MAX_MS)
        return fallback
    return raw


# --- observations -------------------------------------------------------------
#
# One row per recall attempt. The 08-27 question ("is 500ms the binding
# constraint?") could only be answered for a single day, because the
# denominator lived in a container log that resets on every deploy while
# the numerator lived in alert_incidents. This puts both in one durable
# place, per scope, with the budget each attempt ran under.

RETENTION_DAYS = 30


async def record_observation(db, *, app_id, user_id, tier, cq_result: dict) -> None:
    """Write one recall attempt. Never raises: a turn must not fail
    because its telemetry did."""
    import uuid
    from datetime import datetime, timezone
    if not isinstance(cq_result, dict) or "recall_scope" not in cq_result:
        return
    degraded = cq_result.get("degraded")
    await db.execute(
        """INSERT INTO recall_observations
           (id, created_at, app_id, user_id, tier, scope, outcome,
            duration_ms, timeout_ms, matched, patch_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), app_id, user_id, tier,
         cq_result.get("recall_scope") or "full", degraded or "ok",
         cq_result.get("duration_ms"), cq_result.get("timeout_ms"),
         len(cq_result.get("matched_entities") or []), cq_result.get("patch_count")),
    )
    await db.commit()


async def purge_observations(db, retention_days: int = RETENTION_DAYS) -> int:
    """Drop rows past retention. Same 30-day posture as the rest."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cur = await db.execute("DELETE FROM recall_observations WHERE created_at < ?", (cutoff,))
    await db.commit()
    if cur.rowcount:
        logger.info("recall_observations_purge rows=%d", cur.rowcount)
    return cur.rowcount or 0
