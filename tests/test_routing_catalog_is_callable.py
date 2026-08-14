"""Every model the dashboard offers must be one the router will call.

2026-08-14: Opus 5 was added to the routing catalog (`model-routing.json`
models[]), which is what populates the dashboard dropdown. Selecting it
produced an immediate failure with `validate_model rejected
model='claude-opus-5' available=[...the old four]` — because
`config/providers.yml` carries a SEPARATE per-provider allow-list that
`ProviderRouter.validate_model` gates on, and it had not been updated.

The request never reached Anthropic (0 ms, HTTP 400 from our own edge),
and the client-facing message is deliberately opaque ("The requested
model is not available") for relay opacity, so the only evidence is a
server log line. That combination — selectable in the UI, refused at the
gate, silent to the user — is why this needs a test rather than care.
"""

from __future__ import annotations

import json

import yaml


def _routing_catalog() -> list[str]:
    with open("config/remote/model-routing.json") as fh:
        return [m["id"] for m in json.load(fh).get("models", [])]


def _provider_allowlists() -> dict[str, list[str]]:
    with open("config/providers.yml") as fh:
        cfg = yaml.safe_load(fh)
    providers = cfg.get("providers", cfg)
    return {
        name: [m["id"] for m in (p.get("models") or [])]
        for name, p in providers.items()
        if isinstance(p, dict)
    }


def test_every_offered_model_is_callable():
    allow = _provider_allowlists()
    unroutable = []
    for entry in _routing_catalog():
        provider, _, model = entry.partition("/")
        assert model, f"catalog entry {entry!r} is missing its provider prefix"
        listed = allow.get(provider)
        # An empty list means the provider is open-ended (validate_model
        # returns early), so only a populated list can reject.
        if listed and model not in listed:
            unroutable.append(entry)
    assert not unroutable, (
        "these models are selectable in the dashboard but ProviderRouter."
        f"validate_model would reject them: {unroutable}. Add them to the "
        "matching provider's models[] in config/providers.yml."
    )


def test_the_catalog_is_not_empty_and_names_its_provider():
    catalog = _routing_catalog()
    assert catalog, "an empty catalog leaves the dashboard with no options"
    for entry in catalog:
        assert "/" in entry, f"{entry!r} must be provider-prefixed"
