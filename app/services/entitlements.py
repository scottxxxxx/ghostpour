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

App scoping (Scott, 2026-09-02): the apps are MULTITENANT and a shared
SIWA identity does not mean shared anything else. This used to read the
flat slug unconditionally, so Tech Rehearsal and N-400 had their feature
access decided by a matrix written for ShoulderSurf. It now resolves
`{app_dir}/entitlements` FIRST and falls back to the flat slug, which is
the same order `candidate_slugs` uses for every other served config.

Two things that follow, and the second is the one to keep in mind:

  - With no per-app file present the answer is byte-identical to before,
    so introducing the axis changed no live behaviour. What each app's
    matrix SAYS is a product decision, not a mechanism one.
  - The TIER is still shared. A per-app matrix lets each app decide what
    "plus" means for it; it does not decide whether this user is plus,
    because Apple issues subscriptions per developer TEAM and `tier`
    lives on the account row. Full separation needs per-app entitlement
    on top of that, which is a revenue decision rather than a leak.

An unrecognised or absent app_id resolves to the default app's dir, i.e.
the flat file, matching resolve_app_dir. That fails open to today's
behaviour on purpose: an older build that sends no header must not lose
its features over app identity.
"""

import logging

logger = logging.getLogger(__name__)

STATES = ("enabled", "teaser", "disabled")
SLUG = "entitlements"


def matrix_slug_for(app_id: str | None) -> str:
    """The entitlements slug this app reads, e.g. `n400/entitlements`.

    Built from the registered dir rather than the raw header, so a mistyped
    X-App-ID cannot address a namespace of its own.
    """
    from app.routers.config import resolve_app_dir
    return f"{resolve_app_dir(app_id)}/{SLUG}"


def entitlement_matrix(remote_configs: dict, app_id: str | None = None) -> dict:
    """This app's matrix, falling back to the flat one.

    The fallback is not a convenience, it is what keeps the change additive:
    with no per-app file present every app resolves the flat matrix and the
    answer is exactly what it was before the axis existed.
    """
    cfg = None
    if app_id is not None:
        cfg = remote_configs.get(matrix_slug_for(app_id))
    if cfg is None:
        cfg = remote_configs.get(SLUG) or {}
    matrix = cfg.get("matrix")
    return matrix if isinstance(matrix, dict) else {}


def entitlement_state(remote_configs: dict, tier_name: str, feature: str,
                      app_id: str | None = None) -> str:
    """The single resolver — every feature-state read routes through here."""
    cells = entitlement_matrix(remote_configs, app_id).get(feature)
    state = cells.get(tier_name) if isinstance(cells, dict) else None
    return state if state in STATES else "disabled"


def resolved_features(remote_configs: dict, tier_name: str,
                      app_id: str | None = None) -> dict[str, str]:
    """The full {feature: state} map for one tier — the wire shape
    `tier.features` used to serve (usage/me, tiers catalog)."""
    matrix = entitlement_matrix(remote_configs, app_id)
    return {f: entitlement_state(remote_configs, tier_name, f, app_id)
            for f in sorted(matrix)}


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
