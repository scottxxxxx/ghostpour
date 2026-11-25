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

A MISSING locale is a no-op. English is NOT (2026-09-10, Scott's ruling for SS's
translation redesign): the phone's language wins in every case. The served
summary/analysis recipes say "write in the language the participants speak",
so without a directive an English phone with a Spanish meeting got a Spanish
summary. `en` now appends "Respond in English" like every other language and
output_language echoes "en"; null is reserved for a request with no locale at all. See docs/handoffs/tr-managed-prompts-and-locale.md.
"""

from __future__ import annotations

# ISO 639-1 → English language name. Bare codes; a region suffix (es-MX) is
# resolved on its base code. Extend as locales are onboarded.
_LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
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


# Calls where the user asks something (typed in a box, or a preset action
# tapped in that box). Scott, 2026-11-25: "the query should come back in the
# language that the user typed the query in", for all queries. A Spanish
# phone in an English class got Spanish from Help Me Respond (11-23), words
# the student had to say aloud in English.
QUERY_CALL_TYPES = frozenset({
    "query", "query_follow_up", "meeting_chat", "meeting_chat_follow_up",
    "project_chat", "project_chat_follow_up", "tr_query",
})


def _typed_language_rule(target: str) -> str:
    return (
        "Answer in the language of the question or message the user typed. "
        "Only the user's own typed words decide this. Instructions, preset "
        "action text, attachments, transcripts, summaries and memory sent "
        "with the request do not, whatever language they are in. If the user "
        "typed nothing of their own (they tapped a preset action), then when "
        "your answer is words for the user to say aloud in the conversation, "
        "write it in the language that conversation is being held in, and "
        f"otherwise respond in {target}. "
    )


def language_directive(locale: str | None, call_type: str | None = None) -> str | None:
    """The directive to append to a managed system prompt for `locale`, or None
    when the request carried no locale at all. English injects too (see the
    module docstring): the phone's language wins over the transcript's.

    Unknown non-English codes still inject, naming the ISO code so the model can
    act on it rather than silently defaulting to English.
    """
    loc = normalize_locale(locale)
    if loc is None:
        return None
    base = _base_lang(loc)
    name = _LANGUAGE_NAMES.get(base)
    target = f"{name} ({loc})" if name else f"the language with ISO code '{loc}'"
    lang = name or "that language"
    if call_type in QUERY_CALL_TYPES:
        lead = (_typed_language_rule(target)
                + "This overrides every other language instruction in this prompt. ")
        lang = "that language"
    else:
        lead = (
            f"Respond in {target}. This overrides every other language instruction "
            "in this prompt, including any that says to write in the language of "
            "the transcript or of the participants. The material you are given may "
            "be entirely in another language, and that does not change the language "
            "you write in. ")
    return (
        "\n\n--- RESPONSE LANGUAGE ---\n"
        + lead +
        # 2026-10-26 prod review: a custom prompt saying "always answer in
        # English, this is my English oral exam" lost to a Spanish phone, and
        # three more "NEVER English" lines did not help. A language the user
        # names for the reply is a user instruction, not the transcript's.
        "One exception: when the user's own instructions or typed message name "
        "a language for the reply (for example \"always answer in English\"), "
        "use that language. "
        f"Write all free-text the user will read (descriptions, "
        f"titles, sentences) in {lang}. If your response is JSON or other structured "
        "data, translate ONLY those free-text values. Keep every key and field name "
        "in English and the overall structure exactly as specified. Do NOT translate "
        "fixed enumerated token values, for example severity or level values like "
        '"high"/"medium"/"low" or "strong"/"ok"/"weak": emit those in English exactly '
        "as the schema specifies. Any field the schema says must copy an input value "
        "verbatim (e.g. axis labels) must be copied byte-for-byte from the input, never "
        "translated. Do not translate, rename, add, remove, or reorder keys."
    )


def output_language_subtag(output_locale: str | None) -> str | None:
    """What GP tells the client it directed: the BCP-47 primary subtag of the
    locale the directive was built for ("es-mx" -> "es"), or None when no
    directive was sent. Never "en" by inference: with no directive the model
    follows the served recipe, which names the transcript's language."""
    loc = normalize_locale(output_locale)
    return _base_lang(loc) if loc else None


def apply(system_prompt: str, locale: str | None, call_type: str | None = None) -> str:
    """Return `system_prompt` with the language directive appended when needed.

    A no-op ONLY for a missing locale. English injects like every other
    language since 2026-09-10 (Scott's ruling: the phone's language wins), and
    this docstring said otherwise for a day, which is the kind of sentence
    that gets believed instead of read."""
    directive = language_directive(locale, call_type)
    if not directive:
        return system_prompt
    return (system_prompt or "") + directive
