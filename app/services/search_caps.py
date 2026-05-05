"""Resolver for the per-tier search caps + CTA copy.

Source of truth: `config/remote/tiers.{locale}.json` ->
`tiers.{tier_name}.feature_definitions.search`. Shape:

    {
      "searches_per_month": int,           # hard cap (0 means no search at all)
      "searches_soft_threshold": int|null, # soft warning threshold (Pro only today)
      "cta_hard_cap": { kind, title, body, action, ... },
      "cta_soft_cap": { kind, title, body, action, ... } | null,
    }

Locale resolution matches `client_config._resolve_config`: if the
request carries Accept-Language and a locale variant exists, prefer it;
otherwise fall back to the default `tiers` config.

Designed to be safe in the face of partially-populated configs:
- Missing tier → returns `SearchCaps(0, ...)` (deny).
- Missing soft threshold → returned as None.
- Missing CTA → returned as None; caller decides whether to synthesize.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchCaps:
    """Per-tier resolved search-cap state for one user/locale."""

    searches_per_month: int
    """Hard cap for the rolling allocation period. 0 means search is
    not available on this tier at all (Free)."""

    searches_soft_threshold: int | None
    """Threshold at which a soft-cap CTA fires. None means no soft cap."""

    cta_hard_cap: dict | None
    """Full CTA payload (kind, title, body, primary_action, etc.) emitted
    when the user is at or past the hard cap. None if config didn't
    populate one (caller falls back to a generic exhausted message)."""

    cta_soft_cap: dict | None
    """Full CTA payload emitted when used >= soft_threshold. None means
    no soft warning for this tier."""


def _resolve_tiers(
    remote_configs: dict[str, dict],
    locale: str | None,
) -> dict | None:
    if locale and locale != "en":
        loc_key = f"tiers.{locale}"
        if loc_key in remote_configs:
            return remote_configs[loc_key]
    return remote_configs.get("tiers")


def get_search_caps(
    remote_configs: dict[str, dict],
    tier: str,
    locale: str | None = None,
) -> SearchCaps:
    """Resolve search caps + CTA copy for the given tier and locale.

    Tiers without a `search` block in their `feature_definitions` get
    treated as cap=0, no CTAs — i.e., search disabled. This matches the
    Free tier's intended behavior and serves as the safe default for any
    tier that hasn't been provisioned yet.
    """
    tiers_cfg = _resolve_tiers(remote_configs, locale)
    if not tiers_cfg:
        return SearchCaps(0, None, None, None)

    tier_block = tiers_cfg.get("tiers", {}).get(tier, {})
    search_block = tier_block.get("feature_definitions", {}).get("search")
    if not isinstance(search_block, dict):
        return SearchCaps(0, None, None, None)

    return SearchCaps(
        searches_per_month=int(search_block.get("searches_per_month", 0)),
        searches_soft_threshold=(
            int(search_block["searches_soft_threshold"])
            if search_block.get("searches_soft_threshold") is not None
            else None
        ),
        cta_hard_cap=search_block.get("cta_hard_cap"),
        cta_soft_cap=search_block.get("cta_soft_cap"),
    )


def format_cta(
    cta: dict | None,
    *,
    used: int,
    total: int,
    reset_date: str | None = None,
) -> dict | None:
    """Substitute `{used}`, `{total}`, `{reset_date}` template variables
    in the CTA's `title` and `body` fields. Other fields pass through
    unchanged.

    Returns None if the input CTA is None — caller can use this to
    decide whether to surface a CTA at all.
    """
    if not cta:
        return None
    out = dict(cta)
    fmt_args = {
        "used": used,
        "total": total,
        "reset_date": reset_date or "",
    }
    for key in ("title", "body"):
        val = out.get(key)
        if isinstance(val, str):
            try:
                out[key] = val.format(**fmt_args)
            except (KeyError, IndexError):
                # Leave unsubstituted if a variable is missing — better
                # than crashing on a malformed template.
                pass
    return out
