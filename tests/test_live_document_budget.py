"""Live document budget, and extracted documents in a cached part.

A live in-meeting query (call_type query / query_follow_up) waits on its first
word while the model reads every page. Over _LIVE_MAX_TOKENS (estimated from
the page count) a PDF goes as extracted text instead. The decision depends on
the documents alone, so the same files take the same path on every tap and
the prompt cache holds. Typed chat outside a meeting keeps the native path.

Extracted text now rides in reference_text on Anthropic, which the adapter
caches, instead of glued to user_content, which carries the transcript and
was billed in full on every tap.
"""
import base64

import pytest

from app.models.chat import ChatRequest, DocumentAttachment
from app.services import documents
from app.services.documents import PDF_MIME, PPTX_MIME, process_documents
from app.services.providers.anthropic import AnthropicAdapter
from tests.test_documents_passthrough import _MIN_PDF, _min_pptx


def _doc(raw: bytes, media_type: str, name: str) -> DocumentAttachment:
    return DocumentAttachment(name=name, media_type=media_type,
                              data=base64.b64encode(raw).decode())


def _body(docs, call_type="query", provider="anthropic", user="[Speaker 2] How?") -> ChatRequest:
    return ChatRequest(provider=provider, model="claude-sonnet-5", system_prompt="sys",
                       user_content=user, documents=docs,
                       metadata={"call_type": call_type})


def _configs() -> dict:
    return {"client-config": {"documents": {"enabled": True, "min_tier": "plus"}}}


@pytest.fixture
def pages_cost(monkeypatch):
    """Each fixture PDF is one page; price a page so two fit badly."""
    monkeypatch.setattr(documents, "_TOKENS_PER_PDF_PAGE", 60_000)
    monkeypatch.setattr(documents, "_LIVE_MAX_TOKENS", 100_000)


async def _run(body):
    return await process_documents(body, remote_configs=_configs(),
                                   tier_name="pro", managed_routing=True)


@pytest.mark.asyncio
async def test_live_query_over_budget_extracts_the_second_pdf(pages_cost):
    out = await _run(_body([_doc(_MIN_PDF, PDF_MIME, "a.pdf"), _doc(_MIN_PDF, PDF_MIME, "b.pdf")]))
    assert [d.name for d in out.documents] == ["a.pdf"]
    assert '--- Attached: "b.pdf" ---' in out.reference_text
    assert out.user_content == "[Speaker 2] How?"
    assert out.get_meta("document_extracted") == 1
    assert out.get_meta("document_over_live_budget") == 1


@pytest.mark.asyncio
async def test_live_query_under_budget_keeps_both_native(monkeypatch):
    monkeypatch.setattr(documents, "_TOKENS_PER_PDF_PAGE", 40_000)
    monkeypatch.setattr(documents, "_LIVE_MAX_TOKENS", 100_000)
    out = await _run(_body([_doc(_MIN_PDF, PDF_MIME, "a.pdf"), _doc(_MIN_PDF, PDF_MIME, "b.pdf")]))
    assert [d.name for d in out.documents] == ["a.pdf", "b.pdf"]
    assert out.reference_text is None
    assert out.get_meta("document_over_live_budget") == 0


@pytest.mark.asyncio
async def test_typed_chat_outside_a_meeting_ignores_the_live_budget(pages_cost):
    out = await _run(_body([_doc(_MIN_PDF, PDF_MIME, "a.pdf"), _doc(_MIN_PDF, PDF_MIME, "b.pdf")],
                           call_type="meeting_chat"))
    assert [d.name for d in out.documents] == ["a.pdf", "b.pdf"]
    assert out.get_meta("document_over_live_budget") == 0


@pytest.mark.asyncio
async def test_same_documents_take_the_same_path_whatever_the_transcript(pages_cost):
    docs = [_doc(_MIN_PDF, PDF_MIME, "a.pdf"), _doc(_MIN_PDF, PDF_MIME, "b.pdf")]
    one = await _run(_body(docs, user="[Speaker 2] How?"))
    two = await _run(_body(docs, user="[Speaker 3] Why now, and what changed?"))
    assert [d.name for d in one.documents] == [d.name for d in two.documents]
    assert one.reference_text == two.reference_text


@pytest.mark.asyncio
async def test_extracted_text_lands_in_a_cached_part_ahead_of_the_transcript():
    out = await _run(_body([_doc(_min_pptx(), PPTX_MIME, "deck.pptx")]))
    adapter = AnthropicAdapter(api_key="t", base_url="https://api.anthropic.com/v1/messages",
                               auth_header="x-api-key", auth_prefix="")
    body, _ = adapter._build_body(out)
    content = body["messages"][0]["content"]
    assert "Go Live: 07/15" in content[0]["text"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[-1]["text"] == "[Speaker 2] How?"
    assert "cache_control" not in content[-1]


@pytest.mark.asyncio
async def test_extracted_text_goes_ahead_of_client_reference_text():
    body = _body([_doc(_min_pptx(), PPTX_MIME, "deck.pptx")])
    body = body.model_copy(update={"reference_text": "MEETING REFS"})
    out = await _run(body)
    assert out.reference_text.index("Go Live") < out.reference_text.index("MEETING REFS")


@pytest.mark.asyncio
async def test_non_anthropic_still_inlines_into_user_content():
    out = await process_documents(_body([_doc(_min_pptx(), PPTX_MIME, "deck.pptx")], provider="openai"),
                                  remote_configs=_configs(), tier_name="pro", managed_routing=True)
    assert out.reference_text is None
    assert "Go Live: 07/15" in out.user_content


@pytest.mark.asyncio
async def test_extracted_text_survives_the_openrouter_fallback():
    from app.services.anthropic_or_fallback import _or_request
    out = await _run(_body([_doc(_min_pptx(), PPTX_MIME, "deck.pptx")]))
    assert out.reference_text and "Go Live: 07/15" not in out.user_content
    fallback = await _or_request(out, "anthropic/claude-sonnet-5")
    assert fallback.reference_text is None
    assert "Go Live: 07/15" in fallback.user_content
    assert fallback.user_content.rstrip().endswith("[Speaker 2] How?")
