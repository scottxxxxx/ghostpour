"""Server-side reads of tunable parameters from the live tiers.json
(and locale variants). Source of truth is the persistent
`config/remote/tiers.json` (mounted at /app/data/remote-config in prod);
`config/tiers.yml` provides a default that's only used when the JSON
field is missing entirely.

Today this only covers `max_input_tokens` (Project Chat context cap).
The same pattern extends naturally to `monthly_cost_limit_usd`,
`free_quota_per_month`, etc. as we add tunable surfaces to the
dashboard.
"""

from __future__ import annotations

from typing import Any


def _read_json_field(
    remote_configs: dict[str, dict] | None,
    tier_name: str,
    feature: str,
    field: str,
) -> Any | None:
    """Try `tiers.json`'s tiers.{tier}.feature_definitions.{feature}.{field}.
    Returns None if any node in the path is missing — caller falls back."""
    if not remote_configs:
        return None
    cfg = remote_configs.get("tiers")
    if not cfg:
        return None
    tier_block = (cfg.get("tiers") or {}).get(tier_name)
    if not tier_block:
        return None
    return (
        (tier_block.get("feature_definitions") or {})
        .get(feature, {})
        .get(field)
    )


def project_chat_max_input_tokens(
    remote_configs: dict[str, dict] | None,
    tier_name: str,
    yaml_default: int,
) -> int:
    """Resolve the per-tier Project Chat context cap.

    Order of precedence:
      1. tiers.json: tiers.{tier}.feature_definitions.project_chat.max_input_tokens
         — dashboard-editable, source of truth.
      2. tiers.yml: tier.max_input_tokens (passed in as `yaml_default`)
         — only used when the JSON field is missing entirely.

    Returns -1 (uncapped) for unknown tiers if neither source has a value.
    """
    json_value = _read_json_field(
        remote_configs,
        tier_name,
        "project_chat",
        "max_input_tokens",
    )
    if json_value is not None:
        try:
            return int(json_value)
        except (TypeError, ValueError):
            pass
    return yaml_default if yaml_default is not None else -1
