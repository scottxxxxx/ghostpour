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

# automation ranks with pro: the internal harness tier mirrors Pro
# everywhere else (entitlements, model routing), and a gate that
# silently disagreed would put a harness on a lane no paying user
# is on. admin stays unranked, as it always has.
_TIER_RANK = {"free": 0, "plus": 1, "pro": 2, "automation": 2}

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
                   "can download and share (takes about two minutes), or "
                   "just answer right here in chat. Want the file?"),
    "offer_text_gist": ("Sounds like you want {format} {gist}. Building the "
                        "real file takes about two minutes, or I can just lay "
                        "it out right here in chat. Want the file?"),
    "teaser_text": "Want this as a real downloadable file?",
    # Version question for an ambiguous plan/progress ask (Scott's ruling
    # 2026-08-11; final wording Scott-approved after two revision rounds):
    # users say "project plan" while expecting a Gantt, so the question
    # describes both builds in user terms before anything generates. Both
    # options are honestly framed as Excel workbooks from meeting data;
    # the differentiators are the refined share-ready format and receipts
    # traceability. Served config text (client-config bundle) overrides
    # this code default, so the wording is GP's to change with no build
    # anywhere.
    "lane_question_text": (
        "I can build your Excel file two ways.\n\n"
        "Our project status workbook uses a format we've refined for "
        "sharing status with a team: a Gantt style timeline, a progress "
        "curve of reported versus planned, the history of every due date "
        "that moved, and a receipts sheet showing the meeting line behind "
        "each number, so anyone can check where a value came from.\n\n"
        "A custom workbook built to exactly what you describe, in whatever "
        "shape fits your ask.\n\n"
        "Both come from your meeting notes. Which would you like: the "
        "status workbook, or custom?"),
    "format_nouns": {
        "xlsx": "a native Excel spreadsheet (.xlsx)",
        "docx": "a native Word document (.docx)",
        "pptx": "a native PowerPoint deck (.pptx)",
        "pdf": "a PDF file",
    },
}

# Below-tier upsell (Scott 2026-07-14): when the ONLY thing between a
# detected file ask and the generation gate is the subscription tier, a
# served line is prepended to the reply. {tier} resolves to the served
# min_tier's display name at request time — a future Pro->Plus move
# updates the line with zero code change. Dark default; the bundle flips
# it (plain text in an existing field, no new wire shape).
# Below-tier upsell copy. PLACEHOLDER pitch: the structure and the
# placeholders are ours, the words are Scott's to set from the dashboard.
#
# {tier}     display name of the served min_tier, resolved at request
#            time, so an availability move needs no code change
# {artifact} what they just asked for ("that risk register"), falling
#            back to a generic noun when the classifier did not resolve
#            one. "I could build you that risk register" is a different
#            sentence from "I could generate a file".
#
# Per feedback_no_limitation_framing_in_copy this states what the tier
# DOES. It is not an apology. Enabled by default (Scott 2026-08-15):
# shipping it off meant the detection work reached nobody.
_UPSELL_DEFAULTS = {
    "enabled": True,
    "text": ("I can put {artifact} together as a real downloadable file "
             "on {tier}, along with everything else {tier} includes."),
    "generic_artifact": "that",
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
    gen["upsell"] = {**_UPSELL_DEFAULTS,
                     **((docs.get("generation") or {}).get("upsell") or {})}
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


def _classifier_system() -> str:
    """Base classifier plus the artifact catalog.

    One call answers both questions. Measured 2026-08-15: asking which
    ARTIFACT they want costs $0.0005 and lifts recognition from 21% to
    98% on held-out phrasings, so there is no version of this worth
    splitting into a second request.
    """
    try:
        from app.services.artifact_routing import artifact_classifier_system
        catalog = artifact_classifier_system()
    except Exception:  # noqa: BLE001
        return _CLASSIFIER_SYSTEM
    return (
        _CLASSIFIER_SYSTEM
        + "\n\nAlso label WHICH document they want, using this catalog.\n\n"
        + catalog.split("catalog:\n\n", 1)[-1].split("\n\nReply with")[0]
        + '\n\nAdd two more keys to the SAME JSON object: "artifact": '
        '"<key>"|null and "artifact_confidence": "high"|"low". Use null '
        "when the document they want is not in the catalog, and low when "
        "two of them fit roughly equally."
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
    # Artifact SHAPE, not just file nouns. Measured 2026-08-15: file
    # nouns alone matched 5 of 216 real artifact asks. These add 97 more
    # and trip on none of a 22 turn ordinary-chat control.
    "table", "matrix", "grid", "breakdown", "rundown", "roster",
    "inventory", "list of", "a list", "the list", "register", "log of",
    "scorecard", "checklist", "summary of", "recap of", "side by side",
    "pull together", "pull out", "put together", "give me the",
    "give me a", "show me the", "make me", "build me", "draft me",
    "worksheet",
    "lista", "tabla", "cuadro", "resumen de", "desglose",
    "tableau", "liste", "récapitulatif",
    "一覧", "表", "内訳",
)


# Explicit file verbs are a GUARANTEED catch (SS contract, 2026-07-12,
# replacing their removed manual toggle): "make me a file", "generate a
# spreadsheet", "build the docx" must never depend on a sampled classifier.
# Deterministic pattern -> file_request True with the noun's format; the
# LLM classifier only handles everything softer.
# "put ... in/into a word document" was the live 2026-07-28 phrasing that
# missed this catch and fell to the classifier + a redundant confirm.
_EXPLICIT_VERBS = r"(?:make|generate|build|create|produce|export|put|turn|convert|save)"
_EXPLICIT_NOUNS = {
    "xlsx": r"(?:spreadsheet|excel|xlsx|workbook|hoja de c\u00e1lculo|\u30b9\u30d7\u30ec\u30c3\u30c9\u30b7\u30fc\u30c8|\u30a8\u30af\u30bb\u30eb)",
    "docx": r"(?:word doc\w*|docx|documento de word|\u30ef\u30fc\u30c9)",
    "pptx": r"(?:powerpoint|pptx|slide deck|deck|presentaci\u00f3n|\u30d1\u30ef\u30fc\u30dd\u30a4\u30f3\u30c8)",
    "pdf": r"(?:pdf)",
    None: r"(?:file|document|archivo|\u30d5\u30a1\u30a4\u30eb)",
}


_QUESTION_MARKER = re.compile(r"(?:current|user)\s+question\s*:\s*", re.I)


def _question_portion(text: str) -> str:
    """The user's actual question, not the attachment injection blocks.
    With conversation-scoped attachments (SS design, 2026-07-13) the
    reference text rides EVERY turn — intent checks judging the full tail
    would re-trigger classifiers and teasers on every follow-up because
    the document vocabulary is always present. Slice after the last
    question marker when the client assembly provides one; whole text
    otherwise."""
    m = list(_QUESTION_MARKER.finditer(text or ""))
    return text[m[-1].end():] if m else (text or "")


def explicit_file_ask(text: str) -> dict | None:
    """Deterministic catch for explicit generation asks. Returns an intent
    dict ({file_request, format, gist}) or None. A miss on an explicit
    phrase is a bug here, never a UX gap."""
    tail = _question_portion(text)[-4000:].lower()
    for fmt, noun in _EXPLICIT_NOUNS.items():
        if re.search(rf"{_EXPLICIT_VERBS}\b[^.!?\n]{{0,60}}?\b{noun}", tail):
            return {"file_request": True, "format": fmt, "gist": ""}
    return None


# Nouns that, on their own, name a format in the user's OWN words: the
# _EXPLICIT_NOUNS table minus the generic entry (a bare "file" or
# "document" names no format) and minus bare "deck", which is agenda
# vocabulary ("what's on deck for August") far more often than a format
# wish; pptx keeps its unambiguous nouns.
_STATED_NOUN_RES = {
    fmt: re.compile(rf"\b{noun}")
    for fmt, noun in _EXPLICIT_NOUNS.items() if fmt
}
_STATED_NOUN_RES["pptx"] = re.compile(
    r"\b(?:powerpoint|pptx|slide deck|presentación"
    r"|パワーポイント)")


def stated_format(text: str) -> str | None:
    """The format the user's own words named, or None when they named
    none. Distinct from the classifier's `format`, which is a best GUESS
    even when the message names no format at all. The distinction is
    load-bearing for the ambiguity veto (live 2026-08-13 00:40: "Can you
    create a project plan from these meetings?" names no format, the
    classifier guessed docx, and the guess vetoed the version question
    into a plain Word offer)."""
    tail = _question_portion(text)[-2000:].lower()
    for fmt, rx in _STATED_NOUN_RES.items():
        if rx.search(tail):
            return fmt
    return None


# The SS client injects "[N image(s) attached for visual context]" into
# user_content when a send carries photos. On a confirmed generation turn
# this marker WITHOUT actual image blocks means the model would be told
# about an image it cannot see — and it invents rather than fails
# (2026-07-19: "reproduce this Excel" produced a fabricated sales sheet
# with placeholder names).
_IMAGE_MARKER_RE = re.compile(r"\[\s*\d+\s+image\(s\)\s+attached", re.IGNORECASE)

# Steering for a disarmed image-guard turn: the honest answer instead of
# a blind build. Plain chat lane; no wire change, no client handling.
IMAGE_GUARD_STEERING = (
    "\n\nFILE BUILD NOTICE: The user confirmed building a file from an "
    "attached photo, but the photo did not arrive with this confirmation "
    "message. Do not build the file and do not invent or describe file "
    "contents. Tell the user plainly that the photo did not come through "
    "with the confirmation, and ask them to send one new message with the "
    "photo attached and the request repeated. That message will build the "
    "file from the real image."
)


def ask_references_images(text: str) -> bool:
    """True when the assembled ask claims attached images (client marker)."""
    return bool(_IMAGE_MARKER_RE.search(text or ""))


# At-cap steering for file-ish asks (Scott 2026-07-19: tell the user in
# chat rather than letting the lane go silently dormant). Factual, states
# what happens and when it resets; the model still answers inline.
GENERATION_CAP_STEERING = (
    "\n\nFILE GENERATION NOTICE: This account has used all of its included "
    "file generations for the current billing period. The allowance resets "
    "on {reset_date}. If the user is asking for a generated file, tell them "
    "this plainly, and provide the content inline in chat instead. Do not "
    "offer to build a file this period."
)


def tier_feature_block(remote_configs: dict, tier: str, feature: str) -> dict | None:
    """Return tiers.<tier>.feature_definitions.<feature> as a dict, or None.
    Generic reader for per-tier operational config GP dictates to clients
    (e.g. the images downscale/quality block). Numeric-only content, so the
    base tiers config is authoritative."""
    cfg = (remote_configs or {}).get("tiers") or {}
    block = (cfg.get("tiers", {}).get(tier, {})
             .get("feature_definitions", {}).get(feature))
    return block if isinstance(block, dict) else None


def generation_monthly_cap(remote_configs: dict, tier: str) -> int | None:
    """Quiet per-tier monthly generation count cap (2026-07-19).

    Read from the tiers remote config:
    tiers.<tier>.feature_definitions.generation.generations_per_month.
    Absent block or null value = uncapped. Deliberately QUIET — no CTA
    copy, no client counter (unlike search's "N of M" surface): at cap
    the generation lane goes dormant for the rest of the allocation
    period and file asks get the model's inline-chat alternative.
    Numeric-only, so the base config is authoritative; localized tiers
    files carry the same block to survive locale resolution."""
    cfg = (remote_configs or {}).get("tiers") or {}
    block = (cfg.get("tiers", {}).get(tier, {})
             .get("feature_definitions", {}).get("generation"))
    if not isinstance(block, dict):
        return None
    cap = block.get("generations_per_month")
    return int(cap) if cap is not None else None


# Creation verbs aimed at a plan-shaped deliverable (field case 2,
# 2026-08-12): "Can you create a project plan from these meetings?" carries
# no file noun at all, so the vocabulary prefilter never fired, the intent
# flow never ran, and the model self-offered a file menu outside the
# managed lanes. en/es; ja is verb-after-noun and matched separately.
_PLAN_ARTIFACT_VERBS = (
    r"(?:make|create|build|draft|prepare|produce|generate|assemble|"
    r"put together|hazme|haz|crea|arma|prepara|genera)")


def plan_artifact_ask(text: str) -> bool:
    """Creation-shaped plan ask with no file noun: the deliverable is
    plan-shaped even though no file vocabulary appears, so the managed
    intent flow must see it (the classifier still decides whether it is
    a file request; a no becomes the teaser, and either road leads to
    the lane question). A question ABOUT a plan has no creation verb
    aimed at it and stays a pure chat turn (Scott: a mention of the word
    plan must never grow a file question)."""
    from app.services.doc_templates import AMBIGUOUS_PLAN_HINTS
    tail = (text or "")[-2000:].lower()
    nouns = "|".join(re.escape(h) for h in AMBIGUOUS_PLAN_HINTS)
    if re.search(rf"\b{_PLAN_ARTIFACT_VERBS}\b[^.!?\n]{{0,60}}?(?:{nouns})",
                 tail):
        return True
    # ja puts the verb after the noun ("プロジェクト計画を作って")
    return bool(re.search(
        rf"(?:{nouns})[^.!?\n]{{0,20}}?(?:作成|作って|作る|まとめて)", tail))


_REQUEST_SHAPE = re.compile(
    r"\b(give me|show me|make me|build me|draft me|send me|get me|"
    r"put together|pull together|pull out|pull up|lay out|"
    r"write up|write me|create|generate|compile|assemble|produce|prepare|"
    r"can you (?:make|build|create|put|pull|draft|write|give|show|lay|"
    r"compile)|could you (?:make|build|create|put|pull|draft|write|give|"
    r"show|lay)|i need (?:a|the|an)|i want (?:a|the|an)|"
    r"i'?d like (?:a|the|an)|"
    r"dame|hazme|armar|prepara|fais[- ]moi|peux[- ]tu|"
    r"\u4f5c\u3063\u3066|\u307e\u3068\u3081\u3066)\b", re.I)


def looks_like_file_ask(text: str) -> bool:
    tail = (text or "")[-2000:].lower()
    return (any(h in tail for h in _FILE_ASK_HINTS)
            or bool(_REQUEST_SHAPE.search(tail))
            or plan_artifact_ask(tail))


async def classify_generation_intent(provider_router, user_content: str,
                                     on_subcall=None) -> dict | None:
    """Cheap pre-flight intent check (handoff Part 1 step 1). Fail-open:
    ANY failure returns None and the turn proceeds as normal chat. The tail
    of user_content carries the actual question on context-bearing surfaces.
    on_subcall(request, response, elapsed_ms) meters the classifier call."""
    user_content = _question_portion(user_content)
    if not looks_like_file_ask(user_content):
        return None

    import time as _time

    from app.models.chat import ChatRequest
    request = ChatRequest(
        provider="anthropic",
        model=_CLASSIFIER_MODEL,
        system_prompt=_classifier_system(),
        user_content=user_content[-2000:],
        # 150, not 50: the JSON carries a free-text gist — a long one hit
        # the 50 cap live (2026-07-13, finish_reason=max_tokens) and only
        # parsed by luck; truncation means fail-open, a silently lost offer.
        max_tokens=150,
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
        art = parsed.get("artifact")
        art = art if isinstance(art, str) and art else None
        conf = parsed.get("artifact_confidence")
        conf = conf if conf in ("high", "low") else "high"
        return {"file_request": parsed["file_request"], "format": fmt,
                "gist": gist[:120], "artifact": art,
                "artifact_confidence": conf}
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
    "anything ambiguous, or asking for the content INLINE instead "
    "(\"just show me here\", \"a table in chat is fine\") is NOT acceptance. Reply with ONLY this JSON: "
    '{"confirm": true|false, "format": "xlsx"|"docx"|"pptx"|"pdf"|null, '
    '"style": "simple"|"detailed"|null, '
    '"version": "workbook"|"custom"|null} '
    "where format is the user's revised choice, or null to keep the "
    "offered format (always null when confirm is false), style is set "
    "ONLY when the offer presented a simple and a detailed version and the "
    "user's own words chose one (\"detailed please\", \"the simple one\"); "
    "null otherwise, and version is set ONLY when the offer presented a "
    "project status workbook and a custom workbook and the user's own "
    "words chose one: words like \"status workbook\", \"workbook\", "
    "\"status\", \"your format\", \"recipe\", \"the structured one\", or "
    "\"the gantt one\" mean workbook, and words like \"custom\", "
    "\"ad hoc\", \"my version\", or \"just build what I described\" mean "
    "custom; null otherwise."
)


async def interpret_offer_reply(provider_router, offer: dict, reply_text: str,
                                verbatim: bool = False, on_subcall=None) -> dict:
    """Judge a chat reply against a live offer (handoff Part 1 v2).
    Fail-open: any failure is a non-confirm — the turn proceeds as normal
    chat and the user can simply ask again."""
    import time as _time

    from app.models.chat import ChatRequest
    _offer_line = f"OFFER: a {offer['format']} file {offer.get('gist') or ''}"
    if offer.get("lane_choice"):
        # Ambiguous plan ask (Scott's ruling 2026-08-11): the offer asked
        # which version the user wants, so the judge must know both were
        # on the table to read the reply's choice.
        _offer_line += (" (the offer presented two versions: the project "
                        "status workbook, or a custom workbook)")
    request = ChatRequest(
        provider="anthropic",
        model=_CLASSIFIER_MODEL,
        system_prompt=_INTERPRETER_SYSTEM,
        user_content=(f"{_offer_line}\n"
                      f"USER REPLY: {reply_text[:1000] if verbatim else _isolate_reply(reply_text)}"),
        # same headroom as the intent classifier: a truncated verdict here
        # silently drops a user's YES (fail-open reads as a normal turn).
        max_tokens=150,
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
        style = parsed.get("style")
        if style not in ("simple", "detailed"):
            style = None
        version = parsed.get("version")
        if version not in ("workbook", "custom"):
            version = None
        return {"confirm": confirm, "format": fmt or offer["format"],
                "style": style, "version": version}
    except Exception as e:
        logger.info("offer reply interpreter failed open: %s", e)
        return {"confirm": False, "format": offer["format"], "style": None,
                "version": None}


_GIST_QUALIFIER_PREFIXES = (
    "for ", "of ", "about ", "covering ", "showing ", "tracking ",
    "para ", "de ", "sobre ",  # es
)


def gist_composes(gist: str | None) -> str:
    """The classifier's gist is meant to be a qualifier ("for onboarding
    new people") that reads inside "you want {format} {gist}". Verb and
    noun phrases jam the sentence — live 2026-07-14 twice: "convert
    content to spreadsheet" in the template intercept, then "seinfeld
    personality assignments for meeting attendees" in the plain offer.
    Keep the gist only when it composes; return "" otherwise so callers
    fall back to their no-gist copy. Unprefixed languages (ja) drop the
    gist, which is the safe reading."""
    g = (gist or "").strip()
    return g if g.lower().startswith(_GIST_QUALIFIER_PREFIXES) else ""


def build_offer_envelope(confirmation_cfg: dict, fmt: str | None,
                         gist: str = "", offer_id: str | None = None) -> dict:
    """The confirmation_required feature-state envelope (handoff Part 1
    step 2). `details` is add-only — cost_credits slots here if consumable
    credits ever ship."""
    fmt = fmt or "xlsx"
    noun = (confirmation_cfg.get("format_nouns") or {}).get(fmt, "a file")
    gist = gist_composes(gist)
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


def build_lane_question_envelope(confirmation_cfg: dict, gist: str = "",
                                 offer_id: str | None = None) -> dict:
    """The version question for an ambiguous plan/progress file ask
    (Scott's ruling 2026-08-11). Same generation_offer envelope shape the
    client already renders verbatim as an assistant message, so the
    question ships as served text with no client build. The offer behind
    it stores the workbook default (users saying "project plan" expect
    the Gantt); a typed reply choosing custom overrides at arm time."""
    from app.services.doc_templates import TEMPLATES
    gist = gist_composes(gist)
    text = str(confirmation_cfg.get("lane_question_text")
               or _CONFIRMATION_DEFAULTS["lane_question_text"])
    # The approved copy carries no {gist} slot (it names the source,
    # "your meeting notes", instead); the replace stays for served
    # overrides that choose to compose one in.
    text = text.replace("{gist}", (" " + gist) if gist else "")
    payload = {
        "feature_state": {
            "feature": "document_generation",
            "state": "confirmation_required",
            "cta": {
                "kind": "generation_offer",
                "text": text,
                "action": "confirm_generation",
                "details": {
                    "expected_format": "xlsx",
                    "expected_seconds": int(
                        TEMPLATES["gantt_detailed"]["expected_seconds"]),
                    "gist": gist,
                    "template_id": "gantt_detailed",
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


def upsell_line(upsell_cfg: dict, tier_label: str,
                artifact: str | None = None) -> str:
    """Format the below-tier line. Never leaves a placeholder visible.

    An unresolved {artifact} is the common case, not the exception: the
    classifier returns null whenever the ask is for something outside
    our catalog, and a raw "{artifact}" in a user-facing sentence is
    worse than a vague one.
    """
    text = str(upsell_cfg.get("text") or "")
    if not text:
        return ""
    noun = str(upsell_cfg.get("generic_artifact") or "that")
    if artifact:
        try:
            from app.services.artifact_types import CONTRACTS
            c = CONTRACTS.get(artifact)
            if c:
                noun = (c.offer_noun or c.label).split("(")[0].strip()
        except Exception:  # noqa: BLE001
            pass
    return text.replace("{artifact}", noun).replace(
        "{tier}", tier_label).strip()


def generation_tier_shortfall(
    *,
    remote_configs: dict,
    tier_name: str,
    managed_routing: bool,
    provider: str,
    prompt_mode: str | None,
) -> str | None:
    """The served min_tier when this turn fails the generation gate ONLY
    on subscription tier: every mechanical requirement passes and the
    feature is enabled, but the user's plan sits below min_tier. None in
    every other case. Drives the below-tier upsell line; allowed_users is
    deliberately not consulted — a listed identity passes the real gate
    before this is ever reached."""
    if prompt_mode not in _GENERATION_SURFACES:
        return None
    if not managed_routing or provider != "anthropic":
        return None
    cfg = load_generation_config(remote_configs)
    if not cfg["enabled"]:
        return None
    if tier_name not in _TIER_RANK:
        # Unranked tiers (admin) fail the real gate's tier check today, but
        # upselling them would be wrong — only a KNOWN below-min tier is a
        # shortfall.
        return None
    if _TIER_RANK[tier_name] >= _TIER_RANK.get(cfg["min_tier"], 2):
        return None
    return str(cfg["min_tier"])


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
