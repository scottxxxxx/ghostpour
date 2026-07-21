"""Central locale (response-language) injection for managed calls.

See app.services.locale_injection + docs/handoffs/tr-managed-prompts-and-locale.md.
"""

from app.services.locale_injection import (
    apply,
    language_directive,
    normalize_locale,
)
from tests.conftest import chat_request


# --- normalize_locale ------------------------------------------------------

def test_normalize_locale():
    assert normalize_locale(None) is None
    assert normalize_locale("") is None
    assert normalize_locale("   ") is None
    assert normalize_locale("ES") == "es"
    assert normalize_locale("  es  ") == "es"
    assert normalize_locale("es_MX") == "es-mx"
    assert normalize_locale("pt-BR") == "pt-br"


# --- language_directive: when it is a no-op --------------------------------

def test_no_directive_for_english_or_missing():
    for loc in (None, "", "   ", "en", "EN", "en-US", "en_us"):
        assert language_directive(loc) is None, loc


# --- language_directive: content -------------------------------------------

def test_directive_names_known_language():
    d = language_directive("es")
    assert d is not None
    assert "Spanish" in d
    assert "es" in d


def test_directive_resolves_region_to_base_language():
    d = language_directive("es-MX")
    assert d is not None
    assert "Spanish" in d          # base language resolved
    assert "es-mx" in d            # full tag still named


def test_directive_keeps_json_keys_english():
    # The structured-call guard: values translate, keys/structure stay English.
    d = language_directive("fr")
    assert "French" in d
    low = d.lower()
    assert "json" in low
    assert "key" in low
    assert "english" in low
    # explicitly forbids mutating keys
    assert "reorder" in low or "rename" in low


def test_directive_protects_enum_tokens_and_verbatim_labels():
    # Structured clients (tr_match_analysis) parse by fixed enum tokens and by
    # fit_by_dimension labels that mirror the input axes verbatim. The directive
    # must keep those from being translated on a non-English run.
    low = language_directive("es").lower()
    assert "enumerated token" in low          # severity/level tokens guarded
    assert "high" in low and "strong" in low  # names the actual tokens
    assert "verbatim" in low and "byte-for-byte" in low  # input-copied labels guarded


def test_unknown_language_still_injects_with_code():
    # A non-English code we don't have a name for still injects, naming the ISO
    # code rather than silently defaulting to English.
    d = language_directive("xx")
    assert d is not None
    assert "xx" in d


# --- apply -----------------------------------------------------------------

def test_apply_appends_for_non_english():
    base = "You are a helpful assistant."
    out = apply(base, "es")
    assert out.startswith(base)
    assert len(out) > len(base)
    assert "Spanish" in out


def test_apply_is_noop_for_english_or_missing():
    base = "You are a helpful assistant."
    assert apply(base, "en") == base
    assert apply(base, None) == base
    assert apply(base, "") == base


def test_apply_tolerates_empty_system_prompt():
    out = apply("", "es")
    assert "Spanish" in out
    # and None-safe
    assert "Spanish" in apply(None, "es")


# --- composition: mirrors how chat.py applies it ---------------------------

def test_composition_with_assembled_managed_prompt():
    """chat.py assembles the managed prompt, then applies the locale directive.
    The result must carry both the config's system prompt and the directive."""
    from app.services.prompt_assembly import assemble_prompt

    cfgs = {
        "tr-jd-analysis": {
            "version": 1,
            "systemPrompt": "Analyze the job description and return JSON.",
            "userPromptTemplate": "",
        },
    }
    assembled = assemble_prompt("tr_parse_jd", "RAW JD TEXT", cfgs)
    assert assembled is not None

    localized = apply(assembled["system_prompt"], "es")
    assert assembled["system_prompt"] in localized      # original kept verbatim
    assert "Spanish" in localized                        # directive appended
    assert "every key and field name" in localized       # JSON keys guarded


# --- integration: the directive reaches the prompt sent to the provider ----

def test_directive_reaches_routed_prompt(client, free_user, mock_provider):
    """End to end through /v1/chat: a non-English `locale` appends the directive
    to the system prompt that is actually routed to the model — and it works on
    a client-sent prompt (the migration case), not just GP-assembled ones."""
    resp = client.post(
        "/v1/chat",
        json=chat_request(system_prompt="Base prompt.", locale="es"),
        headers=free_user["headers"],
    )
    assert resp.status_code == 200, resp.text
    routed = mock_provider.call_args.args[0]            # ChatRequest passed to route()
    assert "Base prompt." in routed.system_prompt
    assert "Spanish" in routed.system_prompt
    assert "every key and field name" in routed.system_prompt
    # observability header confirms injection fired at the wire
    assert resp.headers.get("X-Output-Locale") == "es"


def test_no_directive_routed_for_english(client, free_user, mock_provider):
    resp = client.post(
        "/v1/chat",
        json=chat_request(system_prompt="Base prompt.", locale="en"),
        headers=free_user["headers"],
    )
    assert resp.status_code == 200, resp.text
    routed = mock_provider.call_args.args[0]
    assert routed.system_prompt == "Base prompt."       # untouched
    assert "X-Output-Locale" not in resp.headers


def test_accept_language_fallback_injects_without_metadata_locale(client, free_user, mock_provider):
    """SS sends Accept-Language (device locale) but not metadata.locale. The
    /v1/chat path must fall back to it so managed output is still forced into
    the user's language."""
    resp = client.post(
        "/v1/chat",
        json=chat_request(system_prompt="Base prompt."),  # no locale in body
        headers={**free_user["headers"], "Accept-Language": "ja-JP,ja;q=0.9"},
    )
    assert resp.status_code == 200, resp.text
    routed = mock_provider.call_args.args[0]
    assert "Base prompt." in routed.system_prompt
    assert "Japanese" in routed.system_prompt
    assert resp.headers.get("X-Output-Locale") == "ja"


def test_accept_language_english_is_noop(client, free_user, mock_provider):
    resp = client.post(
        "/v1/chat",
        json=chat_request(system_prompt="Base prompt."),
        headers={**free_user["headers"], "Accept-Language": "en-US"},
    )
    assert resp.status_code == 200, resp.text
    routed = mock_provider.call_args.args[0]
    assert routed.system_prompt == "Base prompt."       # untouched
    assert "X-Output-Locale" not in resp.headers


def test_metadata_locale_wins_over_accept_language(client, free_user, mock_provider):
    """An explicit per-call locale beats the device default."""
    resp = client.post(
        "/v1/chat",
        json=chat_request(system_prompt="Base prompt.", locale="es"),
        headers={**free_user["headers"], "Accept-Language": "ja-JP"},
    )
    assert resp.status_code == 200, resp.text
    routed = mock_provider.call_args.args[0]
    assert "Spanish" in routed.system_prompt
    assert "Japanese" not in routed.system_prompt
    assert resp.headers.get("X-Output-Locale") == "es"
