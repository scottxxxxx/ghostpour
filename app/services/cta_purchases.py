"""CTA to purchase: did a Shoulder Surf trial or purchase come right after a CTA?

Scott, 2026-11-18. The one paying customer tapped the Memory tab gate
(promo_events surface=memory_tab, feature=context_quilt, block_reason=tier)
and started a trial 13 seconds later. This panel counts that shape for
everyone.

A "start" is a user's FIRST Production `subscribed` row in subscription_events
(a free trial counts, its effective_at is Apple's time). Each start gets one
attribution, the strongest that applies:

  after_tap   a promo click by that user (or one of their devices) in the
              TAP_MIN minutes before the start
  saw_gate    no qualifying tap, but an impression in the SEEN_H hours before
  no_cta      neither

`paid` is subscription_status.paid_ever (Apple's own price; a trial is not
paid). Each CTA is labelled by campaign_id, else surface, else "unlabeled",
because in-app feature gates carry a surface and no campaign. Stats-excluded
accounts are left out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

APP = "shouldersurf"
TAP_MIN = 30
SEEN_H = 24


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def label(campaign_id, surface, feature) -> str:
    if campaign_id:
        return f"campaign {campaign_id}"
    if surface:
        return f"{surface} ({feature})" if feature else surface
    return "unlabeled"


def attribute(start: datetime, events: list[tuple[datetime, str, str]]) -> tuple[str, str | None]:
    """(kind, cta label) for one start from that user's (time, event_type, label) list."""
    taps = [e for e in events if e[1] == "click" and start - timedelta(minutes=TAP_MIN) <= e[0] <= start]
    if taps:
        return "after_tap", max(taps)[2]
    seen = [e for e in events if e[1] == "impression" and start - timedelta(hours=SEEN_H) <= e[0] <= start]
    if seen:
        return "saw_gate", max(seen)[2]
    return "no_cta", None


async def report(db, days: int = 90, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    excluded = {r[0] for r in await (await db.execute(
        "SELECT id FROM users WHERE COALESCE(exclude_from_stats, 0) = 1")).fetchall()}

    # Per-CTA funnel over the window.
    ctas: dict[str, dict] = {}
    user_ev: dict[str, list] = {}
    dev_user: dict[str, str] = {}
    rows = await (await db.execute(
        "SELECT created_at, event_type, user_id, device_id, campaign_id, surface, feature FROM promo_events "
        "WHERE app_id = ? AND created_at >= ? AND event_type IN ('impression', 'click')",
        (APP, since))).fetchall()
    for _, _, uid, dev, *_ in rows:
        if uid and dev:
            dev_user.setdefault(dev, uid)
    for created, et, uid, dev, camp, surf, feat in rows:
        who = uid or dev_user.get(dev or "")
        if who in excluded:
            continue
        lb = label(camp, surf, feat)
        c = ctas.setdefault(lb, {"cta": lb, "impressions": 0, "clicks": 0, "_seen": set(), "_tapped": set(),
                                 "starts_after_tap": 0, "paid_after_tap": 0})
        c["impressions" if et == "impression" else "clicks"] += 1
        key = who or f"dev:{dev}"
        c["_seen" if et == "impression" else "_tapped"].add(key)
        if who:
            user_ev.setdefault(who, []).append((_ts(created), et, lb))

    # First Production start per user, in the window.
    starts = await (await db.execute(
        "SELECT user_id, MIN(effective_at) FROM subscription_events "
        "WHERE event_type = 'subscribed' AND environment = 'Production' GROUP BY user_id")).fetchall()
    paid = {r[0] for r in await (await db.execute(
        "SELECT user_id FROM subscription_status WHERE environment = 'Production' AND paid_ever = 1")).fetchall()}
    out, totals = [], {k: {"starts": 0, "paid": 0} for k in ("after_tap", "saw_gate", "no_cta")}
    for uid, eff in starts:
        if not uid or uid in excluded or not eff or eff < since:
            continue
        kind, lb = attribute(_ts(eff), user_ev.get(uid, []))
        p = uid in paid
        totals[kind]["starts"] += 1
        totals[kind]["paid"] += p
        if kind == "after_tap" and lb in ctas:
            ctas[lb]["starts_after_tap"] += 1
            ctas[lb]["paid_after_tap"] += p
        out.append({"user_id": uid, "started_at": eff, "attribution": kind, "cta": lb, "paid": p})

    for c in ctas.values():
        c["viewers"], c["tappers"] = len(c.pop("_seen")), len(c.pop("_tapped"))
        c["tap_rate"] = round(c["tappers"] / c["viewers"], 3) if c["viewers"] else None
    n = sum(t["starts"] for t in totals.values())
    return {"days": days, "tap_window_min": TAP_MIN, "seen_window_h": SEEN_H,
            "totals": totals, "starts": n,
            "after_tap_share": round(totals["after_tap"]["starts"] / n, 3) if n else None,
            "ctas": sorted(ctas.values(), key=lambda c: (-c["starts_after_tap"], -c["impressions"])),
            "users": sorted(out, key=lambda r: r["started_at"], reverse=True)}
