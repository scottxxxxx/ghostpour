"""Documents passthrough (#359): PDF/PPTX attachments on /v1/chat.

Passthrough (native document block) only when ALL hold: config enabled,
GP-routed (managed), provider anthropic, tier >= min_tier, media_type PDF.
Everything else downgrades to server-side extraction inlined into
user_content with the client's own attachment framing — one round trip,
never a client retry. Hard errors are transport-level only.
"""

import base64
import io
import zipfile

import pytest
from fastapi import HTTPException

from app.models.chat import ChatRequest, DocumentAttachment
from app.services.documents import (
    PDF_MIME,
    PPTX_MIME,
    flatten_documents_for_or,
    load_documents_config,
    process_documents,
)

# --- fixtures: real minimal files, so the extractors run on actual bytes ---

# A minimal but valid one-page PDF with a text object ("Hello ABM").
_MIN_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 44 >> stream
BT /F1 24 Tf 72 720 Td (Hello ABM) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f
trailer << /Size 6 /Root 1 0 R >>
startxref
0
%%EOF"""


def _min_pptx() -> bytes:
    """One-slide pptx with two text runs, built as a raw OOXML zip."""
    slide = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree>"
        "<p:sp><p:txBody><a:p><a:r><a:t>Go Live: 07/15</a:t></a:r></a:p>"
        "<a:p><a:r><a:t>Ticket #74647</a:t></a:r></a:p></p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:sld>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/slides/slide1.xml", slide)
    return buf.getvalue()


def _doc(raw: bytes, media_type: str, name: str = "deck.bin") -> DocumentAttachment:
    return DocumentAttachment(
        name=name, media_type=media_type, data=base64.b64encode(raw).decode()
    )


def _body(docs, provider="anthropic", question="Update this deck") -> ChatRequest:
    return ChatRequest(
        provider=provider, model="claude-sonnet-4-6",
        system_prompt="sys", user_content=question, documents=docs,
    )


def _configs(enabled=True, **over) -> dict:
    documents = {"enabled": enabled, **over}
    return {"client-config": {"documents": documents}}


# --- config resolution ---

def test_defaults_when_key_absent():
    cfg = load_documents_config({})
    assert cfg["enabled"] is False
    assert cfg["min_tier"] == "pro"
    assert cfg["per_file_max_mb"] == 25 and cfg["max_files"] == 2
    assert PDF_MIME in cfg["accepted_types"] and PPTX_MIME in cfg["accepted_types"]


def test_bundled_client_config_ships_disabled():
    import json
    for f in ("client-config.json", "client-config.es.json", "client-config.ja.json"):
        docs = json.load(open(f"config/remote/{f}"))["documents"]
        assert docs["enabled"] is False
        assert docs["min_tier"] == "pro"


# --- passthrough path ---

@pytest.mark.asyncio
async def test_pdf_passthrough_for_managed_pro():
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = await process_documents(
        body, remote_configs=_configs(), tier_name="pro", managed_routing=True
    )
    assert out.documents and len(out.documents) == 1  # rides to the adapter
    assert out.user_content == "Update this deck"     # nothing inlined
    assert out.get_meta("document_count") == 1
    assert out.get_meta("document_bytes") == len(_MIN_PDF)


@pytest.mark.asyncio
async def test_pptx_always_extracts_even_on_pro_path():
    body = _body([_doc(_min_pptx(), PPTX_MIME, "deck.pptx")])
    out = await process_documents(
        body, remote_configs=_configs(), tier_name="pro", managed_routing=True
    )
    assert out.documents is None
    assert '--- Attached: "deck.pptx" ---' in out.user_content
    assert "Go Live: 07/15" in out.user_content
    assert "Ticket #74647" in out.user_content
    assert out.user_content.rstrip().endswith("Update this deck")


# --- downgrade semantics (never an error, never a retry) ---

@pytest.mark.asyncio
@pytest.mark.parametrize("tier_name,managed,provider,enabled", [
    ("plus", True, "anthropic", True),    # tier below min_tier
    ("pro", False, "anthropic", True),    # user-pinned model (BYOK-style)
    ("pro", True, "openrouter", True),    # routed off anthropic
    ("pro", True, "anthropic", False),    # feature flag off
])
async def test_pdf_downgrades_to_extraction(tier_name, managed, provider, enabled):
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")], provider=provider)
    out = await process_documents(
        body, remote_configs=_configs(enabled=enabled),
        tier_name=tier_name, managed_routing=managed,
    )
    assert out.documents is None
    assert '--- Attached: "report.pdf" ---' in out.user_content
    assert "Hello ABM" in out.user_content  # PDF text layer actually extracted


@pytest.mark.asyncio
async def test_format_pulled_from_config_downgrades_quietly():
    # Config no longer accepts pptx (stale client attached one anyway):
    # extraction still runs — the request must not fail.
    body = _body([_doc(_min_pptx(), PPTX_MIME, "deck.pptx")])
    out = await process_documents(
        body, remote_configs=_configs(accepted_types=[PDF_MIME]),
        tier_name="pro", managed_routing=True,
    )
    assert out.documents is None
    assert "Go Live: 07/15" in out.user_content


# --- hard errors (attach-time preventable) ---

@pytest.mark.asyncio
async def test_document_too_large():
    big = b"x" * (2 * 1024 * 1024)
    body = _body([_doc(big, PDF_MIME, "huge.pdf")])
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            body, remote_configs=_configs(per_file_max_mb=1),
            tier_name="pro", managed_routing=True,
        )
    assert ei.value.detail["code"] == "document_too_large"


@pytest.mark.asyncio
async def test_too_many_documents():
    docs = [_doc(_MIN_PDF, PDF_MIME, f"{i}.pdf") for i in range(3)]
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            _body(docs), remote_configs=_configs(max_files=2),
            tier_name="pro", managed_routing=True,
        )
    assert ei.value.detail["code"] == "too_many_documents"


@pytest.mark.asyncio
@pytest.mark.parametrize("data,media_type", [
    ("!!!not-base64!!!", PDF_MIME),
    (base64.b64encode(b"garbage bytes").decode(), PPTX_MIME),
])
async def test_document_unreadable(data, media_type):
    doc = DocumentAttachment(name="bad.bin", media_type=media_type, data=data)
    # plus tier → extraction path, where the parse actually happens
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            _body([doc]), remote_configs=_configs(),
            tier_name="plus", managed_routing=True,
        )
    assert ei.value.detail["code"] == "document_unreadable"


# --- docx extraction (extractor ships ahead of the config flip) ---

def _min_docx() -> bytes:
    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Quarterly summary</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Budget: on track</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


def test_docx_not_in_launch_accepted_types():
    from app.services.documents import DOCX_MIME, load_documents_config

    assert DOCX_MIME not in load_documents_config({})["accepted_types"]


@pytest.mark.asyncio
async def test_stray_docx_extracts_properly_before_config_flip():
    from app.services.documents import DOCX_MIME

    body = _body([_doc(_min_docx(), DOCX_MIME, "notes.docx")])
    out = await process_documents(
        body, remote_configs=_configs(), tier_name="pro", managed_routing=True
    )
    assert out.documents is None  # not accepted → extraction path
    assert "Quarterly summary" in out.user_content
    assert "Budget: on track" in out.user_content  # table cell text survives


@pytest.mark.asyncio
async def test_docx_garbage_is_unreadable():
    from app.services.documents import DOCX_MIME

    doc = DocumentAttachment(
        name="bad.docx", media_type=DOCX_MIME,
        data=base64.b64encode(b"not a zip at all").decode(),
    )
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            _body([doc]), remote_configs=_configs(),
            tier_name="plus", managed_routing=True,
        )
    assert ei.value.detail["code"] == "document_unreadable"


# --- scanned PDFs (spec: in scope on passthrough; marker on extraction) ---

_SCANNED_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj
xref
0 4
0000000000 65535 f
trailer << /Size 4 /Root 1 0 R >>
startxref
0
%%EOF"""


@pytest.mark.asyncio
async def test_scanned_pdf_rides_passthrough_untouched():
    body = _body([_doc(_SCANNED_PDF, PDF_MIME, "scan.pdf")])
    out = await process_documents(
        body, remote_configs=_configs(), tier_name="pro", managed_routing=True
    )
    assert out.documents and len(out.documents) == 1  # vision path handles it


@pytest.mark.asyncio
async def test_scanned_pdf_extraction_succeeds_with_marker():
    body = _body([_doc(_SCANNED_PDF, PDF_MIME, "scan.pdf")])
    out = await process_documents(
        body, remote_configs=_configs(), tier_name="plus", managed_routing=True
    )
    assert out.documents is None
    assert "no extractable text" in out.user_content  # marker, not an error
    assert out.user_content.rstrip().endswith("Update this deck")


# --- adapter + fallback wiring ---

def test_anthropic_builder_renders_document_block():
    from app.services.providers.anthropic import AnthropicAdapter

    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    provider = AnthropicAdapter(
        api_key="test", base_url="https://example.invalid/v1/messages",
        auth_header="x-api-key", auth_prefix="",
    )
    api_body, _headers = provider._build_body(body)
    parts = api_body["messages"][0]["content"]
    doc_blocks = [p for p in parts if p["type"] == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["source"]["media_type"] == PDF_MIME
    assert doc_blocks[0]["title"] == "report.pdf"
    assert parts[-1] == {"type": "text", "text": "Update this deck"}


def test_or_fallback_flattens_documents_to_text():
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = flatten_documents_for_or(body)
    assert out.documents is None
    assert "Hello ABM" in out.user_content
    assert out.user_content.rstrip().endswith("Update this deck")


def test_or_retarget_strips_document_blocks():
    from app.services.anthropic_or_fallback import _or_request

    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = _or_request(body, "anthropic/claude-sonnet-4.6")
    assert out.provider == "openrouter"
    assert out.documents is None
    assert "Hello ABM" in out.user_content
