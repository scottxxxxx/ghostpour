"""`supportsWebSearch` per model in llm-providers (SS ask, 2026-10-16).

SS's own-key web search (SS main 0c15976a, build 2243) reads an optional Bool
on each model. A served value wins; an absent one counts as TRUE for every
Anthropic and every OpenRouter model, so an explicit false is the only way to
turn a model off. Claude gets web_search_20250305 straight from the phone, and
OpenRouter gets its web plugin, which runs for any model on the route.

The served test is the one that matters: the file carrying the field and the
route serving it are different claims (rule 3), and a locale file that lacked
it would quietly win for a French phone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FILES = ["llm-providers.json", "llm-providers.es.json",
         "llm-providers.fr.json", "llm-providers.ja.json"]
SEARCH_PROVIDERS = {"anthropic", "openrouter"}


@pytest.mark.parametrize("name", FILES)
def test_every_anthropic_and_openrouter_model_says_whether_it_searches(name):
    d = json.loads(Path("config/remote", name).read_text())
    seen = 0
    for p in d["providers"]:
        for m in p["models"]:
            if p["id"] in SEARCH_PROVIDERS:
                assert isinstance(m.get("supportsWebSearch"), bool), (p["id"], m["id"])
                seen += 1
            else:
                # Only the two routes the client searches on carry it.
                assert "supportsWebSearch" not in m, (p["id"], m["id"])
    assert seen == 37


@pytest.mark.parametrize("lang", [None, "es", "fr", "ja"])
def test_the_field_is_served_on_the_route(client, lang):
    headers = {"X-App-ID": "shouldersurf"}
    if lang:
        headers["Accept-Language"] = lang
    r = client.get("/v1/config/llm-providers", headers=headers)
    assert r.status_code == 200
    body = r.json()
    data = body.get("data", body)
    served = {(p["id"], m["id"]): m.get("supportsWebSearch")
              for p in data["providers"] for m in p["models"]}
    assert served[("anthropic", "claude-haiku-4-5-20251001")] is True
    assert served[("openrouter", "perplexity/sonar")] is True
    assert served[("openai", "gpt-6-sol")] is None
    assert sum(v is True for v in served.values()) == 37


# `supportsDocuments` (SS ask, 2026-10-16, SS main b057b956, build 2249): an
# original PDF on the user's own key, phone straight to the provider. The
# client's fallback says yes to every Anthropic, Gemini, OpenAI and OpenRouter
# model, so an explicit false is what matters. OpenAI reads a PDF as text plus
# page images, so a model without vision cannot take one; OpenRouter parses a
# PDF itself for a model without native file input.
DOC_PROVIDERS = {"anthropic", "openai", "google", "openrouter"}
NO_DOCUMENTS = {("openai", "gpt-5.4-nano")}


@pytest.mark.parametrize("name", FILES)
def test_every_document_route_model_says_whether_it_takes_a_pdf(name):
    d = json.loads(Path("config/remote", name).read_text())
    seen = 0
    for p in d["providers"]:
        for m in p["models"]:
            if p["id"] in DOC_PROVIDERS:
                want = (p["id"], m["id"]) not in NO_DOCUMENTS
                assert m.get("supportsDocuments") is want, (p["id"], m["id"])
                if p["id"] == "openai" and not m["supportsVision"]:
                    assert want is False, m["id"]
                seen += 1
            else:
                assert "supportsDocuments" not in m, (p["id"], m["id"])
    assert seen == 54


@pytest.mark.parametrize("lang", [None, "es", "fr", "ja"])
def test_supports_documents_is_served_on_the_route(client, lang):
    headers = {"X-App-ID": "shouldersurf"}
    if lang:
        headers["Accept-Language"] = lang
    body = client.get("/v1/config/llm-providers", headers=headers).json()
    data = body.get("data", body)
    served = {(p["id"], m["id"]): m.get("supportsDocuments")
              for p in data["providers"] for m in p["models"]}
    assert served[("openai", "gpt-5.4-nano")] is False
    assert served[("google", "gemini-3.5-flash")] is True
    assert served[("xai", "grok-4.3")] is None
    assert sum(v is True for v in served.values()) == 53
