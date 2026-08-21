"""Feature hook system for GhostPour.

Features are pluggable integrations that run before and after the LLM call.
Each feature implements the FeatureHook protocol and is registered at startup.
The chat endpoint calls hooks generically — no feature-specific code in the
main request path.

See context_quilt_hook.py for the reference implementation.
"""

from typing import Any, Protocol

from app.models.chat import ChatRequest, ChatResponse
from app.models.tier import TierDefinition
from app.models.user import UserRecord


class FeatureHook(Protocol):
    """Protocol for feature integrations in the chat flow."""

    async def before_llm(
        self,
        user: UserRecord,
        body: ChatRequest,
        tier: TierDefinition,
        feature_state: str,
        skip_teasers: set[str],
        app_id: str | None = None,
        recall_max_age_days: int | None = None,
    ) -> tuple[ChatRequest, dict[str, Any]]:
        """Run before the LLM call. May modify the request body.

        recall_max_age_days: the tier's Memory window (None = unlimited),
        resolved by the router from the served tiers dial and handed in so
        the hook never reads tier config itself.

        Returns:
            (possibly_modified_body, hook_result_dict)
            hook_result_dict is passed to after_llm and response_headers.
        """
        ...

    async def after_llm(
        self,
        user: UserRecord,
        body: ChatRequest,
        response: ChatResponse,
        hook_result: dict[str, Any],
        feature_state: str,
        app_id: str | None = None,
    ) -> None:
        """Run after the LLM call. Fire-and-forget (e.g., capture)."""
        ...

    def response_headers(
        self,
        hook_result: dict[str, Any],
        feature_state: str,
    ) -> dict[str, str]:
        """Return headers to add to the chat response."""
        ...
