"""Help and tour numbers for the dashboard's "Help and learning" section.

Pure functions over rows the admin route has already read
(/webhooks/admin/telemetry/help-tour in app/routers/webhooks.py), so every
number here can be tested without a database. Nothing is estimated: a
figure that cannot be derived from what the clients send is left out, and
the two that are derived rather than sent say so where they are computed.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median

# A takeaway is only stated once this many views of its kind (Help visits
# for the Help lines, tour views for the tour line) are in the window.
# Below it one person's afternoon reads like a pattern.
TAKEAWAY_MIN_VIEWS = 10
# An article whose median time open is under this was opened and left.
FAST_EXIT_MS = 5000
TOP_ARTICLES = 20
# Raw lifecycle events are purged at 30 days (app/services/retention.py), so
# "active devices" cannot be counted further back than that.
REACH_MAX_DAYS = 30

INSTALL_BUCKETS = ("day_0", "days_1_3", "days_4_14", "later")
_TOUR_STEP_RE = re.compile(r"^tour_(\d+)$")


def pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def _median(values: list) -> float | None:
    return round(float(median(values)), 1) if values else None


def _counts(values: list, key: str) -> list[dict]:
    return [{key: v, "n": n} for v, n in
            sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0]))]


def parse_ts(value: str | None) -> datetime | None:
    """A stored timestamp as an aware UTC datetime. Rows written by the ping
    are ISO with an offset; backfilled ones are SQLite's 'YYYY-MM-DD
    HH:MM:SS', which is UTC."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def install_bucket(first_seen: datetime | None, first_view: datetime | None) -> str | None:
    """Which bucket a device's first view falls in, by whole days since GP
    first saw the device. None when either end is missing."""
    if first_seen is None or first_view is None:
        return None
    days = max(0, (first_view - first_seen).days)
    if days == 0:
        return "day_0"
    if days <= 3:
        return "days_1_3"
    if days <= 14:
        return "days_4_14"
    return "later"


def daily_series(days: int, today: date, help_days: list[str], tour_days: list[str]) -> list[dict]:
    """One entry per day of the window, oldest first, zeros included, so a
    quiet day shows as quiet instead of vanishing."""
    h, t = Counter(help_days), Counter(tour_days)
    out = []
    for back in range(days - 1, -1, -1):
        day = (today - timedelta(days=back)).isoformat()
        out.append({"day": day, "help": h.get(day, 0), "tour": t.get(day, 0)})
    return out


def summarize_help(views: list[tuple[str, dict]]) -> dict:
    """`views` is (device_id, help block) per Help visit."""
    dwell: dict[str, list[int]] = {}
    article_devices: dict[str, set] = {}
    # Per search text: visits it appeared in, visits where an article was
    # opened from it, and the devices behind each. Intake already merged
    # repeats inside one visit, so a visit counts a text once.
    searches: dict[str, dict] = {}
    no_article = 0
    for device, h in views:
        articles = h.get("articles") or []
        if not articles:
            no_article += 1
        for a in articles:
            dwell.setdefault(a["id"], []).append(a["dwell_ms"])
            article_devices.setdefault(a["id"], set()).add(device)
        for s in h.get("searches") or []:
            agg = searches.setdefault(
                s["q"], {"count": 0, "opened": 0, "devices": set(), "gap_devices": set()})
            agg["count"] += 1
            agg["devices"].add(device)
            if s["opened"]:
                agg["opened"] += 1
            else:
                agg["gap_devices"].add(device)
    total = len(views)
    by_count = sorted(searches.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    gaps = sorted(
        ((q, a["count"] - a["opened"], a["gap_devices"]) for q, a in searches.items()
         if a["count"] > a["opened"]),
        key=lambda g: (-g[1], g[0]))
    top_articles = []
    for aid, ms in sorted(dwell.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_ARTICLES]:
        med = _median(ms)
        top_articles.append({
            # An article opened twice in one visit is two opens.
            "id": aid, "opens": len(ms), "distinct_devices": len(article_devices[aid]),
            "median_dwell_ms": med, "fast_exit": med is not None and med < FAST_EXIT_MS})
    return {
        "views": total,
        "distinct_devices": len({d for d, _ in views}),
        "median_duration_ms": _median([h["duration_ms"] for _, h in views]),
        "by_entry": _counts([h["entry"] for _, h in views], "entry"),
        "searched_rate": pct(sum(1 for _, h in views if h["searched"]), total),
        "asked_rate": pct(sum(1 for _, h in views if h["asked"]), total),
        "whats_new_opened_rate": pct(sum(1 for _, h in views if h["whats_new_opened"]), total),
        "no_article_views": no_article,
        "no_article_rate": pct(no_article, total),
        "top_articles": top_articles,
        # Every search, most frequent first. single_device marks a text only
        # one device ever sent, so one person's odd query reads as that.
        "top_searches": [
            {"q": q, "count": a["count"], "distinct_devices": len(a["devices"]),
             "opened_rate": pct(a["opened"], a["count"]),
             "single_device": len(a["devices"]) == 1}
            for q, a in by_count
        ],
        # The Help gaps: searches after which no article was opened. count is
        # the visits where that happened, not every visit with the text.
        "unopened_searches": [
            {"q": q, "count": n, "distinct_devices": len(devs),
             "single_device": len(devs) == 1}
            for q, n, devs in gaps
        ],
    }


def summarize_tour(views: list[tuple[str, dict]]) -> dict:
    """`views` is (device_id, tour block) per tour viewing from Settings."""
    total = len(views)
    completed = sum(1 for _, t in views if t["completed"])
    # Where the people who did NOT finish stopped, in slide order so it
    # reads as a histogram. A finished tour is not a drop.
    drops = Counter((t.get("last_slide") or "unknown")
                    for _, t in views if not t["completed"])
    return {
        "views": total,
        "distinct_devices": len({d for d, _ in views}),
        "completed": completed,
        "completion_rate": pct(completed, total),
        "median_slides_seen": _median([t["slides_seen"] for _, t in views]),
        "by_entry": _counts([t["entry"] for _, t in views], "entry"),
        "drop_off": [{"last_slide": s, "n": n} for s, n in sorted(drops.items())],
        "narration_on_rate": pct(sum(1 for _, t in views if t["narration_on"]), total),
        # "pt_BR" and "pt-BR" are one language; a tour sent without one
        # counts as "unknown".
        "by_language": _counts(
            [(t.get("language") or "unknown").replace("_", "-") for _, t in views],
            "language"),
    }


def summarize_onboarding_tour(rows: list[dict]) -> dict:
    """What onboarding_completed records about the first-run tour. `rows`
    carry completed, tour_skipped, abandoned_at_step and steps (the parsed
    [{step, dwell_ms}] list).

    The event sends `tour_skipped`, the slides shown (steps named tour_N)
    and, for an abandoned onboarding, the step it was left on. It does NOT
    send "finished the tour", so `went_through` is DERIVED: the tour was
    shown, Skip was not tapped, and the onboarding was not abandoned on a
    tour slide. Where somebody stopped is the last tour slide in their steps
    (a skip) or abandoned_at_step (left the app there)."""
    shown = skipped = went_through = 0
    stops: dict[int, dict] = {}

    def _slide(step: str | None) -> int | None:
        m = _TOUR_STEP_RE.match(step or "")
        return int(m.group(1)) if m else None

    for r in rows:
        slides = [n for n in (_slide(s.get("step")) for s in r.get("steps") or [])
                  if n is not None]
        left_on = _slide(r.get("abandoned_at_step")) if not r.get("completed") else None
        if not slides and left_on is None:
            continue
        shown += 1
        if r.get("tour_skipped"):
            skipped += 1
            if slides:
                stops.setdefault(slides[-1], {"skipped": 0, "left_app": 0})["skipped"] += 1
        elif left_on is not None:
            stops.setdefault(left_on, {"skipped": 0, "left_app": 0})["left_app"] += 1
        else:
            went_through += 1
    return {
        "onboardings": len(rows),
        "tour_shown": shown,
        "tour_skipped": skipped,
        "tour_skipped_rate": pct(skipped, shown),
        "went_through": went_through,
        "went_through_rate": pct(went_through, shown),
        "stops": [{"slide": f"tour_{n}", **stops[n]} for n in sorted(stops)],
    }


def takeaways(help_out: dict, tour_out: dict, reach: dict) -> list[dict]:
    """Short findings for the top of the section, each read straight off the
    numbers beside it. Nothing below TAKEAWAY_MIN_VIEWS, and a search has to
    come from at least two devices to be named."""
    out: list[dict] = []
    if help_out["views"] >= TAKEAWAY_MIN_VIEWS:
        if reach["active_devices"]:
            out.append({
                "title": f"{reach['help_rate']}% of active devices opened Help",
                "body": f"{reach['help_devices']} of {reach['active_devices']} devices "
                        f"in the last {reach['window_days']} days."})
        if help_out["top_articles"]:
            a = help_out["top_articles"][0]
            out.append({
                "title": f"Most opened article: {a['id']}",
                "body": f"Opened {a['opens']} times on {a['distinct_devices']} "
                        f"device{'' if a['distinct_devices'] == 1 else 's'}."})
        gap = next((g for g in help_out["unopened_searches"] if not g["single_device"]), None)
        if gap:
            out.append({
                "title": f"Top search with no article opened: \"{gap['q']}\"",
                "body": f"{gap['count']} times across {gap['distinct_devices']} devices. "
                        "Help may be missing an answer here."})
    if tour_out["views"] >= TAKEAWAY_MIN_VIEWS:
        early = sum(d["n"] for d in tour_out["drop_off"])
        if early:
            worst = max(tour_out["drop_off"], key=lambda d: d["n"])
            out.append({
                "title": f"Most people who leave the tour early stop at {worst['last_slide']}",
                "body": f"{worst['n']} of {early} early exits. "
                        f"{tour_out['completion_rate']}% of tour views reach the end."})
    return out
