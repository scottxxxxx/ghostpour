"""Per-tier Memory recall window (2026-08-21, Scott's ruling via CQ).

Plus gets "your last N days in every conversation"; Pro gets everything.
The number is a DIAL, not copy: it lives at
`tiers.{tier}.feature_definitions.context_quilt.recall_max_age_days` in
the served tiers bundle, is edited from the dashboard like the other
per-tier dials, and is read here by the one resolver every consumer uses
(the recall leg, the served copy templating, the tiers endpoint). CQ
holds no default and no opinion about the value; it only applies what
we send as `metadata.max_age_days` on /v1/recall.

Semantics of the value:
  int >= 1  -> window in days, sent to CQ
  null/absent block -> unlimited, key NOT sent (CQ's "absent = no window")
Free has no quilt leg (teaser), so its value is never read on a recall.
"""
from __future__ import annotations

FEATURE = "context_quilt"
FIELD = "recall_max_age_days"


def _tiers(remote_configs: dict, locale: str | None = None) -> dict | None:
    if locale and locale != "en":
        cfg = remote_configs.get(f"tiers.{locale}")
        if cfg:
            return cfg
    return remote_configs.get("tiers")


def recall_max_age_days(remote_configs: dict, tier: str) -> int | None:
    """The window for `tier`, or None for unlimited / unconfigured.

    Locale-independent on purpose: the English bundle is authoritative
    for numbers, so a dashboard save that lands on every locale in
    lockstep cannot leave one locale on a different window.
    """
    cfg = _tiers(remote_configs)
    block = (((cfg or {}).get("tiers") or {}).get(tier) or {}).get("feature_definitions") or {}
    value = (block.get(FEATURE) or {}).get(FIELD)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 1 else None


# --- Served copy templating (2026-08-21) -----------------------------------
#
# The App Store listing never states the window (static text, resubmission
# to change). The SERVED copy (tier cards, gate and teaser CTAs) carries
# `{recall_window_days}` and is filled here, at serve time, from the Plus
# dial, so a dashboard change propagates to what users read. The
# placeholder is the Plus number by definition of the copy: Plus is "your
# last N days", Pro is "everything". Exactly one placeholder name, on
# purpose; a second spelling is how a copy string ends up shipping raw.
PLACEHOLDER = "{recall_window_days}"


def render_recall_window_copy(payload, remote_configs: dict):
    """Return a copy of `payload` with every `{recall_window_days}` filled.

    Pure: the served bundle in app.state is never mutated, so a later
    dial change renders fresh. If the Plus dial is unset (None) the copy
    has nothing true to say, so the placeholder is replaced by "recent"
    and a warning names the string: a visible wobble in grammar beats a
    shipped brace or an invented number.
    """
    days = recall_max_age_days(remote_configs, "plus")
    fill = str(days) if days is not None else "recent"

    def walk(node):
        if isinstance(node, str):
            if PLACEHOLDER in node:
                if days is None:
                    import logging
                    logging.getLogger(__name__).warning(
                        "recall_window_copy_unfilled", extra={"string": node[:80]})
                return node.replace(PLACEHOLDER, fill)
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(payload)
