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

import re

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


def _shared_window_tier(remote_configs: dict) -> str:
    """The tier a SHARED string (feature_definitions, cta_strings, paywall)
    talks about: the lowest tier whose memory mode is enabled, i.e. the
    upgrade target ("Plus brings your last N days"). Read from the mode
    dial, not assumed; Plus when the matrix is silent."""
    from app.services.entitlements import entitlement_state
    for t in ("free", "plus", "pro"):
        if entitlement_state(remote_configs, t, "context_quilt") == "enabled":
            return t
    return "plus"


def render_recall_window_copy(payload, remote_configs: dict):
    """Return a copy of `payload` with every `{recall_window_days}` filled.

    Pure: the served bundle in app.state is never mutated, so a later
    dial change renders fresh. Each tier's OWN strings (anything under
    `tiers.<name>`, in the bundle or the /v1/tiers response, both keyed
    by tier name) read that tier's dial; strings outside a tier block
    read the upgrade target's dial (`_shared_window_tier`). A dial that is
    unset for a string that names a number has nothing true to say, so
    the placeholder is replaced by "recent" and a warning names the
    string: a visible wobble in grammar beats a shipped brace or an
    invented number.
    """
    shared_days = recall_max_age_days(remote_configs, _shared_window_tier(remote_configs))

    def fill(node, days):
        if PLACEHOLDER in node:
            if days is None:
                import logging
                logging.getLogger(__name__).warning(
                    "recall_window_copy_unfilled", extra={"string": node[:80]})
            return node.replace(PLACEHOLDER, str(days) if days is not None else "recent")
        return node

    def walk(node, days, at_top=False):
        if isinstance(node, str):
            return fill(node, days)
        if isinstance(node, list):
            return [walk(x, days) for x in node]
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if at_top and k == "tiers" and isinstance(v, dict):
                    out[k] = {name: walk(block, recall_max_age_days(remote_configs, name) if isinstance(name, str) else days)
                              for name, block in v.items()}
                else:
                    out[k] = walk(v, days)
            return out
        return node
    return walk(payload, shared_days, at_top=True)


# --- Server-side window on the meeting content itself (2026-08-26) ---------
#
# Scott's ruling (via CQ, supersedes earlier): a Plus user's project chat
# is hydrated with the LAST N DAYS of meetings, a sliding window ending
# now, even when the project holds more; Pro has no window; N is the dial
# above, never a constant. CQ applies N to memory patches. The meeting
# content (summaries, transcript excerpts, prior Q&A) is assembled by the
# client into system_prompt from its slider, and until now GP passed it
# through whatever the slider said. The slider is UI, not a gate; this is
# the gate, reading the SAME dial, so there is no hole between the halves.
#
# Anchor: the assembly contract (docs/wire-contracts/project-chat-prompt-
# assembly.md) puts every meeting under a dated H2:
#     ## Meeting {i} of {N} — {YYYY-MM-DD} · "{title}" ({relative_time})
# A block runs to the next H2 or the end. Numbering is NOT rewritten (the
# client keeps an i -> meeting_id map for follow-up actions); only the
# "You have context from N meeting(s), spanning A to B" preamble is
# re-stated so the model is not told about meetings it cannot see.
# Fail open per block: a header whose date does not parse is kept.

_MEETING_H2 = __import__("re").compile(
    r"^## Meeting (\d+) of (\d+) [\u2014\-\u2013] (\d{4}-\d{2}-\d{2})\b", re.M)
_PREAMBLE = __import__("re").compile(
    r"You have context from (?:one meeting, dated \d{4}-\d{2}-\d{2}|\d+ meeting\(s\)?, spanning "
    r"\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2})(, ordered oldest \u2192 newest)?")


def clamp_meeting_blocks(system_prompt: str | None, max_age_days: int | None,
                         today=None) -> tuple[str | None, list[str]]:
    """Drop every dated meeting block older than `max_age_days` (UTC days,
    inclusive: a meeting exactly N days old is still in). Returns the
    prompt and the dates dropped, in order. No window, no prompt, or no
    dated blocks: unchanged."""
    if not system_prompt or not isinstance(max_age_days, int) or isinstance(max_age_days, bool) \
            or max_age_days < 1:
        return system_prompt, []
    from datetime import date, datetime, timedelta, timezone
    if today is None:
        today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=max_age_days)
    heads = list(_MEETING_H2.finditer(system_prompt))
    if not heads:
        return system_prompt, []
    # block boundaries: each block ends at the next H2 of ANY kind, or EOF
    h2_starts = [m.start() for m in re.finditer(r"^## ", system_prompt, re.M)]
    out, pos, dropped, kept_dates = [], 0, [], []
    for m in heads:
        start = m.start()
        nxt = next((s for s in h2_starts if s > start), len(system_prompt))
        try:
            when = date.fromisoformat(m.group(3))
        except ValueError:
            kept_dates.append(None)
            continue  # unreadable date: keep the block
        if when < cutoff:
            out.append(system_prompt[pos:start])
            pos = nxt
            dropped.append(m.group(3))
        else:
            kept_dates.append(when)
    out.append(system_prompt[pos:])
    text = "".join(out)
    if dropped:
        real = [d for d in kept_dates if d is not None]
        n_kept = len(kept_dates)
        if n_kept == 0:
            pre = "You have context from no meetings in the last %d days." % max_age_days
        elif n_kept == 1 and len(real) == 1:
            pre = "You have context from one meeting, dated %s." % real[0].isoformat()
        elif real:
            pre = "You have context from %d meeting(s), spanning %s to %s" % (
                n_kept, min(real).isoformat(), max(real).isoformat())
        else:
            pre = None
        if pre:
            text, n_sub = _PREAMBLE.subn(lambda mm: pre + (mm.group(1) or ""), text, count=1)
    return text, dropped
