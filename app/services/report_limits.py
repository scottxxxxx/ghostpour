"""Free-tier meeting report counts (Scott, 2026-11-15).

Every Shoulder Surf meeting report is POST /v1/meetings/{id}/report, whatever
model the user has picked (SS Dev read the client: there is no on-device
report). So one server-side counter decides both limits:

  apple   on_device / apple_cloud selected: a LIFETIME count per account.
  ssai    SS AI (and, by default, BYOK or an older build that sends no
          selected_model): a per calendar month (UTC) count per account.

Only the free tier is limited. The numbers live in the served client-config
`limits.meeting_reports`, so Scott moves them without a deploy; with no dial
there is no limit, exactly as before.

The ledger is one row per (account, meeting), written when a REAL report is
stored. A regenerate of a meeting that already counted never counts again and
is never refused, and the ledger is not meeting_reports, which purges at 30
days and so could not hold a lifetime count.
"""
from __future__ import annotations

from datetime import datetime, timezone

APPLE_MODELS = frozenset({"on_device", "apple_cloud"})
LIMITED_TIERS = frozenset({"free"})


def dials(remote_configs: dict) -> dict | None:
    cfg = (remote_configs or {}).get("client-config") or {}
    d = (cfg.get("limits") or {}).get("meeting_reports")
    return d if isinstance(d, dict) else None


def bucket_for(selected_model: str | None, d: dict) -> str:
    m = (selected_model or "").strip().lower()
    if m in APPLE_MODELS:
        return "apple"
    if m == "byok":
        return "apple" if d.get("byok_bucket") == "apple" else "ssai"
    return "ssai"


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def next_month_iso(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    y, m = (n.year + 1, 1) if n.month == 12 else (n.year, n.month + 1)
    return datetime(y, m, 1, tzinfo=timezone.utc).isoformat()


async def already_counted(db, user_id: str, meeting_id: str) -> bool:
    row = await (await db.execute(
        "SELECT 1 FROM report_quota_ledger WHERE user_id = ? AND meeting_id = ?",
        (user_id, meeting_id))).fetchone()
    return row is not None


async def used(db, user_id: str, bucket: str, d: dict, now: datetime | None = None) -> int:
    if bucket == "apple":
        sql = "SELECT COUNT(*) FROM report_quota_ledger WHERE user_id = ? AND bucket = 'apple'"
        args: tuple = (user_id,)
    else:
        buckets = ("ssai", "apple") if d.get("apple_counts_toward_ssai") else ("ssai",)
        ph = ",".join("?" * len(buckets))
        sql = (f"SELECT COUNT(*) FROM report_quota_ledger WHERE user_id = ? "
               f"AND bucket IN ({ph}) AND month = ?")
        args = (user_id, *buckets, month_key(now))
    row = await (await db.execute(sql, args)).fetchone()
    return int(row[0] or 0)


def limit_for(bucket: str, d: dict) -> int | None:
    key = "apple_lifetime" if bucket == "apple" else "ssai_free_per_month"
    v = d.get(key)
    return int(v) if isinstance(v, (int, float)) and v >= 0 else None


async def refusal(db, user, meeting_id: str, selected_model: str | None,
                  remote_configs: dict) -> dict | None:
    """The quota facts when this report must be refused, else None."""
    if user.effective_tier not in LIMITED_TIERS:
        return None
    d = dials(remote_configs)
    if not d:
        return None
    bucket = bucket_for(selected_model, d)
    limit = limit_for(bucket, d)
    if limit is None or await already_counted(db, user.id, meeting_id):
        return None
    n = await used(db, user.id, bucket, d)
    if n < limit:
        return None
    return {"bucket": bucket, "used": n, "limit": limit,
            "resets_at": None if bucket == "apple" else next_month_iso()}


async def record(db, user, meeting_id: str, selected_model: str | None,
                 remote_configs: dict) -> None:
    """Count a real report once per meeting. Every tier is recorded, so a
    user who downgrades is counted from what they actually made."""
    d = dials(remote_configs) or {}
    await db.execute(
        "INSERT OR IGNORE INTO report_quota_ledger "
        "(user_id, meeting_id, bucket, month, created_at) VALUES (?, ?, ?, ?, ?)",
        (user.id, meeting_id, bucket_for(selected_model, d), month_key(),
         datetime.now(timezone.utc).isoformat()))
    await db.commit()
