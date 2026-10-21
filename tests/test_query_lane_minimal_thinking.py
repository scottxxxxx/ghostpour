"""Live Copilot taps run with the least thinking their model allows.

Scott, 2026-10-21 ("please do what the SS team asks related to model
changes"), after his EMIDS interview on 2026-10-20: the first word took 7 to
15 s on the early asks (b0f5628255c3: 14.7 s, 1,572 output tokens for a
five-second card). The lane setting lives on the model-routing ROW, so a
ProjectChat or PostMeetingChat send that arrives as call_type=query keeps its
own surface row and is untouched.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from app.models.chat import ChatRequest
from app.routers.chat import (_apply_routed_model, _apply_routed_thinking,
                              _resolve_model_routing)

ROUTING = json.loads(
    (pathlib.Path(__file__).resolve().parents[1]
     / "config/remote/model-routing.json").read_text())
SS = ROUTING["apps"]["shouldersurf"]["call_types"]


def test_only_the_copilot_rows_carry_the_setting():
    assert SS["query"]["thinking"] == "disabled"
    assert SS["query_follow_up"]["thinking"] == "disabled"
    untouched = [k for k in SS if k not in ("query", "query_follow_up")]
    assert {"meeting_chat", "project_chat", "summary", "report", "analysis",
            "artifact_generation"} <= set(untouched)
    assert [k for k in untouched if "thinking" in SS[k]] == []


def _route(call_type, prompt_mode=None, request=None, tier_name="pro"):
    request = request or SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            remote_configs={"model-routing": ROUTING})),
        state=SimpleNamespace(app_id="shouldersurf"))
    meta = {"call_type": call_type}
    if prompt_mode:
        meta["prompt_mode"] = prompt_mode
    body = ChatRequest(provider="auto", model="auto", user_content="q",
                       metadata=meta)
    tier = SimpleNamespace(default_model="anthropic/claude-sonnet-5")
    body = _apply_routed_model(body, _resolve_model_routing(request, body, tier, tier_name))
    return request, _apply_routed_thinking(request, body)


@pytest.mark.parametrize("tier_name", ["free", "plus", "pro", "automation"])
@pytest.mark.parametrize("call_type", ["query", "query_follow_up"])
def test_a_copilot_tap_routes_with_thinking_disabled(call_type, tier_name):
    _, body = _route(call_type, "Emids2", tier_name=tier_name)
    assert body.thinking == "disabled"


@pytest.mark.parametrize("prompt_mode", ["ProjectChat", "PostMeetingChat"])
def test_a_chat_surface_sent_as_query_keeps_its_own_row(prompt_mode):
    _, body = _route("query", prompt_mode)
    assert body.thinking is None


@pytest.mark.parametrize("call_type", ["summary", "report", "analysis"])
def test_other_lanes_are_untouched(call_type):
    _, body = _route(call_type)
    assert body.thinking is None


def test_a_re_route_to_artifact_generation_drops_the_copilot_setting():
    """The generation path re-resolves on the same request; the Copilot
    lane's setting must not ride into a document build."""
    request, body = _route("query", "Emids2")
    assert body.thinking == "disabled"
    body = body.model_copy(update={"metadata": {**body.metadata,
                                               "call_type": "artifact_generation"}})
    tier = SimpleNamespace(default_model="anthropic/claude-sonnet-5")
    body = _apply_routed_thinking(request, _apply_routed_model(
        body, _resolve_model_routing(request, body, tier, "pro")))
    assert body.thinking is None


@pytest.mark.parametrize("model,block", [
    ("claude-sonnet-5", {"type": "disabled"}),
    ("claude-sonnet-5-5", {"type": "between_tools"}),
])
def test_the_wire_carries_the_floor_for_each_model(model, block):
    """What Anthropic receives: off on Sonnet 5, between_tools on Sonnet 5.5
    (where `disabled` is a 400)."""
    from app.services.providers.anthropic import AnthropicAdapter
    req = ChatRequest(provider="anthropic", model=model, user_content="q",
                      system_prompt="s", thinking="disabled")
    body, _ = AnthropicAdapter(
        api_key="test", base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key", auth_prefix="")._build_body(req)
    assert body.get("thinking") == block


@pytest.mark.parametrize("call_type,prompt_mode,expected", [
    ("query", "Emids2", "disabled"),
    ("query", "ProjectChat", None),
    ("summary", "AutoSummary", None),
])
def test_the_real_chat_route_sends_the_lane_setting(client, pro_user,
                                                    call_type, prompt_mode, expected):
    """Drives /v1/chat, so a router that stopped calling
    _apply_routed_thinking fails here even though every unit above passes."""
    from unittest.mock import AsyncMock, patch
    from app.models.chat import ChatResponse
    seen = []

    async def _capture(*args, **kwargs):
        for a in list(args) + list(kwargs.values()):
            if isinstance(a, ChatRequest):
                seen.append(a)
        return ChatResponse(text="ok", input_tokens=1, output_tokens=1,
                            model="claude-sonnet-5", provider="anthropic", usage={},
                            raw_request_json="{}", raw_response_json="{}")

    with patch("app.services.anthropic_or_fallback.route_with_fallback",
               new=AsyncMock(side_effect=_capture)):
        r = client.post("/v1/chat", json={
            "provider": "auto", "model": "auto", "user_content": "what do I say",
            "system_prompt": "You help in a live meeting.",
            "metadata": {"call_type": call_type, "prompt_mode": prompt_mode},
        }, headers={**pro_user["headers"], "X-App-ID": "shouldersurf"})
    assert r.status_code == 200, r.text[:300]
    assert seen, "the provider call was never reached"
    assert seen[-1].thinking == expected
