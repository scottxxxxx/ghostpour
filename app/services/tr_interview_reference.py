"""Tech Rehearsal's interview reference (`tr_interview_reference`).

TR handoff docs/handoffs/tr-interview-reference-2026-10-21.md, section 1:
from a job description and a resume, GP researches the company and the
role and writes the markdown reference a live Copilot carries into the
interview. Scott ruled 2026-10-21 to take TR's proposal: paid only, counted
inside the prep's action_id with no meter of its own, and
`techrehearsal_plan_required` for free and anonymous.

Three rules live here so the route reads them in one place:

- Interview only. Negotiation, pitch and personal scenarios are out of
  scope for v1, and a generic document for them would be worse than a
  refusal, so any other scenario is a 400.
- Paid only, read from `X-TR-Entitlement` because that is the only paid
  signal TR has on the wire today (tr_budget keys its caps on the same
  header). When the server-side TR entitlement lands, this reads that.
- Web search is on for this call type and no other. The SS search caps in
  search_caps are keyed on the account tier, which is `free` for every TR
  account, so the generic flag would be paywalled; TR meters in dollars
  instead, and the search fees land in that meter.
"""

from __future__ import annotations

CALL_TYPE = "tr_interview_reference"
CONFIG_SLUG = "techrehearsal/interview-reference"
PAID_ENTITLEMENTS = frozenset({"paid"})
_DEFAULT_CTA = {"text": {"en": "The interview reference is part of the paid plan. "
                               "Start your free week to build one."}}


def refusal(call_type: str | None, scenario, entitlement: str | None) -> str | None:
    """"scenario", "plan" or None. Scenario first: a request we would never
    serve is a request-shape error whatever plan the caller holds."""
    if call_type != CALL_TYPE:
        return None
    if (scenario or "").strip().lower() != "interview":
        return "scenario"
    if (entitlement or "").strip().lower() not in PAID_ENTITLEMENTS:
        return "plan"
    return None


def plan_required_cta(remote_configs: dict | None) -> dict:
    """The served copy, so it changes without a build; a default when the
    config is unreadable, because a paywall with no words is worse."""
    cfg = (remote_configs or {}).get(CONFIG_SLUG) or {}
    cta = cfg.get("planRequiredCta")
    return cta if isinstance(cta, dict) and cta.get("text") else _DEFAULT_CTA
