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
    # {excluded} meetings the recall found and could not use.
    "memory_excluded_scope": {
        "text": "This answer skipped {excluded} earlier meeting{plural} that memory found. Plus brings them into every conversation.",
        "label": "See Plus",
    },
    "memory_excluded_window": {
        "text": "{excluded} matching meeting{plural} older than {window} days were out of reach. Pro has no window.",
        "label": "See Pro",
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


def memory_excluded_cta(
    remote_configs: dict,
    current_tier: str,
    excluded: dict[str, Any] | None,
    window_days: int | None,
) -> dict[str, Any] | None:
    """A feature_state for the chat envelope when CQ reports matches it
    could not use. Two shapes, by what did the excluding:

      by_scope   Free's People-scoped recall left matches out  -> Plus
      by_window  Plus's N-day window left older matches out    -> Pro

    `excluded` is CQ's additive block: {"by_scope": {"meetings": n},
    "by_window": {"meetings": n, "oldest": iso}}. Any of it may be
    absent. Zero is silence, not a nudge: a number that is not there is
    the one thing this function must never invent.
    """
    if not isinstance(excluded, dict) or current_tier not in LADDER:
        return None

    def _n(block) -> int:
        v = (block or {}).get("meetings") if isinstance(block, dict) else None
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    by_scope = _n(excluded.get("by_scope"))
    by_window = _n(excluded.get("by_window"))

    if current_tier == "free" and by_scope:
        copy = _copy(remote_configs, "memory_excluded_scope")
        n, plan = by_scope, "plus"
        text = _fmt(copy["text"], excluded=n, plural="" if n == 1 else "s")
        kind = "memory_excluded_scope"
    elif current_tier == "plus" and by_window and window_days:
        copy = _copy(remote_configs, "memory_excluded_window")
        n, plan = by_window, "pro"
        text = _fmt(copy["text"], excluded=n, plural="" if n == 1 else "s", window=window_days)
        kind = "memory_excluded_window"
    else:
        return None

    return {
        "feature": "context_quilt",
        "state": "teaser",
        "cta": {
            "kind": kind,
            "text": text,
            "primary_action": {"label": copy["label"], "action": "open_paywall", "plan": plan},
            "secondary_action": {"label": "Not now", "action": "dismiss"},
            "details": {"excluded_meetings": n, **({"window_days": window_days} if window_days else {})},
        },
    }
