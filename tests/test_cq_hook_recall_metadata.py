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


# --- rundown routing (Context Flow Contract v1, item 3) ---

_DOSSIER = {
    "user_id": "u1",
    "facts": [{"patch_id": "f1", "fact": "Scott prefers async updates",
               "patch_type": "note", "created_at": "2026-06-01T10:00:00Z"}],
    "action_items": [],
    "meetings": [
        {"origin_id": "m2", "origin_type": "meeting", "patches": [
            {"patch_id": "p3", "fact": "Bonus tied to ARR; quota missed",
             "patch_type": "blocker", "created_at": "2026-07-14T16:00:00Z"},
            {"patch_id": "p4", "fact": "Review HubSpot pipeline",
             "patch_type": "todo", "owner": "Scott Guida",
             "deadline_date": "2026-07-20",
             "created_at": "2026-07-14T16:00:00Z"}]},
        {"origin_id": "m1", "origin_type": "meeting", "patches": [
            {"patch_id": "p1", "fact": "2 in a Box model adopted",
             "patch_type": "decided", "created_at": "2026-06-22T15:00:00Z"}]},
    ],
    "server_time": "2026-07-16T01:02:03Z",
}


def test_format_dossier_shape():
    from app.services.context_quilt import format_dossier
    block = format_dossier(_DOSSIER)
    assert block.startswith("[PROJECT MEMORY DOSSIER — complete stored memory: "
                            "4 patches across 2 meetings]")
    assert "## Meeting 1 of 2 — 2026-07-14" in block
    assert "[todo] Review HubSpot pipeline (owner: Scott Guida) (deadline: 2026-07-20)" in block
    assert "## Not tied to a specific meeting" in block
    assert "Scott prefers async updates" in block
    # byte-stability: server_time never rendered, no truncation note under cap
    assert "2026-07-16T01:02:03" not in block
    assert "capped" not in block


@pytest.mark.asyncio
async def test_rundown_ask_routes_to_dossier_not_recall():
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="BASE {{context_quilt}}",
        user_content="Current question: using memory, give me everything "
                     "you have about this project",
        context_quilt=True,
        metadata={"prompt_mode": "ProjectChat", "project": "Kore",
                  "project_id": "proj-uuid-1"},
    )
    with patch("app.services.features.context_quilt_hook.cq.recall",
               new_callable=AsyncMock) as recall, \
         patch("app.services.features.context_quilt_hook.cq.quilt_dossier",
               new_callable=AsyncMock, return_value=_DOSSIER) as dossier:
        new_body, result = await hook.before_llm(
            user=_user("pro"), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
    dossier.assert_awaited_once()
    recall.assert_not_awaited()
    assert "PROJECT MEMORY DOSSIER" in new_body.system_prompt
    assert "{{context_quilt}}" not in new_body.system_prompt
    assert new_body.metadata["cq_recall_block"].startswith("[PROJECT MEMORY DOSSIER")
    assert result["cq_result"]["dossier"] is True


@pytest.mark.asyncio
async def test_rundown_falls_open_to_recall():
    """Contract: ANY miss or failure falls open to normal recall — no
    project_id, non-rundown phrasing, or a dead quilt endpoint."""
    hook = ContextQuiltHook()

    async def run(user_content, metadata, dossier_return):
        body = ChatRequest(
            provider="anthropic", model="claude-haiku-4-5-20251001",
            system_prompt="BASE", user_content=user_content,
            context_quilt=True, metadata=metadata)
        with patch("app.services.features.context_quilt_hook.cq.recall",
                   new_callable=AsyncMock,
                   return_value={"context": "", "matched_entities": []}) as recall, \
             patch("app.services.features.context_quilt_hook.cq.quilt_dossier",
                   new_callable=AsyncMock, return_value=dossier_return) as dossier:
            await hook.before_llm(user=_user("pro"), body=body, tier=None,
                                  feature_state="enabled", skip_teasers=set())
        return recall, dossier

    # non-rundown phrasing -> recall, quilt never called
    recall, dossier = await run(
        "what are the open commitments?",
        {"prompt_mode": "ProjectChat", "project_id": "proj-1"}, _DOSSIER)
    recall.assert_awaited_once(); dossier.assert_not_awaited()

    # rundown phrasing but no project_id -> recall
    recall, dossier = await run(
        "give me everything you have about this project",
        {"prompt_mode": "PostMeetingChat", "project": "Kore"}, _DOSSIER)
    recall.assert_awaited_once(); dossier.assert_not_awaited()

    # rundown + project_id but the quilt call failed -> recall fallback
    recall, dossier = await run(
        "give me everything you have about this project",
        {"prompt_mode": "ProjectChat", "project_id": "proj-1"}, None)
    dossier.assert_awaited_once(); recall.assert_awaited_once()


# --- correction lane (Context Flow Contract item 9, dark) ---

def _settings_stub(corrections=True):
    from unittest.mock import MagicMock
    s = MagicMock()
    s.cq_corrections_enabled = corrections
    s.cq_disable_you_suffix_sanitizer = False
    return s


def test_correction_detection_precision():
    from app.services.context_quilt import is_correction_ask
    assert is_correction_ask("Set the record straight, Robin owns that")
    assert is_correction_ask("correction: the deadline moved to August")
    assert is_correction_ask("update your memory, the bonus was approved")
    # precision: ordinary chat must never fire (false positive = junk patch)
    assert not is_correction_ask("what's wrong with the deployment?")
    assert not is_correction_ask("is that record from the June meeting?")
    assert not is_correction_ask("summarize the meeting")


@pytest.mark.asyncio
async def test_correction_fires_capture_with_block_and_steers():
    import asyncio
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="BASE {{context_quilt}}",
        user_content="Previous conversation: stuff. Current question: "
                     "Set the record straight, Robin owns the CBE fix, not Cindy",
        context_quilt=True,
        metadata={"prompt_mode": "ProjectChat", "project": "Kore",
                  "project_id": "proj-1"},
    )
    recall_text = "[todo] CBE fix [owner: Cindy]"
    with patch("app.services.features.context_quilt_hook.get_settings",
               return_value=_settings_stub(True)), \
         patch("app.services.features.context_quilt_hook.cq.recall",
               new_callable=AsyncMock,
               return_value={"context": recall_text, "matched_entities": []}), \
         patch("app.services.features.context_quilt_hook.cq.capture",
               new_callable=AsyncMock) as capture:
        new_body, _ = await hook.before_llm(
            user=_user("pro"), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
        await asyncio.sleep(0)   # let the fire-and-forget task run
    capture.assert_awaited_once()
    kw = capture.await_args.kwargs
    assert kw["interaction_type"] == "correction"
    # user's words only, question portion only, never the model response
    assert kw["content"].startswith("Set the record straight")
    assert "Previous conversation" not in kw["content"]
    assert "response" not in kw or kw.get("response") is None
    # the freshly injected block rides as the candidate set
    assert kw["context_block"] == recall_text
    assert kw["project_id"] == "proj-1"
    # honest acknowledgment steering: updating, never updated
    assert "MEMORY CORRECTION" in new_body.system_prompt
    assert "never that it is already" in new_body.system_prompt
    # normal recall still ran and injected
    assert recall_text in new_body.system_prompt


@pytest.mark.asyncio
async def test_correction_lane_dark_by_default():
    import asyncio
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        system_prompt="BASE",
        user_content="Set the record straight, Robin owns that",
        context_quilt=True,
        metadata={"prompt_mode": "ProjectChat", "project_id": "proj-1"},
    )
    with patch("app.services.features.context_quilt_hook.get_settings",
               return_value=_settings_stub(False)), \
         patch("app.services.features.context_quilt_hook.cq.recall",
               new_callable=AsyncMock,
               return_value={"context": "", "matched_entities": []}), \
         patch("app.services.features.context_quilt_hook.cq.capture",
               new_callable=AsyncMock) as capture:
        new_body, _ = await hook.before_llm(
            user=_user("pro"), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
        await asyncio.sleep(0)
    capture.assert_not_awaited()
    assert "MEMORY CORRECTION" not in new_body.system_prompt
