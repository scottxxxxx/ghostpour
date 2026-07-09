"""Documents passthrough + server-side extraction (#359 spec).

Project Chat lets a user attach a PDF or PPTX. When the request is
GP-routed (model=auto), the user's tier meets the config's min_tier, the
provider resolved to Anthropic, and the file is a PDF, the document rides
to the model as a native document block (vision sees charts and layout).
In every other case GP extracts text server-side from the same bytes and
inlines it under the identical framing the SS client uses for its own
extraction — one round trip, response indistinguishable from the client
extracted flow. A downgrade is never an error and never a client retry.

Hard errors are transport-level only, preventable at attach time via the
served caps: `document_too_large` (raw bytes over per_file_max_mb),
`too_many_documents` (over max_files), `document_unreadable` (bytes that
don't parse as the declared type).

Config lives in client-config's `documents` key — the same values the
client reads to drive its picker, so both sides enforce one set of
numbers. `enabled` gates the PASSTHROUGH path only: once this code is
deployed, extraction fallback always works for bytes that arrive (an
early client build must degrade to today's behavior, not break).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import zipfile

from fastapi import HTTPException

from app.models.chat import ChatRequest, DocumentAttachment

logger = logging.getLogger("ghostpour.documents")

PDF_MIME = "application/pdf"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
# docx is NOT in the launch accepted_types — extractor support ships ahead of
# the config flip so adding it to the served list later is config-only, and a
# stray docx that arrives early extracts properly instead of hitting the
# unsupported-format marker.
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_DEFAULTS = {
    "enabled": False,
    "min_tier": "pro",
    "accepted_types": [PDF_MIME, PPTX_MIME],
    "per_file_max_mb": 25,
    "max_files": 2,
    # Per-account enablement for e2e / canary: identities (user id or email)
    # here get the passthrough path even while `enabled` is false and
    # regardless of tier. Pairs with SS's client debug override that forces
    # their gate open. Test hook, not a product surface.
    "allowed_users": [],
}

_TIER_RANK = {"free": 0, "plus": 1, "pro": 2}

# Extraction guardrails (server policy, not wire contract): keep a
# pathological file from flooding the prompt or the event loop.
_MAX_EXTRACT_CHARS = 200_000
_MAX_PPTX_SLIDES = 300
_MAX_XML_PART_BYTES = 30 * 1024 * 1024  # zip-bomb guard per slide part


def load_documents_config(remote_configs: dict) -> dict:
    """The `documents` key from client-config, merged over defaults.
    Server enforcement reads the default-locale file only — the feature
    numbers are locale-independent."""
    cfg = (remote_configs.get("client-config") or {}).get("documents") or {}
    return {**_DEFAULTS, **cfg}


def _err(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _decode(doc: DocumentAttachment) -> bytes:
    try:
        return base64.b64decode(doc.data, validate=True)
    except Exception:
        raise _err("document_unreadable", f'Attachment "{doc.name}" is not valid base64.')


def _extract_pdf_text(raw: bytes, name: str) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        text = "\n".join(pages).strip()
    except Exception:
        raise _err("document_unreadable", f'Attachment "{name}" does not parse as PDF.')
    if not text:
        # Scanned / image-only PDF on the extraction path. Never an error
        # (spec: downgrade always succeeds) — tell the model what happened.
        # On the passthrough path the same file is read visually and works.
        return ("(This document has no extractable text — likely a scanned or "
                "image-only PDF. Its visual content is not available on this path.)")
    return text[:_MAX_EXTRACT_CHARS]


def _extract_pptx_text(raw: bytes, name: str) -> str:
    """Slide text straight from the OOXML — all <a:t> runs per slide, which
    includes table cell text. No python-pptx dependency; defusedxml guards
    the user-supplied XML."""
    from defusedxml import ElementTree as DET

    A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        slide_names = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)),
        )[:_MAX_PPTX_SLIDES]
        if not slide_names:
            raise ValueError("no slides")
        out = []
        for i, part in enumerate(slide_names, 1):
            info = zf.getinfo(part)
            if info.file_size > _MAX_XML_PART_BYTES:
                continue
            root = DET.fromstring(zf.read(part))
            runs = [el.text for el in root.iter(f"{A_NS}t") if el.text]
            out.append(f"## Slide {i}\n" + "\n".join(runs))
        text = "\n\n".join(out).strip()
    except HTTPException:
        raise
    except Exception:
        raise _err("document_unreadable", f'Attachment "{name}" does not parse as PPTX.')
    return text[:_MAX_EXTRACT_CHARS]


_FRAMING_PREAMBLE = (
    "The user attached the following reference document(s) to this request. "
    "Read them and use them as the question directs — when asked to produce "
    "output in an attached format, follow it exactly (structure, headings, "
    "and field order included); when asked to check or compare against an "
    "attached document, evaluate the discussion against it specifically."
)


def _frame(name: str, text: str) -> str:
    label = name or "attachment"
    return f'--- Attached: "{label}" ---\n{text}\n--- End of "{label}" ---'


def _extract_docx_text(raw: bytes, name: str) -> str:
    """Body text straight from the OOXML — <w:t> runs grouped per paragraph
    (<w:p>), which covers table cell text in document order. Same
    no-extra-dependency approach as the pptx extractor."""
    from defusedxml import ElementTree as DET

    W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        info = zf.getinfo("word/document.xml")
        if info.file_size > _MAX_XML_PART_BYTES:
            raise ValueError("document.xml too large")
        root = DET.fromstring(zf.read("word/document.xml"))
        paras = []
        for p in root.iter(f"{W_NS}p"):
            runs = [t.text for t in p.iter(f"{W_NS}t") if t.text]
            if runs:
                paras.append("".join(runs))
        text = "\n".join(paras).strip()
        if not text:
            raise ValueError("no text")
    except HTTPException:
        raise
    except Exception:
        raise _err("document_unreadable", f'Attachment "{name}" does not parse as DOCX.')
    return text[:_MAX_EXTRACT_CHARS]


def _extract_to_text(doc: DocumentAttachment, raw: bytes) -> str:
    if doc.media_type == PDF_MIME:
        return _extract_pdf_text(raw, doc.name)
    if doc.media_type == PPTX_MIME:
        return _extract_pptx_text(raw, doc.name)
    if doc.media_type == DOCX_MIME:
        return _extract_docx_text(raw, doc.name)
    # Unknown type on the extraction path: config pulled the format after
    # the client attached it. Best effort — refuse quietly with a marker
    # rather than failing the whole chat.
    return "(attachment format not supported; content unavailable)"


async def process_documents(
    body: ChatRequest,
    *,
    remote_configs: dict,
    tier_name: str,
    managed_routing: bool,
    user_identity: set[str] | None = None,
) -> ChatRequest:
    """Validate caps, then split each document between the passthrough path
    (kept on body.documents for the provider adapter to render) and the
    extraction path (inlined into user_content). Returns the updated body.
    """
    docs = body.documents or []
    if not docs:
        return body

    cfg = load_documents_config(remote_configs)
    max_files = int(cfg["max_files"])
    per_file_max = int(cfg["per_file_max_mb"]) * 1024 * 1024

    if len(docs) > max_files:
        raise _err("too_many_documents", f"Max {max_files} documents per request.")

    decoded: list[bytes] = []
    for doc in docs:
        raw = _decode(doc)
        if len(raw) > per_file_max:
            raise _err(
                "document_too_large",
                f'Attachment "{doc.name}" is {len(raw) // (1024 * 1024)}MB; '
                f'max is {cfg["per_file_max_mb"]}MB.',
            )
        decoded.append(raw)

    tier_ok = _TIER_RANK.get(tier_name, 0) >= _TIER_RANK.get(cfg["min_tier"], 2)
    # allowed_users overrides `enabled` AND the tier gate (e2e/canary hook);
    # routing + provider stay mechanical requirements either way.
    listed = bool(user_identity and set(user_identity) & set(cfg.get("allowed_users") or []))
    passthrough_allowed = (
        (bool(cfg["enabled"]) and tier_ok or listed)
        and managed_routing
        and body.provider == "anthropic"
    )

    keep: list[DocumentAttachment] = []
    extracted: list[str] = []
    for doc, raw in zip(docs, decoded):
        accepted = doc.media_type in cfg["accepted_types"]
        # v1 passthrough is PDF-only: PPTX has no native document block, so
        # its "interpretation" is the structured text extraction below.
        if passthrough_allowed and accepted and doc.media_type == PDF_MIME:
            keep.append(doc)
        else:
            text = await asyncio.to_thread(_extract_to_text, doc, raw)
            extracted.append(_frame(doc.name, text))

    user_content = body.user_content
    if extracted:
        blocks = "\n\n".join(extracted)
        user_content = f"{_FRAMING_PREAMBLE}\n\n{blocks}\n\n{user_content}"

    total_bytes = sum(len(r) for r in decoded)
    meta = dict(body.metadata or {})
    meta["document_count"] = len(docs)
    meta["document_bytes"] = total_bytes
    logger.info(
        "documents: %d file(s) %d bytes — passthrough=%d extracted=%d "
        "(tier=%s managed=%s provider=%s enabled=%s)",
        len(docs), total_bytes, len(keep), len(extracted),
        tier_name, managed_routing, body.provider, cfg["enabled"],
    )
    return body.model_copy(update={
        "documents": keep or None,
        "user_content": user_content,
        "metadata": meta,
    })


def flatten_documents_for_or(body: ChatRequest) -> ChatRequest:
    """OpenRouter fallback path: OR adapters don't render document blocks,
    so passthrough documents would silently vanish. Extract them to framed
    text instead so the fallback answer still sees the content."""
    if not body.documents:
        return body
    extracted = []
    for doc in body.documents:
        try:
            raw = base64.b64decode(doc.data, validate=True)
            extracted.append(_frame(doc.name, _extract_to_text(doc, raw)))
        except Exception:
            # Never let fallback flattening kill the retry.
            extracted.append(_frame(doc.name, "(content unavailable)"))
    blocks = "\n\n".join(extracted)
    return body.model_copy(update={
        "documents": None,
        "user_content": f"{_FRAMING_PREAMBLE}\n\n{blocks}\n\n{body.user_content}",
    })
