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
    # New builds only (the request carries selected_provider). SS's client
    # decodes verdicts as a STRICT enum, so an older build must never see
    # these: an unknown verdict fails its decode (SS, 2026-10-13).
    "unlock_required",
    "quota_exhausted",
]
CtaKind = Literal["login_required", "unlock_required", "quota_exhausted"]
SelectedProvider = Literal["ssai", "apple_cloud", "byok", "on_device"]
# Which one-time unlock lets a free user run Project Chat on which model.
# On-device has no pack (Scott's ruling): only the switch to SS AI.
PROVIDER_UNLOCK = {"apple_cloud": "unlock_apple_cloud", "byok": "unlock_byok"}
FREE_TASTE_PER_MONTH = 1
GpChatFlag = Literal["all", "ssai", "ssai_free_only", "logged_in", "plus"]
SelectedModel = Literal["ssai", "external"]
Tier = Literal["free", "plus", "pro", "admin"]


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    cta_kind: CtaKind | None
    unlock: str | None = None
    switch_to_ssai: bool = False


def resolve_project_chat_verdict(
    *,
    is_logged_in: bool,
    tier: Tier | None,
    gp_chat_flag: GpChatFlag,
    selected_model: SelectedModel,
    selected_provider: SelectedProvider | None = None,
    is_anonymous: bool = False,
    owned_unlocks: frozenset[str] | set[str] = frozenset(),
    taste_used: int = 0,
    taste_includes_this_session: bool = False,
) -> VerdictResult:
    """Resolve the Project Chat verdict for a single send.

    Args:
        is_logged_in: Whether the request carries a valid JWT.
        tier: User's tier ("free" | "plus" | "pro" | "admin"). None when not logged in.
        gp_chat_flag: Server-controlled policy mode.
        selected_model: "ssai" if user picked SS AI, else "external".
    """
    # Free under ssai_free_only, NEW builds only (Scott's rulings,
    # 2026-10-13). An old build sends no selected_provider and takes exactly
    # the path below it has always taken.
    if (selected_provider is not None and gp_chat_flag == "ssai_free_only"
            and is_logged_in and tier == "free"):
        if selected_provider == "ssai":
            # The free monthly taste: one conversation a month, signed-in
            # (Apple) accounts only. Follow-ups in a conversation already
            # counted are part of it.
            if is_anonymous:
                return VerdictResult(verdict="login_required", cta_kind="login_required")
            if taste_includes_this_session or taste_used < FREE_TASTE_PER_MONTH:
                return VerdictResult(verdict="send_to_gp", cta_kind=None)
            return VerdictResult(verdict="quota_exhausted", cta_kind="quota_exhausted")
        unlock = PROVIDER_UNLOCK.get(selected_provider)
        if unlock and unlock in owned_unlocks:
            return VerdictResult(verdict="send_to_user_model", cta_kind=None)
        return VerdictResult(verdict="unlock_required", cta_kind="unlock_required",
                             unlock=unlock, switch_to_ssai=True)

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


# --- the free taste counter --------------------------------------------------

def taste_period(now=None) -> str:
    from datetime import datetime, timezone
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


async def taste_state(db, user_id: str, session_id: str | None) -> tuple[int, bool]:
    """(distinct conversations counted this month, whether this one is)."""
    period = taste_period()
    used = (await (await db.execute(
        "SELECT COUNT(*) FROM project_chat_taste WHERE user_id = ? AND period = ?",
        (user_id, period))).fetchone())[0]
    mine = False
    if session_id:
        mine = (await (await db.execute(
            "SELECT 1 FROM project_chat_taste WHERE user_id = ? AND period = ? AND session_id = ?",
            (user_id, period, session_id.lower()))).fetchone()) is not None
    return int(used or 0), mine


async def record_taste(db, user_id: str, session_id: str | None) -> None:
    """Count this conversation toward the month. Idempotent per session."""
    from datetime import datetime, timezone
    if not session_id:
        session_id = "no-session"
    await db.execute(
        "INSERT OR IGNORE INTO project_chat_taste (user_id, period, session_id, first_at) VALUES (?, ?, ?, ?)",
        (user_id, taste_period(), session_id.lower(), datetime.now(timezone.utc).isoformat()))
    await db.commit()
