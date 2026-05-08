"""Tests for the Anthropic cache_control split around the CQ recall block.

When the Context Quilt feature hook stashes the recall text on
`metadata.cq_recall_block`, the Anthropic adapter slices `system_prompt`
into [prefix, recall, suffix] blocks with cache_control on prefix +
recall. CQ #89 made recall byte-stable across calls within a 5-min
window, so isolating it as its own breakpoint lets the base prefix
keep caching cross-turn even when recall content differs.

Falls back to the long-standing single-block layout when no recall
block is stashed, the recall text isn't found in system_prompt, or
the recall text is empty.
"""

from app.models.chat import ChatRequest
from app.services.providers.anthropic import AnthropicAdapter


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(
        api_key="test",
        base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key",
        auth_prefix="",
    )


def test_no_recall_block_yields_single_cache_block():
    request = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="base prompt",
        user_content="hello",
    )
    body, _ = _adapter()._build_body(request)
    assert len(body["system"]) == 1
    assert body["system"][0]["text"] == "base prompt"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_recall_block_in_middle_yields_three_blocks_two_cache_breakpoints():
    recall = "User prefers concise answers. Met with Bob last Tuesday."
    request = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt=f"BASE INSTRUCTIONS\n\n{recall}\n\nPROJECT NOTES",
        user_content="hello",
        metadata={"cq_recall_block": recall},
    )
    body, _ = _adapter()._build_body(request)
    assert len(body["system"]) == 3
    prefix, mid, suffix = body["system"]

    assert prefix["text"] == "BASE INSTRUCTIONS\n\n"
    assert prefix["cache_control"] == {"type": "ephemeral"}

    assert mid["text"] == recall
    assert mid["cache_control"] == {"type": "ephemeral"}

    assert suffix["text"] == "\n\nPROJECT NOTES"
    assert "cache_control" not in suffix


def test_recall_block_as_prefix_yields_two_blocks_one_breakpoint():
    """When the prepend path runs (no {{context_quilt}} placeholder), the
    hook puts recall first in system_prompt — there's no prefix to cache
    independently, so the layout collapses to [recall, suffix]."""
    recall = "[CONTEXT FROM PREVIOUS MEETINGS]\nUser likes brevity."
    suffix_text = "\n\nBASE INSTRUCTIONS"
    request = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt=recall + suffix_text,
        user_content="hello",
        metadata={"cq_recall_block": recall},
    )
    body, _ = _adapter()._build_body(request)
    assert len(body["system"]) == 2
    assert body["system"][0]["text"] == recall
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][1]["text"] == suffix_text
    assert "cache_control" not in body["system"][1]


def test_recall_block_not_found_falls_back_to_single_block():
    """Defensive: if a downstream mutation stripped the recall text from
    system_prompt, don't crash — fall back to the legacy single-block
    layout."""
    request = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE INSTRUCTIONS",
        user_content="hello",
        metadata={"cq_recall_block": "this text does not appear above"},
    )
    body, _ = _adapter()._build_body(request)
    assert len(body["system"]) == 1
    assert body["system"][0]["text"] == "BASE INSTRUCTIONS"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_empty_recall_block_falls_back_to_single_block():
    request = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE INSTRUCTIONS",
        user_content="hello",
        metadata={"cq_recall_block": ""},
    )
    body, _ = _adapter()._build_body(request)
    assert len(body["system"]) == 1
    assert body["system"][0]["text"] == "BASE INSTRUCTIONS"


def test_split_preserves_other_body_fields():
    """The system-block split shouldn't disturb tools/messages/thinking."""
    recall = "Recall content here."
    request = ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt=f"BASE\n{recall}\nMORE",
        user_content="hi",
        metadata={"cq_recall_block": recall, "search_enabled": True},
    )
    body, _ = _adapter()._build_body(request)
    assert "tools" in body
    assert body["messages"][0]["role"] == "user"
    assert len(body["system"]) == 3
