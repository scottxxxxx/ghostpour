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
