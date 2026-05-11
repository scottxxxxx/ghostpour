"""Resolver for the budget-exhausted CTA copy.

Source of truth: `config/remote/tiers.{locale}.json` →
`tiers.{tier}.feature_definitions.budget.cta_exhausted`. Shape:

    {
      "kind": "budget_exhausted",
      "text": "You've used your free AI for this month. Upgrade to Plus to keep going.",
      "action": "open_paywall"
    }

The Free tier is the only one that hits this gate today (paid tiers
have unlimited budget), but the resolver is tier-agnostic so a future
"trial expired" or paid-overage variant can be slotted in without
plumbing changes.

Locale resolution matches `search_caps._resolve_tiers`: prefer the
`tiers.{locale}` config when one exists, else fall back to base `tiers`.

Falls back to a generic English CTA when config doesn't ship one so
callers never have to handle a None — keeps the inline `chat.py`
behavior intact when the tiers config is unreachable (e.g., startup
race or a malformed bundle).
"""

from __future__ import annotations

_FALLBACK_CTA: dict = {
    "kind": "budget_exhausted",
    "text": "You've used your free AI for this month. Upgrade to Plus to keep going.",
    "action": "open_paywall",
}


def _resolve_tiers(
    remote_configs: dict[str, dict],
    locale: str | None,
) -> dict | None:
    if locale and locale != "en":
        loc_key = f"tiers.{locale}"
        if loc_key in remote_configs:
            return remote_configs[loc_key]
    return remote_configs.get("tiers")


def get_budget_exhausted_cta(
    remote_configs: dict[str, dict],
    tier: str,
    locale: str | None = None,
) -> dict:
    """Resolve the budget-exhausted CTA for a tier/locale.

    Returns a fresh dict so callers can mutate without affecting the
    cached config. Always returns something — never None — so blocked
    `/v1/chat` and `/v1/capture-transcript` responses always carry a
    renderable CTA even when config is missing.
    """
    tiers_cfg = _resolve_tiers(remote_configs, locale)
    if tiers_cfg:
        tier_block = tiers_cfg.get("tiers", {}).get(tier, {})
        budget_block = tier_block.get("feature_definitions", {}).get("budget")
        if isinstance(budget_block, dict):
            cta = budget_block.get("cta_exhausted")
            if isinstance(cta, dict):
                return dict(cta)
    return dict(_FALLBACK_CTA)
