"""Usage and purchase stats for case-based apps (N-400 today), Scott
2026-10-18: how many people use it, how long, and how long before they buy.

USAGE is measured: every interview turn is a usage_log row carrying user_id,
case_id (one application) and a timestamp.

PURCHASES are measured too, from Apple (app_purchases, see app_purchases.py),
but WHO bought is not: the purchase token is a hash of an A-Number GP never
sees. So a purchase is tied to a case by TIME, and only when exactly one case
was active in the window around it. Every number built on that tie is
labelled inferred, and the match counts travel with it so a reader can see
how much of the picture the inference covers.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

# A gap longer than this between two turns starts a new session.
SESSION_GAP = timedelta(minutes=30)
# A gap counts toward active time only up to this long; a longer pause is
# her away from the phone, not her using it.
ACTIVE_GAP_CAP = timedelta(minutes=5)
# A purchase belongs to a case that had a turn in [purchase - BEFORE,
# purchase + AFTER], when exactly one case did.
MATCH_BEFORE = timedelta(minutes=60)
MATCH_AFTER = timedelta(minutes=5)

# Answered from idx_usage_app_case_ts alone (tests/test_case_stats.py plans
# it). Flagged demo and test accounts stay out, as on every dashboard count.
CASE_ROWS_SQL = (
    "SELECT case_id, user_id, request_timestamp FROM usage_log "
    "WHERE app_id = ? AND case_id IS NOT NULL AND request_timestamp >= ? "
    "AND (user_id IS NULL OR user_id NOT IN "
    "(SELECT id FROM users WHERE exclude_from_stats = 1)) "
    "ORDER BY case_id, request_timestamp")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def sessions_and_active(times: list[datetime]) -> tuple[int, float]:
    """(sessions, active minutes) for one case's sorted turn times."""
    if not times:
        return 0, 0.0
    sessions, active = 1, timedelta(0)
    for a, b in zip(times, times[1:]):
        gap = b - a
        if gap > SESSION_GAP:
            sessions += 1
        if gap <= ACTIVE_GAP_CAP:
            active += gap
    return sessions, round(active.total_seconds() / 60, 1)


def match_purchase(purchased_at: datetime, cases: dict[str, list[datetime]]) -> tuple[str, str | None]:
    """('matched', case_id) | ('ambiguous', None) | ('unmatched', None)."""
    lo, hi = purchased_at - MATCH_BEFORE, purchased_at + MATCH_AFTER
    hits = [cid for cid, ts in cases.items() if any(lo <= t <= hi for t in ts)]
    if len(hits) == 1:
        return "matched", hits[0]
    return ("ambiguous", None) if hits else ("unmatched", None)


def _med(xs: list[float]) -> float | None:
    return round(median(xs), 1) if xs else None


def summarize(rows: list[tuple[str, str | None, str]], purchases: list[dict]) -> dict:
    """rows: (case_id, user_id, request_timestamp) for one app.
    purchases: app_purchases rows as dicts for the same app."""
    cases: dict[str, list[datetime]] = {}
    users: set[str] = set()
    for cid, uid, ts in rows:
        cases.setdefault(cid, []).append(_ts(ts))
        if uid:
            users.add(uid)
    per_case = {cid: sessions_and_active(sorted(ts)) for cid, ts in cases.items()}
    spans = [(max(ts) - min(ts)).total_seconds() / 86400 for ts in cases.values()]

    live = [p for p in purchases if not p.get("revoked_at")]
    revenue: dict[str, float] = {}
    for p in live:
        if p.get("price_milli") is not None and p.get("currency"):
            revenue[p["currency"]] = revenue.get(p["currency"], 0) + p["price_milli"] / 1000
    by_env: dict[str, int] = {}
    for p in purchases:
        env = p.get("environment") or "unknown"
        by_env[env] = by_env.get(env, 0) + 1

    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    to_first, to_active, to_sessions, bought = [], [], [], set()
    for p in live:
        if not p.get("purchased_at"):
            counts["unmatched"] += 1
            continue
        at = _ts(p["purchased_at"])
        status, cid = match_purchase(at, cases)
        counts[status] += 1
        if cid is None:
            continue
        bought.add(cid)
        before = sorted(t for t in cases[cid] if t <= at + MATCH_AFTER)
        s, a = sessions_and_active(before)
        to_first.append((at - before[0]).total_seconds() / 60)
        to_active.append(a)
        to_sessions.append(s)

    return {
        "usage": {
            "users": len(users),
            "cases": len(cases),
            "turns": len(rows),
            "median_turns_per_case": _med([len(t) for t in cases.values()]),
            "median_sessions_per_case": _med([s for s, _ in per_case.values()]),
            "median_active_minutes_per_case": _med([a for _, a in per_case.values()]),
            "median_days_first_to_last_turn": _med(spans),
        },
        "purchases": {
            "count": len(purchases),
            "refunded": len(purchases) - len(live),
            "revenue": [{"currency": c, "total": round(v, 2)} for c, v in sorted(revenue.items())],
            "by_environment": by_env,
        },
        "to_purchase_inferred": {
            "match_counts": counts,
            "window_minutes": {"before": int(MATCH_BEFORE.total_seconds() // 60),
                               "after": int(MATCH_AFTER.total_seconds() // 60)},
            "cases_with_matched_purchase": len(bought),
            "median_minutes_first_turn_to_purchase": _med(to_first),
            "median_active_minutes_before_purchase": _med(to_active),
            "median_sessions_before_purchase": _med(to_sessions),
        },
        "definitions": {
            "session_gap_minutes": int(SESSION_GAP.total_seconds() // 60),
            "active_gap_cap_minutes": int(ACTIVE_GAP_CAP.total_seconds() // 60),
        },
    }
