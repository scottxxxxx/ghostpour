"""Human-facing labels for dashboard/reporting surfaces.

These are DISPLAY-only mappings over the raw wire values the client sends.
The stored `call_type` (a client-owned field, persisted verbatim) is never
rewritten — we only relabel it when rendering the admin dashboard so the
tables read cleanly.
"""


# The SS client tags its end-of-meeting summary-consolidation pass with
# call_type="analysis", the same value it uses for the genuine
# PostSessionAnalysis call. The two are distinguishable only by prompt_mode.
# Surface the consolidation pass under its own label so the dashboard doesn't
# lump 100 consolidation rolls in with real analysis calls.
#
# NOTE: the equivalent split for the by-call-type *aggregation* is done with a
# SQL CASE expression (see webhooks.py get_user_detail) so the weighted latency
# average stays correct — keep the two in sync.
def display_call_type(call_type: str | None, prompt_mode: str | None) -> str | None:
    """Return the dashboard label for a (call_type, prompt_mode) pair."""
    if call_type == "analysis" and prompt_mode == "SummaryConsolidation":
        return "consolidation"
    return call_type
