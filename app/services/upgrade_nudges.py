"""Upgrade nudges: a specific, earned reason, at the moment it bites.

Scott, 2026-08-23, after an inventory of every served CTA found that the
ENTIRE Plus-to-Pro path was one trigger, search-cap exhaustion, which is
the weakest argument Pro has. Nothing surfaced the differences that
actually separate the tiers: memory with no window instead of 30 days,
the stronger model on reports, 360K context against 150K, 5 images
against 3. Free's memory cell was `disabled` rather than `teaser`, so the
thing the tier sheet calls "THE value proposition" produced no Free nudge
either.

The sheet's own rule for the Free teaser is the rule for every nudge
here: "a teaser that says upgrade for more context carries no
information; one that says this answer skipped 6 earlier meetings does."

Two primitives:

`next_tier_that_fits` reads the SERVED dials and names the lowest tier
whose cap would have satisfied what the user just tried. It never
recommends a tier that would fail the same way, and it returns None when
no tier fits, because "upgrade" is a lie when trimming is the only fix.

`memory_excluded_cta` turns CQ's report of what its own scoping or
windowing left out into served copy with the number in it. CQ is the
side that applies the predicate, so CQ is the side that can count what
it cut; GP only renders. Dormant until CQ ships the field (asked
2026-08-23), and additive on both ends: absent means no nudge.

Copy is served config, never code. GP owns the recipe, SS renders.
"""
from __future__ import annotations

from typing import Any


class _Safe(dict):
    """format_map that leaves an unknown placeholder visible rather than
    raising. Locale copy does not have to use every placeholder, and a
    KeyError here would turn a nudge into a 500 on a chat turn, which is
    the one outcome worse than no nudge."""
    def __missing__(self, key):
        return "{" + key + "}"


def _fmt(template: str, **kw) -> str:
    return template.format_map(_Safe(kw))


# Paid tiers in ascending order. Admin and automation are never a target.
LADDER = ("free", "plus", "pro")

DEFAULT_COPY: dict[str, dict[str, str]] = {
    # {needed} and {cap} are rendered in K for chars; {tier} is the target.
    "context_fits_higher": {
        "text": "This is {needed}K characters. {tier_name} fits up to {cap}K.",
        "label": "See {tier_name}",
    },
    # {excluded} is a count of MEETINGS IN THE PROJECT the tier could not
    # use, never a count of matches that scored (CQ contract 2026-08-23:
    # by_scope = meetings holding memory the People render cannot use;
    # by_window = meetings whose last observation is older than the
    # window). Copy must claim the project count, not "memory found".
    "memory_excluded_scope": {
        "text": "Memory from {excluded} meeting{plural} in this project is not available on {tier_name}. {next_tier} brings it into every conversation.",
        "label": "See {next_tier}",
    },
    "memory_excluded_window": {
        "text": "This project has {excluded} meeting{plural} older than {window} days, outside the {tier_name} window. {next_tier} has {next_window}.",
        "next_window_none": "no window",
        "next_window_days": "a {n} day window",
        "label": "See {next_tier}",
    },
}

TIER_NAMES = {"free": "Free", "plus": "Plus", "pro": "Pro"}


def _copy(remote_configs: dict, key: str) -> dict[str, str]:
    """Served copy wins; code default is only the floor so the feature
    cannot ship a raw placeholder if a locale bundle lacks the block."""
    cfg = (remote_configs.get("tiers") or {}).get("upgrade_nudges") or {}
    block = cfg.get(key) or {}
    return {**DEFAULT_COPY[key], **{k: v for k, v in block.items() if isinstance(v, str)}}


def next_tier_that_fits(
    remote_configs: dict,
    current_tier: str,
    needed: int,
    cap_for_tier,
) -> tuple[str, int] | None:
    """The lowest tier ABOVE `current_tier` whose cap satisfies `needed`.

    `cap_for_tier(tier) -> int | None` reads the served dial; -1 or None
    means uncapped and always fits. Returns (tier, cap) or None when no
    higher tier fits, in which case the only honest advice is to trim.

    Never returns the current tier: a user already on it is not being
    asked to buy it.
    """
    if current_tier not in LADDER:
        return None
    for tier in LADDER[LADDER.index(current_tier) + 1:]:
        cap = cap_for_tier(tier)
        if cap is None or cap == -1 or cap >= needed:
            return tier, (cap if isinstance(cap, int) else -1)
    return None


def context_upgrade_action(
    remote_configs: dict,
    current_tier: str,
    actual_chars: int,
    cap_for_tier,
) -> dict[str, Any] | None:
    """Secondary action for the `context_too_large` block: the next tier
    that would have fit THIS request, with both numbers in it. None when
    no tier fits, so the block stays a plain "trim" with no false
    affordance."""
    fit = next_tier_that_fits(remote_configs, current_tier, actual_chars, cap_for_tier)
    if not fit:
        return None
    tier, cap = fit
    copy = _copy(remote_configs, "context_fits_higher")
    name = TIER_NAMES.get(tier, tier)
    cap_k = "unlimited" if cap == -1 else f"{cap // 1000}"
    return {
        "label": _fmt(copy["label"], tier_name=name),
        "action": "open_paywall",
        "plan": tier,
        "reason": _fmt(copy["text"], needed=actual_chars // 1000, cap=cap_k, tier_name=name),
    }


def _tier_display(remote_configs: dict, tier: str) -> str:
    t = ((remote_configs.get("tiers") or {}).get("tiers") or {}).get(tier) or {}
    name = t.get("display_name") if isinstance(t, dict) else None
    return name if isinstance(name, str) and name else tier.capitalize()


def memory_excluded_cta(
    remote_configs: dict,
    current_tier: str,
    excluded: dict[str, Any] | None,
    window_days: int | None,
) -> dict[str, Any] | None:
    """A feature_state for the chat envelope when CQ reports matches it
    could not use. Two shapes, by what did the excluding:

      by_scope   a not-enabled tier's People-scoped recall left matches out
      by_window  a windowed tier's N-day window left older matches out

    The TARGET is read from the dials, never assumed from a tier name
    (Scott via CQ, 2026-08-26: mode and window are two independent dials
    per tier and every combination must work by configuration alone).
    by_scope sells the lowest higher tier whose memory mode is "enabled";
    by_window sells the lowest higher tier whose window is wider or
    unlimited. No such tier: silence. Copy placeholders: {excluded}
    {plural} {window} {tier_name} {next_tier} {next_window}.

    `excluded` is CQ's additive block: {"by_scope": {"meetings": n},
    "by_window": {"meetings": n, "oldest": iso}}. Any of it may be
    absent. Zero is silence, not a nudge: a number that is not there is
    the one thing this function must never invent.
    """
    if not isinstance(excluded, dict) or current_tier not in LADDER:
        return None
    from app.services.entitlements import entitlement_state
    from app.services.recall_window import recall_max_age_days

    def _n(block) -> int:
        v = (block or {}).get("meetings") if isinstance(block, dict) else None
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    by_scope = _n(excluded.get("by_scope"))
    by_window = _n(excluded.get("by_window"))
    above = LADDER[LADDER.index(current_tier) + 1:]
    # ⚠ NOT app-scoped: this helper has no request and renders ShoulderSurf's
    # memory upsell copy. Reads the flat matrix, which is SS's. If another
    # app ever renders these strings, thread app_id from its caller first.
    mode = entitlement_state(remote_configs, current_tier, "context_quilt")
    cur = window_days if isinstance(window_days, int) and not isinstance(window_days, bool) and window_days >= 1 else None
    me = _tier_display(remote_configs, current_tier)

    if mode != "enabled" and by_scope:
        plan = next((t for t in above if entitlement_state(remote_configs, t, "context_quilt") == "enabled"), None)
        if plan is None:
            return None
        copy = _copy(remote_configs, "memory_excluded_scope")
        n = by_scope
        fmt = dict(excluded=n, plural="" if n == 1 else "s", tier_name=me,
                   next_tier=_tier_display(remote_configs, plan))
        kind = "memory_excluded_scope"
    elif cur and by_window:
        def _wider(t):
            w = recall_max_age_days(remote_configs, t)
            return w is None or w > cur
        plan = next((t for t in above if _wider(t)), None)
        if plan is None:
            return None
        copy = _copy(remote_configs, "memory_excluded_window")
        n = by_window
        nw = recall_max_age_days(remote_configs, plan)
        phrase = _copy(remote_configs, "memory_excluded_window").get(
            "next_window_none" if nw is None else "next_window_days",
            DEFAULT_COPY["memory_excluded_window"]["next_window_none" if nw is None else "next_window_days"])
        fmt = dict(excluded=n, plural="" if n == 1 else "s", window=cur, tier_name=me,
                   next_tier=_tier_display(remote_configs, plan),
                   next_window=_fmt(phrase, n=nw if nw is not None else ""))
        kind = "memory_excluded_window"
    else:
        return None
    text = _fmt(copy["text"], **fmt)
    label = _fmt(copy["label"], **fmt)

    return {
        "feature": "context_quilt",
        "state": "teaser",
        "cta": {
            "kind": kind,
            "text": text,
            "primary_action": {"label": label, "action": "open_paywall", "plan": plan},
            "secondary_action": {"label": "Not now", "action": "dismiss"},
            "details": {"excluded_meetings": n, **({"window_days": cur} if cur else {}),
                        "next_tier": plan},
        },
    }
