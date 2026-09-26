"""The monthly dollar allowance a user may spend, in one place.

It was computed inline, identically, in three routes (chat, usage, reports).
The anonymous purchase account (2026-09-26) adds the one rule they must all
share: an anonymous account on the free tier has NO allowance. Scott ruled
that an anonymous account gets nothing until a verified purchase binds a plan
to it, because creating one costs a single unauthenticated call and a free
allowance on it could be farmed by reinstalling or scripting. A limit of 0
blocks from the first call (`monthly_used >= limit` holds at 0 >= 0), before
the overage tolerance is ever consulted.
"""

from __future__ import annotations


def effective_monthly_limit(user, tier) -> float:
    """Dollars for this period; -1 means unlimited."""
    if user.is_anonymous and user.effective_tier == "free":
        return 0.0
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        return tier.trial_cost_limit_usd
    return tier.monthly_cost_limit_usd
