"""User-facing display names for model_id values.

The `model_id` field on telemetry events carries the internal routing
identifier (e.g. "cloudzap/auto", "onDevice/foundation-models"). The
operator dashboard should render the product-facing name instead, so
"SS AI" reads as "SS AI" and not as the leaked GhostPour gateway alias
that grew out of the codename.

Add a row here whenever a new internal routing id ships, or use the
raw id as the dashboard label when there's no mapping.
"""

from __future__ import annotations

_DISPLAY_NAMES: dict[str, str] = {
    "cloudzap/auto": "SS AI",
    "onDevice/foundation-models": "Apple Foundation Models",
}


def to_display_name(model_id: str | None) -> str | None:
    """Return the product-facing label for a routing id. None passes
    through. Unknown ids are returned as-is so the dashboard still shows
    something useful and an operator can grep for it."""
    if not model_id:
        return model_id
    return _DISPLAY_NAMES.get(model_id, model_id)
