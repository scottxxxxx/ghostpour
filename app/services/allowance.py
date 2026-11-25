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


def effective_monthly_limit(user, tier, anonymous_capped: bool = False) -> float:
    """Dollars for this period; -1 means unlimited.

    `anonymous_capped` is True only when the app being called gives anonymous
    accounts their own capped allowance (app/services/anonymous_budget.py,
    N-400 since 2026-10-05). Then the shared meter steps aside for the
    anonymous free account because that app's per-install and daily caps run
    instead. Every other anonymous call keeps the zero, so ShoulderSurf's rule
    is unchanged and a missing N-400 cap fails closed."""
    if user.is_anonymous and user.effective_tier == "free":
        return -1 if anonymous_capped else 0.0
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        return tier.trial_cost_limit_usd
    return tier.monthly_cost_limit_usd


# WHAT THE ALLOWANCE COUNTS. Promo calls (app/services/promo_metering.py, SS's
# compare_summary since 2026-11-25) are served free and must never count
# toward the monthly allowance. The allowance is counted in two places and
# both read this:
#
#   * the meter, users.monthly_used_usd, which UsageTracker.record_cost adds
#     to. record_cost asks counts_toward_allowance() before it writes.
#   * the real spend read, allocation_reset.real_spend_alerts, which sums
#     usage_log over the user's period against their cap. It filters rows
#     with ALLOWANCE_USAGE_LOG_FILTER.
#
# Promo rows stay in usage_log with their real cost, so GP's own spend on
# them still shows on every dashboard view that does not apply this filter.
ALLOWANCE_USAGE_LOG_FILTER = "promo IS NULL"


def counts_toward_allowance(promo: str | None) -> bool:
    """False for a call served under a promo; True for every metered call."""
    return not promo
