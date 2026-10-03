"""The 2026-09-29 outage: the org's Anthropic spend cap was hit at about
20:30Z and every call came back 400 "You have reached your specified API
usage limits". Two defences failed together, and each is pinned here.

1. The provider health check SAW it (20:37:52Z) and did not alert: it only
   treated 402 and 401/403 as alertable, and this was a 400.
2. The OpenRouter fallback could not help: every Shoulder Surf route had
   moved to claude-sonnet-5 the day before, and the fallback's model map
   had no entry for it, so fallback was silently OFF for all of them.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.anthropic_or_fallback import translate_to_or_model_id
from app.services.provider_health import ProbeResult, _alert_decision, _now

ROOT = Path(__file__).resolve().parent.parent
CAP_BODY = ('HTTP 400: {"type":"error","error":{"type":"invalid_request_error","message":'
            '"You have reached your specified API usage limits. You will regain access on '
            '2026-10-01 at 00:00 UTC."}}')


def _probe(code, detail):
    return ProbeResult(provider="anthropic", checked_at=_now(), healthy=False,
                       status_code=code, detail=detail)


def test_the_spend_cap_400_alerts_as_budget_exhausted():
    assert _alert_decision(_probe(400, CAP_BODY)) == ("provider_budget_exhausted", "anthropic_usage_limit")


def test_a_credit_balance_400_alerts_too():
    body = 'HTTP 400: {"error":{"message":"Your credit balance is too low to access the Anthropic API."}}'
    assert _alert_decision(_probe(400, body))[0] == "provider_budget_exhausted"


def test_an_ordinary_400_still_does_not_alert():
    assert _alert_decision(_probe(400, 'HTTP 400: {"error":{"message":"messages: at least one message is required"}}')) is None


def _routed_anthropic_models() -> set[str]:
    doc = json.loads((ROOT / "config/remote/model-routing.json").read_text())
    out = set()
    for app in doc["apps"].values():
        for ct in app["call_types"].values():
            for mid in ct["models"].values():
                if mid.startswith("anthropic/"):
                    out.add(mid.split("/", 1)[1])
    for f in (ROOT / "config/remote/n400").glob("*.json"):
        m = json.loads(f.read_text()).get("recommendedModel") or ""
        if m.startswith("claude-"):
            out.add(m)
    return out


def test_every_routed_claude_model_can_fail_over_to_openrouter():
    """Moving a route to a new model must not silently switch its fallback off."""
    routed = _routed_anthropic_models()
    assert "claude-sonnet-5" in routed  # the list is real, not empty
    missing = sorted(m for m in routed if translate_to_or_model_id(m) is None)
    assert not missing, f"no OpenRouter fallback for {missing}; add them to _OR_MODEL_TRANSLATION"


def test_the_fallback_actually_reaches_openrouter_through_the_real_router(monkeypatch):
    """The mapping alone was not enough: the router's allowlist refused every
    Claude-on-OpenRouter id with a 400, so the fallback had never worked. This
    drives the REAL router: Anthropic fails the way the cap failed (the
    adapter raises a 502), and the answer must come back from OpenRouter."""
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.models.chat import ChatRequest, ChatResponse
    from app.services.anthropic_or_fallback import route_with_fallback
    from app.services.provider_router import ProviderRouter
    from app.services.providers.anthropic import AnthropicAdapter
    from app.services.providers.openai_compat import OpenAICompatAdapter

    async def capped(self, request):
        raise HTTPException(status_code=502, detail={"code": "provider_error",
                            "message": "anthropic: You have reached your specified API usage limits."})
    seen = []

    async def openrouter(self, request):
        seen.append(request.model)
        return ChatResponse(text="from openrouter", input_tokens=1, output_tokens=1,
                            model=request.model, provider="openrouter", usage={})
    monkeypatch.setattr(AnthropicAdapter, "send_request", capped)
    monkeypatch.setattr(OpenAICompatAdapter, "send_request", openrouter)
    router = ProviderRouter(str(ROOT / "config/providers.yml"),
                            SimpleNamespace(anthropic_api_key="a", openrouter_api_key="o"))
    req = ChatRequest(provider="anthropic", model="claude-sonnet-5", system_prompt="s", user_content="u")
    out = asyncio.run(route_with_fallback(router, req, None, SimpleNamespace(alert_email_from="x@y")))
    assert out.text == "from openrouter" and seen == ["anthropic/claude-sonnet-5"]


def test_no_model_call_skips_the_fallback():
    """Scott, 2026-09-29: if we cannot reach Anthropic or have no tokens,
    fall back to OpenRouter, EVERYWHERE. Eight call sites went straight to
    the router and would have failed on the cap even with the fallback
    fixed. A new direct call fails here instead of in an outage."""
    import re
    direct = []
    for f in (ROOT / "app").rglob("*.py"):
        if f.name == "anthropic_or_fallback.py":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"provider_router\.route(_stream)?\(", line):
                direct.append(f"{f.relative_to(ROOT)}:{n}")
    assert not direct, f"direct model calls without the OpenRouter fallback: {direct}"
