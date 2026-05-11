"""Project Chat policy resolver.

Pure function that implements the state matrix for Project Chat routing.
Given the user's auth/tier state, the server-controlled `gp_chat_flag`,
and the user's selected model, it returns a verdict telling the iOS
client what to do.

See docs/wire-contracts/project-chat.md for the full state table.

Free-tier metering is no longer a count quota; the budget gate
(app/services/budget_gate.py) is the authoritative Free-tier blocker.
This resolver is purely about routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Verdict = Literal[
    "send_to_gp",
    "send_to_user_model",
    "login_required",
]
CtaKind = Literal["login_required"]
GpChatFlag = Literal["all", "ssai", "ssai_free_only", "logged_in", "plus"]
SelectedModel = Literal["ssai", "external"]
Tier = Literal["free", "plus", "pro", "admin"]


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    cta_kind: CtaKind | None


def resolve_project_chat_verdict(
    *,
    is_logged_in: bool,
    tier: Tier | None,
    gp_chat_flag: GpChatFlag,
    selected_model: SelectedModel,
) -> VerdictResult:
    """Resolve the Project Chat verdict for a single send.

    Args:
        is_logged_in: Whether the request carries a valid JWT.
        tier: User's tier ("free" | "plus" | "pro" | "admin"). None when not logged in.
        gp_chat_flag: Server-controlled policy mode.
        selected_model: "ssai" if user picked SS AI, else "external".
    """
    if gp_chat_flag == "all":
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    if gp_chat_flag == "logged_in":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    if gp_chat_flag == "ssai":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        return VerdictResult(verdict="send_to_gp", cta_kind=None)

    if gp_chat_flag == "ssai_free_only":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        if tier == "free":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    if gp_chat_flag == "plus":
        if not is_logged_in:
            return VerdictResult(verdict="login_required", cta_kind="login_required")
        if tier == "free":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        if selected_model == "ssai":
            return VerdictResult(verdict="send_to_gp", cta_kind=None)
        return VerdictResult(verdict="send_to_user_model", cta_kind=None)

    return VerdictResult(verdict="login_required", cta_kind="login_required")
