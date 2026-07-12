"""Document generation (phase 2a): gate + artifact collection.

Design: docs/design/documents-phase2-returned-files.md. The chat router arms
generation (ChatRequest.generation) when the gate passes; the anthropic
adapter attaches the sandbox + document skills; this module walks the final
response for generated file references, downloads them from the provider's
files surface, and stages them in GP's 6h fetch window.

Failure semantics: generation is best-effort — the text answer always
returns; collection errors log and yield an empty list, never an exception.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import aiosqlite
import httpx

from app.services import generated_files as staging

logger = logging.getLogger("ghostpour.document_generation")

_TIER_RANK = {"free": 0, "plus": 1, "pro": 2}

_GEN_DEFAULTS = {
    "enabled": False,
    "min_tier": "pro",
    "formats": [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ],
    "max_files_out": 2,
    "max_file_out_mb": 25,
}

# Confirmation envelope (handoff Part 1). While `enabled` is false the
# arming rule stays gate-based (the dark e2e lane); once true, generation
# arms ONLY on a confirmed resend and unconfirmed file intents get the
# offer envelope instead of a silent multi-minute turn.
_CONFIRMATION_DEFAULTS = {
    "enabled": False,
    "expected_seconds": 150,
    "poll_after_seconds": 5,
    "offer_text": ("That sounds like a file request. I can build {format} you "
                   "can download and share — it takes about two minutes — or "
                   "just answer right here in chat. Want the file?"),
    "offer_text_gist": ("Sounds like you want {format} {gist}. Building the "
                        "real file takes about two minutes — or I can just lay "
                        "it out right here in chat. Want the file?"),
    "format_nouns": {
        "xlsx": "a native Excel spreadsheet (.xlsx)",
        "docx": "a native Word document (.docx)",
        "pptx": "a native PowerPoint deck (.pptx)",
        "pdf": "a PDF file",
    },
}

# Chat surfaces where generation may arm. Non-streaming only (the router
# enforces that separately — ProjectChat is forced non-streaming already).
_GENERATION_SURFACES = {"ProjectChat", "PostMeetingChat"}

_FILES_BASE = "https://api.anthropic.com/v1/files"
_FILES_BETA = "files-api-2025-04-14"
_DOCX_OUT = ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document")


def load_generation_config(remote_configs: dict, locale: str | None = None) -> dict:
    """Generation config with nested confirmation merge. `locale` picks the
    localized client-config variant for served envelope text; the base
    config remains authoritative for every gate decision."""
    slug = "client-config"
    cfg_src = remote_configs.get(slug) or {}
    if locale and remote_configs.get(f"{slug}.{locale}"):
        cfg_src = remote_configs[f"{slug}.{locale}"]
    docs = cfg_src.get("documents") or {}
    gen = {**_GEN_DEFAULTS, **(docs.get("generation") or {})}
    gen["confirmation"] = {**_CONFIRMATION_DEFAULTS,
                           **((docs.get("generation") or {}).get("confirmation") or {})}
    return gen


_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
_CLASSIFIER_SYSTEM = (
    "You classify whether a chat message asks the assistant to CREATE a "
    "downloadable file (spreadsheet, document, presentation, or PDF). "
    "Asking a question ABOUT an attached file is NOT a file request; only "
    "requests to produce/build/export/write a file count. Reply with ONLY "
    'this JSON, no prose: {"file_request": true|false, '
    '"format": "xlsx"|"docx"|"pptx"|"pdf"|null, "gist": "..."} where format '
    "is your best guess of the desired output format (null when "
    "file_request is false) and gist is a short lowercase phrase IN THE "
    "LANGUAGE OF THE MESSAGE describing what the file is for, e.g. "
    '"for onboarding new people" ("" when file_request is false).'
)


# Recall-biased vocabulary prefilter: the Haiku classifier costs ~900ms on
# every gate-passing send, which post-flip is every Pro chat message. Only
# invoke it when the ask plausibly mentions making a file — the classifier
# stays the decider (this list over-triggers by design), and misses have
# the manual generate-as-file path. en/es/ja.
_FILE_ASK_HINTS = (
    "spreadsheet", "excel", "xlsx", "workbook", "word doc", "docx",
    "powerpoint", "pptx", "slide", "deck", "pdf", "file", "report",
    "chart", "gantt", "tracker", "export", "download", "document",
    "hoja de cálculo", "archivo", "documento", "informe", "presentación",
    "diapositiva", "gráfico",
    "スプレッドシート", "ファイル", "文書", "ドキュメント", "資料",
    "レポート", "エクセル", "ワード", "パワーポイント", "シート", "グラフ",
)


def looks_like_file_ask(text: str) -> bool:
    tail = (text or "")[-2000:].lower()
    return any(h in tail for h in _FILE_ASK_HINTS)


async def classify_generation_intent(provider_router, user_content: str,
                                     on_subcall=None) -> dict | None:
    """Cheap pre-flight intent check (handoff Part 1 step 1). Fail-open:
    ANY failure returns None and the turn proceeds as normal chat. The tail
    of user_content carries the actual question on context-bearing surfaces.
    on_subcall(request, response, elapsed_ms) meters the classifier call."""
    if not looks_like_file_ask(user_content):
        return None

    import time as _time

    from app.models.chat import ChatRequest
    request = ChatRequest(
        provider="anthropic",
        model=_CLASSIFIER_MODEL,
        system_prompt=_CLASSIFIER_SYSTEM,
        user_content=user_content[-2000:],
        max_tokens=50,
        temperature=0.0,
        call_type="generation_intent",
        prompt_mode="GenerationIntent",
    )
    start = _time.monotonic()
    try:
        response = await asyncio.wait_for(provider_router.route(request), timeout=10.0)
        elapsed_ms = int((_time.monotonic() - start) * 1000)
        if on_subcall is not None:
            await on_subcall(request, response, elapsed_ms)
        txt = response.text or ""
        parsed = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        if not isinstance(parsed.get("file_request"), bool):
            return None
        fmt = parsed.get("format")
        if fmt not in ("xlsx", "docx", "pptx", "pdf"):
            fmt = None
        gist = parsed.get("gist")
        gist = gist.strip() if isinstance(gist, str) else ""
        return {"file_request": parsed["file_request"], "format": fmt,
                "gist": gist[:120]}
    except Exception as e:
        logger.info("generation intent classifier failed open: %s", e)
        return None


_REPLY_MARKER = re.compile(r"(?:current|user)\s+question\s*:\s*", re.I)


def _isolate_reply(reply_text: str) -> str:
    """Pull the user's actual reply out of the assembled send. Clients
    re-inject attachment blocks into echo sends (by contract), so the raw
    user_content tail is mostly document text with the reply at the very
    end — feeding that to the judge made it fish template fragments out as
    "the reply" (first live case: judged Scott's bare "Yes" ambiguous while
    quoting 'y Red/Yellow?', a string from his attached template). Slice
    after the last question marker when present; plain tail otherwise."""
    matches = list(_REPLY_MARKER.finditer(reply_text or ""))
    if matches:
        return reply_text[matches[-1].end():][:1000]
    return (reply_text or "")[-1000:]


_INTERPRETER_SYSTEM = (
    "The assistant just offered to build a file for the user and the user "
    "replied. Decide whether the reply ACCEPTS the offer. Acceptance "
    "includes casual agreement (yes / go ahead / sure / do it, in any "
    "language) and agreement WITH a changed format or tweak (\"actually "
    "make it a spreadsheet\"). The reply may carry attached-document "
    "context; judge ONLY the user's own words, never text quoted from an "
    "attached document. A refusal, an unrelated question, "
    "anything ambiguous, or asking for the content INLINE instead — "
    "\"just show me here\", \"a table in chat is fine\" — is NOT acceptance. Reply with ONLY this JSON: "
    '{"confirm": true|false, "format": "xlsx"|"docx"|"pptx"|"pdf"|null} '
    "where format is the user's revised choice, or null to keep the "
    "offered format (always null when confirm is false)."
)


async def interpret_offer_reply(provider_router, offer: dict, reply_text: str,
                                on_subcall=None) -> dict:
    """Judge a chat reply against a live offer (handoff Part 1 v2).
    Fail-open: any failure is a non-confirm — the turn proceeds as normal
    chat and the user can simply ask again."""
    import time as _time

    from app.models.chat import ChatRequest
    request = ChatRequest(
        provider="anthropic",
        model=_CLASSIFIER_MODEL,
        system_prompt=_INTERPRETER_SYSTEM,
        user_content=(f"OFFER: a {offer['format']} file {offer.get('gist') or ''}\n"
                      f"USER REPLY: {_isolate_reply(reply_text)}"),
        max_tokens=50,
        temperature=0.0,
        call_type="generation_intent",
        prompt_mode="GenerationOfferReply",
    )
    start = _time.monotonic()
    try:
        response = await asyncio.wait_for(provider_router.route(request), timeout=10.0)
        elapsed_ms = int((_time.monotonic() - start) * 1000)
        if on_subcall is not None:
            await on_subcall(request, response, elapsed_ms)
        txt = response.text or ""
        parsed = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        confirm = parsed.get("confirm") is True
        fmt = parsed.get("format")
        if fmt not in ("xlsx", "docx", "pptx", "pdf"):
            fmt = None
        return {"confirm": confirm, "format": fmt or offer["format"]}
    except Exception as e:
        logger.info("offer reply interpreter failed open: %s", e)
        return {"confirm": False, "format": offer["format"]}


def build_offer_envelope(confirmation_cfg: dict, fmt: str | None,
                         gist: str = "", offer_id: str | None = None) -> dict:
    """The confirmation_required feature-state envelope (handoff Part 1
    step 2). `details` is add-only — cost_credits slots here if consumable
    credits ever ship."""
    fmt = fmt or "xlsx"
    noun = (confirmation_cfg.get("format_nouns") or {}).get(fmt, "a file")
    gist = (gist or "").strip()
    template = confirmation_cfg.get("offer_text_gist") if gist else None
    text = str(template or confirmation_cfg["offer_text"])
    text = text.replace("{format}", noun).replace("{gist}", gist)
    payload = {
        "feature_state": {
            "feature": "document_generation",
            "state": "confirmation_required",
            "cta": {
                "kind": "generation_offer",
                # rendered VERBATIM as an assistant chat message (SS design
                # revision 2026-07-12) and persisted in chat history
                "text": text,
                "action": "confirm_generation",
                "details": {
                    "expected_format": fmt,
                    "expected_seconds": int(confirmation_cfg["expected_seconds"]),
                    "gist": gist,
                },
            },
        },
    }
    if offer_id:
        payload["feature_state"]["cta"]["details"]["offer_id"] = offer_id
    return payload


def generation_gate(
    *,
    remote_configs: dict,
    tier_name: str,
    managed_routing: bool,
    provider: str,
    prompt_mode: str | None,
    user_identity: set[str] | None,
) -> bool:
    """Should this turn arm the generation tools? Mirrors the documents
    passthrough gate: allowed_users (shared with phase 1) overrides enabled
    AND tier for e2e; routing/provider/surface stay mechanical requirements."""
    if prompt_mode not in _GENERATION_SURFACES:
        return False
    if not managed_routing or provider != "anthropic":
        return False
    cfg = load_generation_config(remote_configs)
    docs = (remote_configs.get("client-config") or {}).get("documents") or {}
    listed = bool(user_identity and set(user_identity) & set(docs.get("allowed_users") or []))
    tier_ok = _TIER_RANK.get(tier_name, 0) >= _TIER_RANK.get(cfg["min_tier"], 2)
    return (bool(cfg["enabled"]) and tier_ok) or listed


def _walk_file_ids(raw_response_json: str) -> list[str]:
    """Generated-artifact file ids from the provider's final content blocks."""
    try:
        data = json.loads(raw_response_json)
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[str] = []
    for b in data.get("content", []):
        if not isinstance(b, dict) or not b.get("type", "").endswith("_tool_result"):
            continue
        c = b.get("content")
        items = c.get("content", []) if isinstance(c, dict) else (c if isinstance(c, list) else [])
        for it in items:
            if isinstance(it, dict) and it.get("file_id"):
                out.append(it["file_id"])
    # de-dup, preserve order
    seen: set[str] = set()
    return [f for f in out if not (f in seen or seen.add(f))]


async def collect_generated_files(
    db: aiosqlite.Connection,
    *,
    raw_response_json: str,
    api_key: str,
    remote_configs: dict,
    user_id: str,
    app_id: str | None,
) -> list[dict]:
    """Download generated artifacts from the provider and stage them.
    Best-effort: every failure logs and skips; never raises."""
    cfg = load_generation_config(remote_configs)
    file_ids = _walk_file_ids(raw_response_json)[: int(cfg["max_files_out"])]
    if not file_ids:
        return []

    max_bytes = int(cfg["max_file_out_mb"]) * 1024 * 1024
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": _FILES_BETA,
    }
    staged: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for fid in file_ids:
            try:
                meta_r = await client.get(f"{_FILES_BASE}/{fid}", headers=headers)
                if meta_r.status_code != 200:
                    logger.warning("generation: metadata %s -> %s", fid, meta_r.status_code)
                    continue
                meta = meta_r.json()
                name = meta.get("filename") or fid
                mime = meta.get("mime_type") or "application/octet-stream"
                if mime not in cfg["formats"]:
                    logger.info("generation: '%s' mime %s not in served formats — skipping", name, mime)
                    continue
                if int(meta.get("size_bytes") or 0) > max_bytes:
                    logger.info("generation: '%s' over max_file_out_mb — skipping", name)
                    continue
                content_r = await client.get(f"{_FILES_BASE}/{fid}/content", headers=headers)
                if content_r.status_code != 200 or len(content_r.content) > max_bytes:
                    logger.warning("generation: download %s -> %s", fid, content_r.status_code)
                    continue
                content = content_r.content
                if mime == _DOCX_OUT:
                    # Word-compat backstop (2026-07-11 field finding): rebuild
                    # sandbox-authored docx on a Word-derived template.
                    # Fail-open — a rebuild error keeps the original bytes.
                    from app.services.docx_rebuild import rebuild_docx
                    content = await asyncio.to_thread(rebuild_docx, content)
                row = await staging.stage(
                    db, user_id=user_id, app_id=app_id,
                    name=name, media_type=mime, content=content,
                )
                if row:
                    staged.append(row)
            except Exception as e:  # noqa: BLE001 — best-effort per file
                logger.warning("generation: collecting %s failed: %s", fid, e)
    if staged:
        logger.info("generation: staged %d artifact(s) for user %s: %s",
                    len(staged), user_id[:8], [s["name"] for s in staged])
    return staged
