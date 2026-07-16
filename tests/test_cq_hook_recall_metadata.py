"""Hook-level tests: ContextQuiltHook.before_llm stashes the recall text
on `metadata.cq_recall_block` so cache-aware adapters (Anthropic) can
slice the system prompt at the recall boundary into separate
cache_control blocks.

Pairs with tests/test_anthropic_cache_split.py, which exercises the
adapter side of the contract.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatRequest
from app.models.user import UserRecord
from app.services.features.context_quilt_hook import ContextQuiltHook


def _user(tier: str = "pro") -> UserRecord:
    return UserRecord(
        id="u-1",
        apple_sub="apple-u-1",
        tier=tier,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_recall_text_is_stashed_on_metadata_when_enabled():
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE INSTRUCTIONS\n\n{{context_quilt}}\n\nPROJECT NOTES",
        user_content="hi",
        context_quilt=True,
    )
    recall_text = "User prefers brevity. Met with Bob last Tuesday."
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": recall_text, "matched_entities": []},
    ):
        new_body, _ = await hook.before_llm(
            user=_user("pro"),
            body=body,
            tier=None,  # not consulted; feature_state is passed in directly
            feature_state="enabled",
            skip_teasers=set(),
        )

    assert new_body.metadata is not None
    assert new_body.metadata.get("cq_recall_block") == recall_text
    # And the recall text appears verbatim in system_prompt so the adapter
    # can locate it for the split.
    assert recall_text in new_body.system_prompt


@pytest.mark.asyncio
async def test_no_recall_metadata_when_recall_returns_empty_context():
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE INSTRUCTIONS\n\n{{context_quilt}}",
        user_content="hi",
        context_quilt=True,
    )
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "", "matched_entities": []},
    ):
        new_body, _ = await hook.before_llm(
            user=_user("pro"),
            body=body,
            tier=None,
            feature_state="enabled",
            skip_teasers=set(),
        )

    assert (new_body.metadata or {}).get("cq_recall_block") is None


@pytest.mark.asyncio
async def test_no_recall_metadata_for_teaser_state():
    """Plus tier (recall_only) runs recall for matched-entities metadata
    only — should not stash a cache block."""
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE INSTRUCTIONS",
        user_content="hi",
        context_quilt=True,
    )
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "ignored on teaser", "matched_entities": ["X"]},
    ):
        new_body, _ = await hook.before_llm(
            user=_user("plus"),
            body=body,
            tier=None,
            feature_state="teaser",
            skip_teasers=set(),
        )

    assert (new_body.metadata or {}).get("cq_recall_block") is None


# --- memory contract v1 keys (CQ working session 2026-07-15/16) ---

@pytest.mark.asyncio
async def test_project_chat_recall_carries_contract_keys():
    """memory_signals passes through from the client; token_budget 1200
    is GP-set on ProjectChat recalls."""
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE",
        user_content="what are the open commitments?",
        context_quilt=True,
        metadata={"prompt_mode": "ProjectChat", "project": "Kore",
                  "project_id": "proj-uuid-1", "memory_signals": True},
    )
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "ctx", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("pro"), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
    sent = recall.await_args.kwargs["metadata"]
    assert sent["memory_signals"] is True
    assert sent["token_budget"] == 1200
    assert sent["project_id"] == "proj-uuid-1"


@pytest.mark.asyncio
async def test_non_project_surfaces_keep_default_budget():
    """Meeting Chat (and anything else) keeps CQ's default budget, and
    memory_signals is absent unless the client sent it."""
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE",
        user_content="hi",
        context_quilt=True,
        metadata={"prompt_mode": "PostMeetingChat", "project": "Kore"},
    )
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "ctx", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("pro"), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
    sent = recall.await_args.kwargs["metadata"]
    assert "token_budget" not in sent
    assert "memory_signals" not in sent
