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


def test_bundled_client_config_documents_live_and_pro_gated():
    # Flipped live 2026-07-11 after the e2e matrix closed. POLICY CHANGE
    # 2026-07-14 (Scott): the FLAT bundle now carries the permanent test
    # lane so a fresh deploy / DR restore seeds it (overlay-only meant a
    # restore silently lost it) and the provenance drift row stays clean.
    # Load-bearing invariants: pro gate intact, all three locales agree,
    # test-lane entries are OPAQUE UUIDS ONLY — the repo is public, so an
    # email in this list would publish PII (the gate matches id OR email;
    # always bake the id). Locale variants stay empty (enforcement reads
    # the flat file only).
    import json
    import re
    uuid_re = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    for f in ("client-config.json", "client-config.es.json", "client-config.ja.json"):
        docs = json.load(open(f"config/remote/{f}"))["documents"]
        assert docs["enabled"] is True
        assert docs["min_tier"] == "pro"
    flat = json.load(open("config/remote/client-config.json"))["documents"]
    assert flat["allowed_users"], "flat bundle carries the permanent test lane"
    for entry in flat["allowed_users"]:
        assert uuid_re.match(entry), f"non-UUID in public bundle: {entry!r}"
    for f in ("client-config.es.json", "client-config.ja.json"):
        assert json.load(open(f"config/remote/{f}"))["documents"]["allowed_users"] == []


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


@pytest.mark.asyncio
async def test_allowed_users_get_passthrough_while_dark():
    # e2e/canary hook: a listed identity rides passthrough even with
    # enabled:false AND a below-min tier; routing/provider stay required.
    cfgs = _configs(enabled=False, allowed_users=["ss-test@shouldersurf.com"])
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = await process_documents(
        body, remote_configs=cfgs, tier_name="plus", managed_routing=True,
        user_identity={"u-123", "ss-test@shouldersurf.com"},
    )
    assert out.documents and len(out.documents) == 1

    # unlisted identity: dark server still extracts (never ignores)
    out2 = await process_documents(
        _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")]),
        remote_configs=cfgs, tier_name="pro", managed_routing=True,
        user_identity={"someone-else@x.com"},
    )
    assert out2.documents is None
    assert "Hello ABM" in out2.user_content

    # listed but user-pinned model: mechanics still win — extraction
    out3 = await process_documents(
        _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")]),
        remote_configs=cfgs, tier_name="pro", managed_routing=False,
        user_identity={"ss-test@shouldersurf.com"},
    )
    assert out3.documents is None


# --- provider ceilings (size budget + page cap → downgrade, never error) ---

@pytest.mark.asyncio
async def test_oversized_passthrough_downgrades_to_extraction():
    # Two docs that each fit the wire cap but together exceed the served
    # passthrough budget: the first rides, the second downgrades. The budget
    # comes from the documents.passthrough config key (client pre-checks the
    # same numbers), floored to 1MB here via a tiny max_total_mb... a 480-byte
    # fixture can't exceed 1MB, so pad the second doc instead.
    # >1MB raw, still a cheap parse: comment padding sits after the header,
    # so the EOF/xref scan at the tail stays tiny (trailing junk instead sends
    # pypdf into a pathological backwards line-scan).
    head, rest = _MIN_PDF.split(b"\n", 1)
    big = head + b"\n" + (b"% pad\n" * 180_000) + rest
    cfgs = _configs(passthrough={"max_pdf_pages": 600, "max_total_mb": 1})
    body = _body([
        _doc(_MIN_PDF, PDF_MIME, "first.pdf"),
        _doc(big, PDF_MIME, "second.pdf"),
    ])
    out = await process_documents(
        body, remote_configs=cfgs, tier_name="pro", managed_routing=True
    )
    assert out.documents and len(out.documents) == 1
    assert out.documents[0].name == "first.pdf"
    assert '--- Attached: "second.pdf" ---' in out.user_content
    assert "Hello ABM" in out.user_content  # extracted, not dropped


@pytest.mark.asyncio
async def test_pdf_over_page_cap_downgrades():
    # Page cap served via config: max_pdf_pages 0 makes every PDF "too long".
    cfgs = _configs(passthrough={"max_pdf_pages": 0, "max_total_mb": 22})
    body = _body([_doc(_MIN_PDF, PDF_MIME, "long.pdf")])
    out = await process_documents(
        body, remote_configs=cfgs, tier_name="pro", managed_routing=True
    )
    assert out.documents is None
    assert "Hello ABM" in out.user_content  # extraction path, request succeeded


def test_passthrough_limits_served_in_bundled_config():
    import json
    for f in ("client-config.json", "client-config.es.json", "client-config.ja.json"):
        pt = json.load(open(f"config/remote/{f}"))["documents"]["passthrough"]
        assert pt["max_pdf_pages"] == 600
        assert pt["max_total_mb"] == 22
    # defaults match the bundle so an absent key behaves identically
    from app.services.documents import load_documents_config
    assert load_documents_config({})["passthrough"] == {"max_pdf_pages": 600, "max_total_mb": 22}


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
    # References resend the same bytes every send — the cache breakpoint on
    # the LAST document block makes repeat sends bill at cache-read rates.
    assert doc_blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert parts[-1] == {"type": "text", "text": "Update this deck"}


@pytest.mark.asyncio
async def test_or_fallback_flattens_documents_to_text():
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = await flatten_documents_for_or(body)
    assert out.documents is None
    assert "Hello ABM" in out.user_content
    assert out.user_content.rstrip().endswith("Update this deck")


@pytest.mark.asyncio
async def test_or_retarget_strips_document_blocks():
    from app.services.anthropic_or_fallback import _or_request

    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = await _or_request(body, "anthropic/claude-sonnet-4.6")
    assert out.provider == "openrouter"
    assert out.documents is None
    assert "Hello ABM" in out.user_content


# --- parse deadline (bounded executor) ---

def _slow(*_a, **_k):
    import time
    time.sleep(2)
    return "never returned in time"


def _fresh_executor(monkeypatch):
    """Each timeout test gets its own executor — a leaked _slow thread from a
    prior test would otherwise occupy the shared 2-worker pool and starve
    this test's submissions past the shrunken deadline."""
    from concurrent.futures import ThreadPoolExecutor
    import app.services.documents as docs_mod
    monkeypatch.setattr(docs_mod, "_EXTRACT_EXECUTOR",
                        ThreadPoolExecutor(max_workers=2, thread_name_prefix="doc-extract-test"))


@pytest.mark.asyncio
async def test_extraction_timeout_is_document_unreadable(monkeypatch):
    import app.services.documents as docs_mod
    _fresh_executor(monkeypatch)
    monkeypatch.setattr(docs_mod, "_EXTRACT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(docs_mod, "_extract_to_text", _slow)
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")], provider="openrouter")
    with pytest.raises(HTTPException) as exc:
        await process_documents(
            body, remote_configs=_configs(), tier_name="pro", managed_routing=True,
        )
    assert exc.value.detail["code"] == "document_unreadable"
    assert "too long" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_page_count_timeout_downgrades_to_extraction(monkeypatch):
    import app.services.documents as docs_mod
    _fresh_executor(monkeypatch)
    monkeypatch.setattr(docs_mod, "_EXTRACT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(docs_mod, "_pdf_page_count", _slow)
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = await process_documents(
        body, remote_configs=_configs(), tier_name="pro", managed_routing=True,
    )
    # passthrough-eligible, but the unvettable PDF downgrades to extraction
    assert out.documents is None
    assert "Hello ABM" in out.user_content


@pytest.mark.asyncio
async def test_flatten_timeout_degrades_to_marker_not_error(monkeypatch):
    import app.services.documents as docs_mod
    _fresh_executor(monkeypatch)
    monkeypatch.setattr(docs_mod, "_EXTRACT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(docs_mod, "_extract_to_text", _slow)
    body = _body([_doc(_MIN_PDF, PDF_MIME, "report.pdf")])
    out = await flatten_documents_for_or(body)
    assert out.documents is None
    assert "(content unavailable)" in out.user_content
    assert out.user_content.rstrip().endswith("Update this deck")


@pytest.mark.asyncio
async def test_error_details_are_typed_fields():
    """Part 5 contract: interpolated values ride details as typed fields."""
    big = b"x" * (2 * 1024 * 1024)
    body = _body([_doc(big, PDF_MIME, "huge.pdf")])
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            body, remote_configs=_configs(per_file_max_mb=1),
            tier_name="pro", managed_routing=True)
    d = ei.value.detail
    assert d["code"] == "document_too_large"
    assert d["details"] == {"file": "huge.pdf", "size_mb": 2, "max_mb": 1}

    docs = [_doc(_MIN_PDF, PDF_MIME, f"{i}.pdf") for i in range(3)]
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            _body(docs), remote_configs=_configs(max_files=2),
            tier_name="pro", managed_routing=True)
    assert ei.value.detail["details"] == {"max_files": 2}

    bad = DocumentAttachment(name="bad.bin", media_type=PDF_MIME, data="!!!")
    with pytest.raises(HTTPException) as ei:
        await process_documents(
            _body([bad]), remote_configs=_configs(),
            tier_name="plus", managed_routing=True)
    assert ei.value.detail["details"] == {"file": "bad.bin"}


@pytest.mark.asyncio
async def test_xlsx_extracts_as_structured_sheets():
    import io as _io
    import openpyxl
    from app.services.documents import XLSX_MIME
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Tasks"
    ws.append(["Task", "Owner", "Status"])
    ws.append(["Fix 401", "Chirag", "Blocked"])
    ws2 = wb.create_sheet("Budget")
    ws2.append(["Item", "Cost"]); ws2.append(["Proxy", 1200])
    buf = _io.BytesIO(); wb.save(buf)
    body = _body([_doc(buf.getvalue(), XLSX_MIME, "plan.xlsx")])
    out = await process_documents(body, remote_configs=_configs(),
                                  tier_name="pro", managed_routing=True)
    assert out.documents is None                       # extraction lane, never passthrough
    assert "=== Sheet: Tasks ===" in out.user_content
    assert "Fix 401,Chirag,Blocked" in out.user_content
    assert "=== Sheet: Budget ===" in out.user_content
    assert "Proxy,1200" in out.user_content


@pytest.mark.asyncio
async def test_xlsx_garbage_is_unreadable_with_details():
    from app.services.documents import XLSX_MIME
    bad = _doc(b"not a zip", XLSX_MIME, "bad.xlsx")
    with pytest.raises(HTTPException) as ei:
        await process_documents(_body([bad]), remote_configs=_configs(),
                                tier_name="pro", managed_routing=True)
    assert ei.value.detail["code"] == "document_unreadable"
    assert ei.value.detail["details"] == {"file": "bad.xlsx"}


# --- reference_text (Part 6: conversation-scoped reference caching) ---

def _adapter():
    from app.services.providers.anthropic import AnthropicAdapter
    return AnthropicAdapter(api_key="t", base_url="https://x.invalid/v1/messages",
                            auth_header="x-api-key", auth_prefix="")


def test_reference_text_renders_as_cached_part():
    body = ChatRequest(provider="anthropic", model="claude-sonnet-4-6",
                       system_prompt="sys", user_content="make the bars blue",
                       reference_text='--- Attached: "plan.xlsx" ---\nrows...')
    api_body, _ = _adapter()._build_body(body)
    parts = api_body["messages"][0]["content"]
    assert parts[0]["text"].startswith("--- Attached")
    assert parts[0]["cache_control"] == {"type": "ephemeral"}   # the spare 4th breakpoint
    assert parts[-1] == {"type": "text", "text": "make the bars blue"}


def test_reference_part_yields_on_generation_turns():
    body = ChatRequest(provider="anthropic", model="claude-sonnet-4-6",
                       system_prompt="sys", user_content="yes build it",
                       generation=True, reference_text="ref block")
    api_body, _ = _adapter()._build_body(body)
    parts = api_body["messages"][0]["content"]
    ref = next(p for p in parts if p.get("text") == "ref block")
    assert "cache_control" not in ref                            # yields (Part 6 rule)
    assert parts[-1]["cache_control"] == {"type": "ephemeral"}   # generation breakpoint wins


@pytest.mark.asyncio
async def test_or_fallback_folds_reference_text():
    from app.services.anthropic_or_fallback import _or_request
    body = ChatRequest(provider="anthropic", model="claude-sonnet-4-6",
                       system_prompt="s", user_content="question",
                       reference_text="REF BLOCK")
    out = await _or_request(body, "anthropic/claude-sonnet-4.6")
    assert out.reference_text is None
    assert out.user_content.startswith("REF BLOCK")
    assert out.user_content.endswith("question")
