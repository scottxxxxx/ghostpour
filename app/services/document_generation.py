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
    "offer_text": ("This looks like a file request. Generate {format} from "
                   "this project? Takes about two minutes."),
    "format_nouns": {
        "xlsx": "a spreadsheet",
        "docx": "a Word document",
        "pptx": "a slide deck",
        "pdf": "a PDF",
    },
}

# Chat surfaces where generation may arm. Non-streaming only (the router
# enforces that separately — ProjectChat is forced non-streaming already).
_GENERATION_SURFACES = {"ProjectChat", "PostMeetingChat"}

_FILES_BASE = "https://api.anthropic.com/v1/files"
_FILES_BETA = "files-api-2025-04-14"


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
    '"format": "xlsx"|"docx"|"pptx"|"pdf"|null} where format is your best '
    "guess of the desired output format (null when file_request is false)."
)


async def classify_generation_intent(provider_router, user_content: str,
                                     on_subcall=None) -> dict | None:
    """Cheap pre-flight intent check (handoff Part 1 step 1). Fail-open:
    ANY failure returns None and the turn proceeds as normal chat. The tail
    of user_content carries the actual question on context-bearing surfaces.
    on_subcall(request, response, elapsed_ms) meters the classifier call."""
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
        return {"file_request": parsed["file_request"], "format": fmt}
    except Exception as e:
        logger.info("generation intent classifier failed open: %s", e)
        return None


def build_offer_envelope(confirmation_cfg: dict, fmt: str | None) -> dict:
    """The confirmation_required feature-state envelope (handoff Part 1
    step 2). `details` is add-only — cost_credits slots here if consumable
    credits ever ship."""
    fmt = fmt or "xlsx"
    noun = (confirmation_cfg.get("format_nouns") or {}).get(fmt, "a file")
    return {
        "feature_state": {
            "feature": "document_generation",
            "state": "confirmation_required",
            "cta": {
                "kind": "generation_offer",
                "text": str(confirmation_cfg["offer_text"]).replace("{format}", noun),
                "action": "confirm_generation",
                "details": {
                    "expected_format": fmt,
                    "expected_seconds": int(confirmation_cfg["expected_seconds"]),
                },
            },
        },
    }


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
                row = await staging.stage(
                    db, user_id=user_id, app_id=app_id,
                    name=name, media_type=mime, content=content_r.content,
                )
                if row:
                    staged.append(row)
            except Exception as e:  # noqa: BLE001 — best-effort per file
                logger.warning("generation: collecting %s failed: %s", fid, e)
    if staged:
        logger.info("generation: staged %d artifact(s) for user %s: %s",
                    len(staged), user_id[:8], [s["name"] for s in staged])
    return staged
