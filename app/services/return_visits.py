"""Return visits: did a new Shoulder Surf user come back for a second meeting?

Scott, 2026-11-18. Comparing the one paying customer with the 15 trials that
lapsed or were cancelled, first-day use did NOT separate them: four lapsed
users tapped live help as much as the payer. What did separate them was
coming back. Every lapsed user was active on 1 to 3 days near signup and
then went quiet inside a 7-day trial. So the signal this panel watches is:

  returned   a second meeting on a LATER UTC day, within 72 hours of the
             first meeting.

A meeting is an AutoSummary call (`call_type = 'summary'`), one per meeting.
Live help is `query` / `query_follow_up`. Rows are usage_log, Shoulder Surf
only, Production users only in the sense that stats-excluded accounts are
left out. `subscribed` is any Production Apple subscription on the account;
`paid` is subscription_status.paid_ever, which Apple's own price sets (a
free trial is not paid). A signup younger than 72 hours since its first
meeting is `pending`, never counted as not returned.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

APP = "shouldersurf"
WINDOW_H = 72


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def classify(meetings: list[datetime], now: datetime) -> str:
    """'no_meeting' | 'returned' | 'not_returned' | 'pending' from meeting times."""
    if not meetings:
        return "no_meeting"
    ms = sorted(meetings)
    first = ms[0]
    end = first + timedelta(hours=WINDOW_H)
    if any(m.date() > first.date() and m < end for m in ms[1:]):
        return "returned"
    return "pending" if now < end else "not_returned"


async def report(db, days: int = 60, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    users = await (await db.execute(
        "SELECT id, created_at, apple_sub FROM users "
        "WHERE created_at >= ? AND COALESCE(exclude_from_stats, 0) = 0", (since,))).fetchall()
    ids = [u[0] for u in users]
    if not ids:
        return {"days": days, "window_hours": WINDOW_H, "weeks": [], "users": []}
    ph = ",".join("?" * len(ids))
    meet: dict[str, list[datetime]] = {}
    live: dict[str, int] = {}
    for uid, ct, ts in await (await db.execute(
            f"SELECT user_id, call_type, request_timestamp FROM usage_log "
            f"WHERE app_id = ? AND user_id IN ({ph}) AND status = 'success' "
            f"AND call_type IN ('summary', 'query', 'query_follow_up')", (APP, *ids))).fetchall():
        if ct == "summary":
            meet.setdefault(uid, []).append(_ts(ts))
        else:
            live[uid] = live.get(uid, 0) + 1
    subbed, paid = set(), set()
    for uid, p in await (await db.execute(
            f"SELECT user_id, MAX(COALESCE(paid_ever, 0)) FROM subscription_status "
            f"WHERE environment = 'Production' AND user_id IN ({ph}) GROUP BY user_id", ids)).fetchall():
        subbed.add(uid)
        if p:
            paid.add(uid)

    rows, weeks = [], {}
    for uid, created, sub in users:
        if uid not in meet and uid not in live:
            continue  # never used Shoulder Surf (another app's account, or no use yet)
        c = _ts(created)
        state = classify(meet.get(uid, []), now)
        wk = (c - timedelta(days=c.weekday())).strftime("%Y-%m-%d")
        rows.append({"user_id": uid, "created_at": created, "week": wk,
                     "anonymous": str(sub or "").startswith("anonymous:"),
                     "meetings": len(meet.get(uid, [])), "live_help": live.get(uid, 0),
                     "state": state, "subscribed": uid in subbed, "paid": uid in paid})
        w = weeks.setdefault(wk, {"week": wk, "users": 0, "had_meeting": 0, "returned": 0,
                                  "not_returned": 0, "pending": 0, "subscribed": 0, "paid": 0,
                                  "paid_returned": 0})
        w["users"] += 1
        if state != "no_meeting":
            w["had_meeting"] += 1
            w[state] += 1
        w["subscribed"] += uid in subbed
        w["paid"] += uid in paid
        w["paid_returned"] += (uid in paid and state == "returned")
    for w in weeks.values():
        decided = w["returned"] + w["not_returned"]
        w["return_rate"] = round(w["returned"] / decided, 3) if decided else None
    return {"days": days, "window_hours": WINDOW_H,
            "weeks": sorted(weeks.values(), key=lambda w: w["week"], reverse=True),
            "users": sorted(rows, key=lambda r: r["created_at"], reverse=True)}
