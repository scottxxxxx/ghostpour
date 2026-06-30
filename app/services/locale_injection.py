"""Central language-output injection for managed calls.

Clients (Tech Rehearsal today) send a bare ISO `locale` on managed calls. When
it names a non-English language, GP appends ONE directive to the system prompt
so the model answers in the user's language. Done in a single place so the rule
can't drift as prompts migrate into GP configs.

The one directive is safe for every call type:
- Prose calls (e.g. tr_intake, tr_rewrite) get fully translated.
- Structured calls the client parses by exact key (tr_match_analysis,
  tr_brief_analysis, tr_debrief, and any other JSON output) keep their keys and
  structure in English while only the human-readable string VALUES translate —
  so the client's parsing never breaks.

English (`en` / `en-*`) and missing locales are a no-op: English is the default,
no instruction needed. See docs/handoffs/tr-managed-prompts-and-locale.md.
"""

from __future__ import annotations

# ISO 639-1 → English language name. Bare codes; a region suffix (es-MX) is
# resolved on its base code. Extend as locales are onboarded.
_LANGUAGE_NAMES = {
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "it": "Italian", "nl": "Dutch", "pl": "Polish", "sv": "Swedish",
    "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
    "id": "Indonesian", "vi": "Vietnamese", "th": "Thai",
}


def normalize_locale(locale: str | None) -> str | None:
    """Lowercase + trim a locale tag, normalizing `_` to `-`. None when blank."""
    if not locale:
        return None
    loc = locale.strip().lower().replace("_", "-")
    return loc or None


def _base_lang(loc: str) -> str:
    """The base language subtag (es-MX -> es)."""
    return loc.split("-", 1)[0]


def language_directive(locale: str | None) -> str | None:
    """The directive to append to a managed system prompt for `locale`, or None
    when no injection is needed (English or missing).

    Unknown non-English codes still inject, naming the ISO code so the model can
    act on it rather than silently defaulting to English.
    """
    loc = normalize_locale(locale)
    if loc is None:
        return None
    base = _base_lang(loc)
    if base == "en":
        return None
    name = _LANGUAGE_NAMES.get(base)
    target = f"{name} ({loc})" if name else f"the language with ISO code '{loc}'"
    lang = name or "that language"
    return (
        "\n\n--- RESPONSE LANGUAGE ---\n"
        f"Respond in {target}. Write all free-text the user will read (descriptions, "
        f"titles, sentences) in {lang}. If your response is JSON or other structured "
        "data, translate ONLY those free-text values. Keep every key and field name "
        "in English and the overall structure exactly as specified. Do NOT translate "
        "fixed enumerated token values — for example severity or level values like "
        '"high"/"medium"/"low" or "strong"/"ok"/"weak" — emit those in English exactly '
        "as the schema specifies. Any field the schema says must copy an input value "
        "verbatim (e.g. axis labels) must be copied byte-for-byte from the input, never "
        "translated. Do not translate, rename, add, remove, or reorder keys."
    )


def apply(system_prompt: str, locale: str | None) -> str:
    """Return `system_prompt` with the language directive appended when needed.
    A no-op (returns the prompt unchanged) for English or missing locales."""
    directive = language_directive(locale)
    if not directive:
        return system_prompt
    return (system_prompt or "") + directive
