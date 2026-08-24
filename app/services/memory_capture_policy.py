"""Memory-capture policy resolver.

Pure function that decides what `/v1/capture-transcript` should do for a
given (tier, quota) combination. Mirrors the Project Chat policy module.

Verdicts:
  - capture           — fire `cq.capture()`, no upsell card
  - capture_with_cta  — fire `cq.capture()` AND surface a CTA in the next
                        /v1/quilt fetch (Free, within free monthly quota)
  - skip_with_cta     — do NOT fire `cq.capture()`; surface a CTA only
                        (Free, over quota)
  - recall_only       — neither capture nor CTA (Plus today; recall stays
                        on the chat-flow hook path)

See docs/wire-contracts/memory-capture.md for the full spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Verdict = Literal["capture", "capture_with_cta", "skip_with_cta", "recall_only"]
# "free_people_only" added 2026-08-10: a free user past their Memory quota
# whose meeting IS still being captured, because People is exempt from the
# cap. The old copy for that state said "Want your AI to remember
# meetings?", which is now simply false: it is remembering, they just
# cannot read it back yet.
CtaKind = Literal["free_within_quota_footer", "free_no_quota_only",
                  "free_people_only"]


@dataclass(frozen=True)
class MemoryVerdict:
    verdict: Verdict
    cta_kind: CtaKind | None  # None when no CTA should be surfaced


def resolve_memory_capture_verdict(
    *,
    feature_state: str,  # "enabled" | "teaser" | "disabled" — from tier
    has_quota: bool,
    people_enabled: bool = False,
) -> MemoryVerdict:
    """Resolve what to do for one capture-transcript call.

    Args:
        feature_state: The user's tier-resolved state for context_quilt.
            "enabled"  → Pro: full capture, no CTA.
            "teaser"   → Free (since 2026-08-24): same rules as disabled.
            "disabled" → Free: gated by has_quota.
        has_quota: For Free, whether the user has remaining captures this
            period. Always True for unlimited (-1) or paid tiers (paid tiers
            return early before this is checked).
        people_enabled: Whether the People feature is on for this tier.
            Scott's ruling (2026-08-10): People is EXEMPT from the free-tier
            cap. People is enabled on every tier and the only closed gate is
            signed-out, but People is built from captured meetings, so a free
            user capped at one capture a month had a permanently empty tab.
            That reads as broken rather than as locked, which is the one
            impression a gate must never give.

    Returns:
        MemoryVerdict with verdict and optional cta_kind.
    """
    if feature_state == "enabled":
        return MemoryVerdict(verdict="capture", cta_kind=None)

    # feature_state "teaser" or "disabled" → Free tier. Free flipped from
    # disabled to teaser on 2026-08-24 (Scott) so the client renders the
    # memory nudge; the capture rules below are unchanged by that flip.
    # (The old "teaser = Plus, recall only" era predates Plus being
    # enabled and no tier resolves to it any more.)
    if people_enabled:
        # Capture regardless of quota, because the capture feeds TWO things
        # and only one of them is paid. Person entities are People, which is
        # free on every tier; quilt patches are Memory, which is not.
        #
        # The quota still governs the CTA, so the upsell keeps its rhythm:
        # the first capture of the month reads as the free Memory they got,
        # and later ones say what is being built rather than claiming
        # nothing is. What changes is that we no longer SKIP the capture,
        # because skipping it starves a feature the user is entitled to in
        # order to meter one they are not.
        return MemoryVerdict(
            verdict="capture_with_cta",
            cta_kind="free_within_quota_footer" if has_quota else "free_people_only",
        )
    if has_quota:
        return MemoryVerdict(
            verdict="capture_with_cta",
            cta_kind="free_within_quota_footer",
        )
    return MemoryVerdict(
        verdict="skip_with_cta",
        cta_kind="free_no_quota_only",
    )
