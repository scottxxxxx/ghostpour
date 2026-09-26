"""Merging an anonymous purchase account into an existing Apple account.

When a buyer who purchased signed out later signs in with an Apple ID that
already has an account, Scott ruled the APPLE ACCOUNT WINS (2026-09-26): the
plan and the user's history move to it and the anonymous account closes.
ContextQuilt (Memory) is NOT touched for iOS 1.18 ("let's get this app
approved first"); `anonymous_merges` records every merge so it can run later.
CQ's note for that day: closing the account does not freeze the CQ subject,
since its ingest is asynchronous, so that merge needs a drain gate.

WHAT MOVES. The plan, only when the Apple account has none of its own. If
both are paid (the anonymous purchase was on a different Apple ID's
subscription), the Apple account keeps its plan and the anonymous
transaction stays on the closed row, so its Apple notifications cannot touch
the Apple account's tier; the caller reports `plan_conflict`. The rows a user
sees or that follow the plan move with `UPDATE OR IGNORE`: a row that would
collide with one the Apple account already has stays on the closed account
rather than being deleted. Analytics rows (usage_log, telemetry, promo and
attribution events) stay where they were recorded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

# User-visible content, and the plan's own records.
CONTENT_TABLES = ("meeting_reports", "meeting_transcripts", "meeting_shares",
                  "generated_files", "generations", "project_prefs", "device_tokens")
PLAN_TABLES = ("subscription_events", "subscription_status")

_PLAN_COLUMNS = ("tier", "monthly_cost_limit_usd", "monthly_used_usd", "overage_balance_usd",
                 "searches_used", "generations_used", "allocation_resets_at", "is_trial",
                 "trial_start", "trial_end", "original_transaction_id")


def _paid(row: dict) -> bool:
    return (row.get("tier") or "free") != "free"


async def merge_into(db: aiosqlite.Connection, anon: dict, apple: dict) -> dict:
    """Move the anonymous account into the Apple account and close it."""
    plan_conflict = _paid(anon) and _paid(apple)
    plan_moved = _paid(anon) and not _paid(apple)
    now = datetime.now(timezone.utc).isoformat()

    if plan_moved:
        sets = ", ".join(f"{c} = ?" for c in _PLAN_COLUMNS)
        await db.execute(
            f"UPDATE users SET {sets}, simulated_tier = NULL, simulated_exhausted = 0, "
            "ever_subscribed = 1, updated_at = ? WHERE id = ?",
            (*[anon.get(c) for c in _PLAN_COLUMNS], now, apple["id"]))
        await db.execute(
            "UPDATE users SET tier = 'free', original_transaction_id = NULL WHERE id = ?",
            (anon["id"],))

    moved = {}
    for table in CONTENT_TABLES + (PLAN_TABLES if plan_moved else ()):
        cur = await db.execute(
            f"UPDATE OR IGNORE {table} SET user_id = ? WHERE user_id = ?",
            (apple["id"], anon["id"]))
        moved[table] = cur.rowcount

    await db.execute("UPDATE users SET is_active = 0, updated_at = ? WHERE id = ?",
                     (now, anon["id"]))
    await db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (anon["id"],))
    await db.execute(
        """INSERT OR REPLACE INTO anonymous_merges
           (anonymous_user_id, into_user_id, merged_at, plan_moved, plan_conflict,
            original_transaction_id, moved_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (anon["id"], apple["id"], now, int(plan_moved), int(plan_conflict),
         anon.get("original_transaction_id"), json.dumps(moved, sort_keys=True)))
    await db.commit()
    return {"plan_moved": plan_moved, "plan_conflict": plan_conflict, "moved": moved}
