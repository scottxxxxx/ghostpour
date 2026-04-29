"""Project Chat policy resolver.

Pure function that implements the state matrix Scott specified for Project
Chat routing. Given the user's auth/tier state, the server-controlled
gp_chat_flag, the user's selected model, and free-tier quota state, it
returns a verdict telling the iOS client what to do.

See docs/wire-contracts/project-chat.md for the full state table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Type aliases for readability
Verdict = Literal[
    "send_to_gp",
    "send_to_gp_with_cta",
    "send_to_user_model",
    "login_required",
]
CtaKind = Literal[
    "quota_remaining",
    "quota_exhausted",
    "unlimited",
    "login_required",
]
GpChatFlag = Literal["all", "ssai", "ssai_free_only", "logged_in", "plus"]
SelectedModel = Literal["ssai", "external"]
Tier = Literal["free", "plus", "pro", "admin"]


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    cta_kind: CtaKind | None  # None when no CTA should accompany the verdict


def resolve_project_chat_verdict(
    *,
    is_logged_in: bool,
    tier: Tier | None,
    gp_chat_flag: GpChatFlag,
    selected_model: SelectedModel,
    has_quota: bool,
    free_quota_per_month: int,
) -> VerdictResult:
    """Resolve the Project Chat verdict for a single send.

    Args:
        is_logged_in: Whether the request carries a valid JWT.
        tier: User's tier ("free" | "plus" | "pro" | "admin"). None when not logged in.
        gp_chat_flag: Server-controlled policy mode.
        selected_model: "ssai" if user picked SS AI, else "external".
        has_quota: Whether the user has remaining free Project Chat quota
            this period. Always True for unlimited (-1) or paid tiers.
        free_quota_per_month: Configured quota cap. Determines CtaKind for
            CTA-bearing verdicts: 0/positive => quota_*; -1 => unlimited.

    Returns:
        VerdictResult with the verdict and optional cta_kind.

    Notes:
        - Tier is treated as "free" when is_logged_in is True but tier is None
          (defensive). Callers should pass an explicit tier whenever possible.
        - Free-tier quota only affects CTA copy in modes where Free users
          actually receive a CTA-bearing outcome (ssai with Other model, or
          plus mode). In other modes it has no effect.
    """
    # Determine the CTA kind for "Free user receives a CTA-wrap" outcomes.
    # This isolates the unlimited / quota_remaining / quota_exhausted
    # decision in one place.
    def _cta_kind_for_free_with_cta() -> CtaKind:
        if free_quota_per_month == -1:
            return "unlimited"
        if has_quota:
            return "quota_remaining"
        return "quota_exhausted"

    # =========================================================
    # gp_chat_flag = "all" — most permissive; no login required
    # =========================================================
    if gp_chat_flag == "all":
        # Routing follows the user's model selection, regardless of tier or
        # auth state.
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    # =========================================================
    # gp_chat_flag = "logged_in" — auth required; user's model
    # =========================================================
    if gp_chat_flag == "logged_in":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    # =========================================================
    # gp_chat_flag = "ssai" — auth required; SS AI overrides
    # user model selection (paid tiers too). Free users with
    # their own model selected get a CTA wrap when out of quota.
    # =========================================================
    if gp_chat_flag == "ssai":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        # All logged-in users route to GP. Free + external + no quota gets CTA.
        if tier == "free" and selected_model == "external" and not has_quota:
            return VerdictResult(
                verdict="send_to_gp_with_cta",
                cta_kind=_cta_kind_for_free_with_cta(),
            )
        return VerdictResult(verdict="send_to_gp", cta_kind=None)

    # =========================================================
    # gp_chat_flag = "ssai_free_only" — auth required. Hybrid:
    # ssai semantics for Free tier (override user model, metered
    # gate via CTA), logged_in semantics for paid tiers (respect
    # the BYOK choice). Use this when you want both the Free
    # conversion nudge AND BYOK respect for paying users.
    # =========================================================
    if gp_chat_flag == "ssai_free_only":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        if tier == "free":
            # Same shape as ssai mode for Free tier
            if selected_model == "external" and not has_quota:
                return VerdictResult(
                    verdict="send_to_gp_with_cta",
                    cta_kind=_cta_kind_for_free_with_cta(),
                )
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        # Plus / Pro / Admin: respect their model choice (logged_in semantics)
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    # =========================================================
    # gp_chat_flag = "plus" — Plus/Pro required for unrestricted
    # use. Free users always get GP-routed responses with a CTA.
    # =========================================================
    if gp_chat_flag == "plus":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        if tier == "free":
            # Always CTA-wrap for free users, regardless of selected model.
            return VerdictResult(
                verdict="send_to_gp_with_cta",
                cta_kind=_cta_kind_for_free_with_cta(),
            )
        # Plus/Pro/Admin: routing follows user's model selection
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    # Defense — unknown flag value shouldn't happen if config validates,
    # but if it does, default to most restrictive (login required).
    return VerdictResult(verdict="login_required", cta_kind="login_required")


def render_cta_text(
    cta_kind: CtaKind,
    cta_strings: dict[str, str],
    *,
    remaining: int = 0,
    total: int = 0,
) -> str:
    """Render a CTA template string with quota substitutions.

    cta_strings comes from features.yml or the locale-specific tiers config.
    Templates use {remaining} and {total} placeholders.
    """
    template = cta_strings.get(cta_kind, "")
    return template.format(remaining=remaining, total=total)
