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
CtaKind = Literal["free_within_quota_footer", "free_no_quota_only"]


@dataclass(frozen=True)
class MemoryVerdict:
    verdict: Verdict
    cta_kind: CtaKind | None  # None when no CTA should be surfaced


def resolve_memory_capture_verdict(
    *,
    feature_state: str,  # "enabled" | "teaser" | "disabled" — from tier
    has_quota: bool,
) -> MemoryVerdict:
    """Resolve what to do for one capture-transcript call.

    Args:
        feature_state: The user's tier-resolved state for context_quilt.
            "enabled"  → Pro: full capture, no CTA.
            "teaser"   → Plus: existing recall-only behavior; no capture, no CTA.
            "disabled" → Free: gated by has_quota.
        has_quota: For Free, whether the user has remaining captures this
            period. Always True for unlimited (-1) or paid tiers (paid tiers
            return early before this is checked).

    Returns:
        MemoryVerdict with verdict and optional cta_kind.
    """
    if feature_state == "enabled":
        return MemoryVerdict(verdict="capture", cta_kind=None)

    if feature_state == "teaser":
        return MemoryVerdict(verdict="recall_only", cta_kind=None)

    # feature_state == "disabled" → Free tier
    if has_quota:
        return MemoryVerdict(
            verdict="capture_with_cta",
            cta_kind="free_within_quota_footer",
        )
    return MemoryVerdict(
        verdict="skip_with_cta",
        cta_kind="free_no_quota_only",
    )
