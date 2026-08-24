"""Meeting translations engine (docs/wire-contracts/meeting-translations.md).

One engine, two client uses: the post-meeting toggle and the
share-import offer. The client sends segment GROUPS of {id, text} only;
labels and timestamps never ride this wire, so reassembly by id is the
client's and structure cannot be corrupted here by construction.

ENGINE_VERSION is the prompt recipe version (dossier:
docs/prompt-dossiers/translations.md). Bump it whenever the prompt
changes; the cache key carries it, so stored renditions never silently
change under a client.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger("ghostpour.translations")

ENGINE_VERSION = 1

# Server-controlled, never on the wire (client sends no model choice).
TRANSLATION_PROVIDER = "anthropic"
TRANSLATION_MODEL = "claude-haiku-4-5-20251001"

ARTIFACTS = ("transcript", "summary", "report")

# Transcript translation is FAITHFUL: no dash rule, the user's own words
# keep their punctuation (same carve-out as transcriptCleanup).
_PROMPT_BASE = (
    "You translate meeting content segments. Input is a JSON array of "
    "{id, text} objects in the source language. Return ONLY a JSON array "
    "of {id, text} objects: every id present exactly once, in the same "
    "order, each text translated to the target language. Translate "
    "faithfully: keep the register, fillers, and punctuation style of "
    "the speaker; never summarize, never omit, never add. Personal "
    "names, company names, and product names are never translated."
)
_PROMPT_PROSE_EXTRA = (
    " Never use em dashes or en dashes anywhere in your output, even "
    "when the source contains them; use a comma, colon, or parentheses "
    "instead."
)


def system_prompt(artifact: str) -> str:
    return _PROMPT_BASE if artifact == "transcript" else _PROMPT_BASE + _PROMPT_PROSE_EXTRA


def normalize_language(tag: Any) -> str | None:
    """BCP-47-ish sanity: 2-3 letter primary subtag, optional subtags."""
    if not isinstance(tag, str):
        return None
    tag = tag.strip()
    if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", tag):
        return tag
    return None


def cache_key(segments: list[dict], source: str, target: str) -> str:
    """sha256(canonical segment-group JSON) + source + target +
    engine_version — the contract's idempotency key. Canonicalisation
    matches the config-manifest convention (sorted keys, compact
    separators, ensure_ascii=False)."""
    canon = json.dumps(segments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"{h}:{source}:{target}:v{ENGINE_VERSION}"


async def cache_get(db: aiosqlite.Connection, key: str) -> list[dict] | None:
    row = await (await db.execute(
        "SELECT response_json FROM translations_cache WHERE cache_key = ?", (key,)
    )).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["response_json"])
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_put(
    db: aiosqlite.Connection, key: str, artifact: str,
    source: str, target: str, segments: list[dict],
) -> None:
    await db.execute(
        """INSERT OR REPLACE INTO translations_cache
           (cache_key, artifact, source_language, target_language,
            engine_version, response_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key, artifact, source, target, ENGINE_VERSION,
         json.dumps(segments, ensure_ascii=False),
         datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


async def purge_transcript_translations(db: aiosqlite.Connection, max_age_days: int = 30) -> int:
    """Retention: cached TRANSCRIPT translations inherit transcript
    retention (30-day purge, retroactive) — the cache never outlives its
    source. Summary and report renditions follow report retention and
    are not purged here."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    cur = await db.execute(
        "DELETE FROM translations_cache WHERE artifact = 'transcript' AND created_at < ?",
        (cutoff,),
    )
    await db.commit()
    if cur.rowcount:
        logger.info("translations_purge transcript rows=%d", cur.rowcount)
    return cur.rowcount


def parse_model_output(text: str, expected_ids: list[str]) -> list[dict] | None:
    """Server-side parse of the model's JSON. Fences and prose wrapping
    are stripped HERE — the wire to the client carries clean JSON only,
    which is why the contract forbids client-side fence stripping."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(out, list):
        return None
    cleaned = []
    for o in out:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) \
                or not isinstance(o.get("text"), str):
            return None
        cleaned.append({"id": o["id"], "text": o["text"]})
    if [o["id"] for o in cleaned] != expected_ids:
        return None
    return cleaned
