"""Pre-call cost estimate + credit-based budget gate.

Internally everything is dollars (single source of truth from
`monthly_cost_limit_usd` per tier). Wire-facing fields use credits
to obfuscate vendor pricing and let us shift the conversion later
without an iOS update.

Conversion: 1 cent = 100 credits → 1 USD = 10,000 credits. Free's
$0.35 limit surfaces as 3,500 credits; the $0.05 overage tolerance
as 500 credits.
"""

from __future__ import annotations

import logging

from app.services.pricing import PricingService

logger = logging.getLogger("ghostpour.budget_gate")

# 1 cent = 100 credits → 1 USD = 10,000 credits.
# Free $0.35 → 3,500 credits. $0.05 overage → 500 credits.
CREDITS_PER_DOLLAR = 10_000

# Allowed overage above the monthly cap. A user can land mid-call into
# this band; the next call will be blocked. Matches the SS comm.
OVERAGE_TOLERANCE_USD = 0.05

# Output-tokens fallback when the request didn't specify max_tokens.
# Matches the default we already pass to the report builder.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


def dollars_to_credits(usd: float) -> int:
    """Round to nearest credit. Internal $ → wire credits."""
    return int(round(usd * CREDITS_PER_DOLLAR))


def estimate_input_tokens(text: str) -> int:
    """Char/4 heuristic — matches iOS's `(text.count + 3) / 4` exactly so
    the client-side fuel gauge and the server-side gate agree."""
    return (len(text) + 3) // 4


def estimate_call_cost_usd(
    pricing: PricingService,
    provider: str,
    model: str,
    input_tokens: int,
    max_output_tokens: int | None,
) -> float | None:
    """Worst-case cost estimate for a chat call. Returns None if the model
    pricing isn't loaded — caller should treat None as "skip the gate"
    (fail open) so a transient pricing outage doesn't blanket-block users.

    Uses input_token count + max_output_tokens (worst case the model
    might emit). Real cost is recorded post-call by usage_tracker.
    """
    info = pricing.get_model_pricing(provider, model)
    if info is None:
        return None

    input_per_token = info.get("input_cost_per_token") or 0
    output_per_token = info.get("output_cost_per_token") or 0
    if input_per_token == 0 and output_per_token == 0:
        return None

    out_tokens = max_output_tokens if max_output_tokens is not None else DEFAULT_MAX_OUTPUT_TOKENS
    return input_tokens * input_per_token + out_tokens * output_per_token


def would_exceed_budget(
    monthly_used_usd: float,
    estimated_cost_usd: float,
    effective_limit_usd: float,
) -> bool:
    """True if running this call would push the user past
    `effective_limit + OVERAGE_TOLERANCE`. -1 limit means unlimited."""
    if effective_limit_usd == -1:
        return False
    return monthly_used_usd + estimated_cost_usd > effective_limit_usd + OVERAGE_TOLERANCE_USD
