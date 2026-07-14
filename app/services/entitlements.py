"""Feature entitlement matrix — the Phase 2 single source of truth
(docs/design/feature-entitlements.md, approved 2026-07-13, built
2026-07-14 after the Tiers-tab feature editor was found writing the
ephemeral in-image tiers.yml).

The matrix lives in the `entitlements` remote config:

    {"version": N, "matrix": {feature: {tier: "enabled"|"teaser"|"disabled"}}}

One object is simultaneously what enforcement reads, what the dashboard
edits (PUT hot-reloads app.state.remote_configs), and what
/v1/config/entitlements serves — a cell flip is the enforcement change,
by construction. tiers.yml keeps limits/pricing/display; features.yml
keeps definitions and copy. A missing cell resolves "disabled", the same
default TierDefinition.feature_state carried.

App scoping: reads the flat slug — feature enforcement is app-agnostic
today, exactly like the tiers.yml blocks it replaces. Per-app matrices
(techrehearsal/entitlements) are issue #356's call.
"""

import logging

logger = logging.getLogger(__name__)

STATES = ("enabled", "teaser", "disabled")
SLUG = "entitlements"


def entitlement_matrix(remote_configs: dict) -> dict:
    cfg = remote_configs.get(SLUG) or {}
    matrix = cfg.get("matrix")
    return matrix if isinstance(matrix, dict) else {}


def entitlement_state(remote_configs: dict, tier_name: str, feature: str) -> str:
    """The single resolver — every feature-state read routes through here."""
    cells = entitlement_matrix(remote_configs).get(feature)
    state = cells.get(tier_name) if isinstance(cells, dict) else None
    return state if state in STATES else "disabled"


def resolved_features(remote_configs: dict, tier_name: str) -> dict[str, str]:
    """The full {feature: state} map for one tier — the wire shape
    `tier.features` used to serve (usage/me, tiers catalog)."""
    return {f: entitlement_state(remote_configs, tier_name, f)
            for f in sorted(entitlement_matrix(remote_configs))}


def validate_matrix(data: dict, *, known_features: set, known_tiers: set) -> list[str]:
    """Closed-enum write validation (the closed-enum lesson): a malformed
    matrix never loads — the caller rejects the write and the last good
    config stays live. Returns human-readable problems, empty = valid."""
    matrix = data.get("matrix")
    if not isinstance(matrix, dict):
        return ["entitlements config must carry a 'matrix' object"]
    errors: list[str] = []
    for feature, cells in matrix.items():
        if feature not in known_features:
            errors.append(f"unknown feature '{feature}'")
        if not isinstance(cells, dict):
            errors.append(f"'{feature}' must map tiers to states")
            continue
        for tier, state in cells.items():
            if tier not in known_tiers:
                errors.append(f"unknown tier '{tier}' on '{feature}'")
            if state not in STATES:
                errors.append(
                    f"invalid state '{state}' on '{feature}.{tier}' "
                    f"(must be one of {', '.join(STATES)})")
    return errors


def completeness_warnings(remote_configs: dict, *, known_features: set,
                          known_tiers: set) -> list[str]:
    """Absent known cells (they resolve 'disabled' silently) — logged at
    startup so a half-authored matrix is visible, never enforced."""
    matrix = entitlement_matrix(remote_configs)
    return [f"{feature}.{tier}"
            for feature in sorted(known_features)
            for tier in sorted(known_tiers)
            if tier not in (matrix.get(feature) or {})]
