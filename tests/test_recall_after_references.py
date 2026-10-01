"""The CQ recall must sit AFTER cached references, never before them.

Anthropic's prompt cache matches a prefix in the order system, then messages.
The recall changes every turn; documents and reference text do not. With the
recall in the system prompt, every recall change threw away the document
cache: 10 of 23 of Scott's Help Me Respond taps on 2026-10-01 rewrote about
82k document tokens ($0.21 each instead of $0.02, 4.3 s to first word).

The property under test is the cache one itself: two turns that differ only
in their recall produce byte-identical requests up to and including the last
cached reference block. See _layout_for_references.
"""
import json

from app.models.chat import ChatRequest, DocumentAttachment
from app.services.features.context_quilt_hook import RECALL_USE_GUARD
from app.services.providers.anthropic import AnthropicAdapter

TEMPLATE = (
    "RULES\n\nHELP ME RESPOND\n\n{{context_quilt}}\n\n"
    " Never use em dashes."
)


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(
        api_key="test",
        base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key",
        auth_prefix="",
    )


def _request(recall: str | None, *, docs=True, reference_text=None,
             user="Recent transcript:\n[Speaker 2] How?") -> ChatRequest:
    system = TEMPLATE.replace(
        "{{context_quilt}}", f"{RECALL_USE_GUARD}\n{recall}" if recall else "")
    return ChatRequest(
        provider="anthropic",
        model="claude-sonnet-5",
        system_prompt=system,
        user_content=user,
        documents=[DocumentAttachment(name="deck.pdf", media_type="application/pdf",
                                      data="QQ==")] if docs else None,
        reference_text=reference_text,
        metadata={"cq_recall_block": recall} if recall else {},
    )


def _cached_prefix(body: dict) -> str:
    """Everything the cache matches through the LAST cache_control breakpoint."""
    parts = [("system", b) for b in body["system"]]
    parts += [("user", p) for p in body["messages"][0]["content"]]
    last = max(i for i, (_, p) in enumerate(parts) if "cache_control" in p)
    return json.dumps(parts[: last + 1], sort_keys=True)


def test_recall_change_leaves_document_prefix_byte_identical():
    a, _ = _adapter()._build_body(_request("Met Sandra on Tuesday."))
    b, _ = _adapter()._build_body(_request("Board wants the 31% number.",
                                           user="Recent transcript:\n[Speaker 2] Why?"))
    assert _cached_prefix(a) == _cached_prefix(b)


def test_recall_still_reaches_the_model_after_the_documents():
    recall = "Met Sandra on Tuesday."
    body, _ = _adapter()._build_body(_request(recall))
    content = body["messages"][0]["content"]
    kinds = [p["type"] for p in content]
    assert kinds == ["document", "text", "text"]
    assert content[1]["text"] == f"[CONTEXT FROM PREVIOUS MEETINGS]\n{recall}"
    assert "cache_control" not in content[1]
    assert content[2]["text"].startswith("Recent transcript")
    system = body["system"]
    assert len(system) == 1 and recall not in system[0]["text"]
    # The guard is static and stays cached in the system prompt.
    assert RECALL_USE_GUARD in system[0]["text"]
    assert system[0]["text"].endswith(" Never use em dashes.")


def test_reference_text_alone_also_moves_the_recall():
    a, _ = _adapter()._build_body(_request("one", docs=False, reference_text="REF"))
    b, _ = _adapter()._build_body(_request("two", docs=False, reference_text="REF"))
    assert _cached_prefix(a) == _cached_prefix(b)
    assert a["messages"][0]["content"][1]["text"].endswith("one")


def test_no_references_keeps_the_three_block_system_layout():
    recall = "Met Sandra on Tuesday."
    body, _ = _adapter()._build_body(_request(recall, docs=False))
    assert [b["text"] == recall for b in body["system"]] == [False, True, False]
    assert [p["type"] for p in body["messages"][0]["content"]] == ["text"]


def test_no_recall_with_documents_is_unchanged():
    body, _ = _adapter()._build_body(_request(None))
    assert len(body["system"]) == 1
    assert [p["type"] for p in body["messages"][0]["content"]] == ["document", "text"]


def test_hook_label_moves_with_the_block_instead_of_dangling():
    # The hook's no-placeholder path writes the label into the system itself.
    recall = "Met Sandra on Tuesday."
    request = ChatRequest(
        provider="anthropic", model="claude-sonnet-5",
        system_prompt=f"RULES\n\n{RECALL_USE_GUARD}\n[CONTEXT FROM PREVIOUS MEETINGS]\n{recall}",
        user_content="hi",
        documents=[DocumentAttachment(name="d.pdf", media_type="application/pdf", data="QQ==")],
        metadata={"cq_recall_block": recall},
    )
    body, _ = _adapter()._build_body(request)
    assert "[CONTEXT FROM PREVIOUS MEETINGS]" not in body["system"][0]["text"]
    assert body["messages"][0]["content"][1]["text"].count("[CONTEXT FROM PREVIOUS MEETINGS]") == 1
