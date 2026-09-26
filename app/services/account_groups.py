"""Which accounts are one person (Scott, 2026-09-26).

An anonymous buyer who restores on a second device gets a second account;
Scott ruled the dashboard shows both but tracks them as the same account on
two devices. `account_links` records each hop (a verified restore that moved
a plan, a merge into an Apple account) and this groups the accounts those
hops connect.

The group's key is the account that holds the plan now: the last hop's
destination, since plans only ever move forward along a link.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


async def link(db: aiosqlite.Connection, from_user_id: str, to_user_id: str,
               reason: str, otid: str | None = None) -> None:
    """Record that two accounts are one person. Idempotent; caller commits."""
    if not from_user_id or not to_user_id or from_user_id == to_user_id:
        return
    await db.execute(
        """INSERT OR IGNORE INTO account_links
           (from_user_id, to_user_id, reason, original_transaction_id, linked_at)
           VALUES (?, ?, ?, ?, ?)""",
        (from_user_id, to_user_id, reason, otid, datetime.now(timezone.utc).isoformat()))


async def groups(db: aiosqlite.Connection) -> dict[str, str]:
    """{user_id: group key} for every account in a link. Unlinked accounts are
    absent: an account alone is its own group."""
    rows = await (await db.execute(
        "SELECT from_user_id, to_user_id FROM account_links ORDER BY linked_at")).fetchall()
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in rows:
        # Point the older account's root at the newer one, so the root is the
        # latest destination, which is the account holding the plan now.
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return {u: find(u) for u in list(parent)}
