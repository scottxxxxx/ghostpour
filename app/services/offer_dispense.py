"""Per-user offer-code dispense — the pool + reserve-once issuance behind a
`storekit_offer` promo CTA.

GP holds a pool of one-time-use App Store offer codes (minted via the Connect
API, see offer_codes.py) and hands out ONE code per user at promo-resolve time,
injected into the CTA's `action.value`. Design (agreed with SS):

- **Reserve-once, idempotent per user.** Promo resolve fires on every cold
  launch, so we never burn a fresh code per resolve — the first dispense reserves
  an unused code and binds it to the user (device_id fallback when signed out),
  and every later resolve hands back that same reserved code.
- **Per-campaign pools.** A code belongs to an `(offer_id, environment)` pool.
  The `storekit_offer` CTA names both, so a sandbox test campaign draws sandbox
  codes and the live production campaign draws production codes — no client
  change, no cross-environment mixing.
- **Exhaustion → suppress.** `dispense` returns None; the caller (promo resolve)
  drops the CTA rather than shipping an empty `action.value` (the client also
  guards an empty value as a backstop).

v1 scope: a dispensed (reserved) code is terminal for the pool — we do NOT
reclaim un-redeemed reservations. That is safe (a one-time code that WAS redeemed
can never be re-handed-out) but means the pool depletes by distinct users who see
the offer, not by redemptions. Reclaiming abandoned reservations needs a per-user
redemption signal off ASSN and is the v2 before a high-volume production campaign.
"""

from datetime import datetime, timezone

import aiosqlite

_RESERVE_RETRIES = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def dispense(
    db: aiosqlite.Connection,
    *,
    offer_id: str,
    environment: str,
    user_id: str | None = None,
    device_id: str | None = None,
) -> str | None:
    """Return the code reserved for this actor, reserving a fresh one on first
    call. None when the actor can't be anchored or the pool is exhausted.

    Idempotent: the same `(offer_id, environment, actor)` always yields the same
    code. Prefers `user_id` as the reservation key; falls back to `device_id`
    when the resolve is unauthenticated.
    """
    if user_id:
        who_col, who_val = "reserved_by_user", user_id
    elif device_id:
        who_col, who_val = "reserved_by_device", device_id
    else:
        return None

    # Already reserved for this actor? Hand back the same code (idempotent).
    existing = await (await db.execute(
        f"SELECT code FROM offer_code_pool "
        f"WHERE offer_id = ? AND environment = ? AND {who_col} = ? AND status = 'reserved' "
        f"ORDER BY reserved_at LIMIT 1",
        (offer_id, environment, who_val),
    )).fetchone()
    if existing:
        return existing[0]

    # Reserve an available code. SELECT-then-guarded-UPDATE so two concurrent
    # resolves can't hand the same code to two actors — the WHERE status='available'
    # guard makes the winner unique; the loser retries onto the next code.
    for _ in range(_RESERVE_RETRIES):
        cand = await (await db.execute(
            "SELECT code FROM offer_code_pool "
            "WHERE offer_id = ? AND environment = ? AND status = 'available' "
            "ORDER BY created_at, code LIMIT 1",
            (offer_id, environment),
        )).fetchone()
        if not cand:
            return None  # pool exhausted
        code = cand[0]
        cur = await db.execute(
            f"UPDATE offer_code_pool "
            f"SET status = 'reserved', {who_col} = ?, reserved_at = ? "
            f"WHERE code = ? AND status = 'available'",
            (who_val, _now_iso(), code),
        )
        if cur.rowcount == 1:
            await db.commit()
            return code
        # Lost the race for this specific code; try the next available one.
    await db.commit()
    return None


async def load_pool(
    db: aiosqlite.Connection,
    *,
    offer_id: str,
    environment: str,
    codes: list[str],
    batch_id: str | None = None,
    product_id: str | None = None,
) -> dict:
    """Insert code strings as 'available'. Idempotent (`INSERT OR IGNORE` on the
    code PK) so re-loading a batch never duplicates a code or resets one that's
    already reserved. Returns `{loaded, skipped}`."""
    now = _now_iso()
    seen = 0
    loaded = 0
    for raw in codes:
        code = (raw or "").strip()
        if not code:
            continue
        seen += 1
        cur = await db.execute(
            "INSERT OR IGNORE INTO offer_code_pool "
            "(code, offer_id, product_id, environment, batch_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'available', ?)",
            (code, offer_id, product_id, environment, batch_id, now),
        )
        loaded += cur.rowcount
    await db.commit()
    return {"loaded": loaded, "skipped": seen - loaded}


async def pool_status(
    db: aiosqlite.Connection, *, offer_id: str, environment: str
) -> dict:
    """Available / reserved counts for an `(offer_id, environment)` pool."""
    rows = await (await db.execute(
        "SELECT status, COUNT(*) FROM offer_code_pool "
        "WHERE offer_id = ? AND environment = ? GROUP BY status",
        (offer_id, environment),
    )).fetchall()
    counts = {status: n for status, n in rows}
    return {
        "offer_id": offer_id,
        "environment": environment,
        "available": counts.get("available", 0),
        "reserved": counts.get("reserved", 0),
        "total": sum(counts.values()),
    }
