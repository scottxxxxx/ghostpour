"""TR's Live Copilot runs on Sonnet 5.5 at effort low, chosen by surface.

Scott, 2026-10-21: yes to TR's ask (docs/handoffs/tr-interview-reference-
2026-10-21.md, section 2). TR's replay put the first word near 1 s on
Sonnet 5.5 low against 6 to 10 s on what it ran before. The Copilot and the
job and session chats all send call_type=tr_query, so the model cannot be
picked by call_type; TR's client e916be4 adds `metadata.surface`
("copilot", "job_chat", "session_chat") and GP routes on it through a
`<call_type>@<surface>` row. Every build before e916be4 sends no surface
and must keep today's routing exactly.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest
import yaml

from app.models.chat import ChatRequest
from app.routers.chat import (_apply_routed_model, _apply_routed_thinking,
                              _resolve_model_routing)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTING = json.loads((ROOT / "config/remote/model-routing.json").read_text())
TR = ROUTING["apps"]["techrehearsal"]["call_types"]
TODAY_TR_QUERY = "anthropic/claude-haiku-4-5-20251001"


def _route(call_type, surface=None, app_id="techrehearsal", tier_name="paid",
           reasoning=None, request=None):
    request = request or SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            remote_configs={"model-routing": ROUTING})),
        state=SimpleNamespace(app_id=app_id))
    meta = {"call_type": call_type}
    if surface is not None:
        meta["surface"] = surface
    body = ChatRequest(provider="auto", model="auto", user_content="q",
                       metadata=meta, reasoning=reasoning)
    tier = SimpleNamespace(default_model="anthropic/claude-haiku-4-5-20251001")
    body = _apply_routed_model(body, _resolve_model_routing(request, body, tier, tier_name))
    return request, _apply_routed_thinking(request, body)


@pytest.mark.parametrize("tier_name", ["free", "paid", "automation"])
@pytest.mark.parametrize("call_type", ["tr_query", "tr_follow_up"])
def test_a_copilot_ask_runs_on_sonnet_5_5_low(call_type, tier_name):
    _, body = _route(call_type, "copilot", tier_name=tier_name)
    assert (body.provider, body.model) == ("anthropic", "claude-sonnet-5-5")
    assert body.reasoning == "low"
    # Low effort, not thinking off: the replay winner reasoned a little.
    assert body.thinking is None


@pytest.mark.parametrize("surface", [None, "", "job_chat", "session_chat"])
def test_anything_but_copilot_keeps_todays_tr_query_route(surface):
    """No surface is every TR build on a phone today."""
    _, body = _route("tr_query", surface)
    assert f"{body.provider}/{body.model}" == TODAY_TR_QUERY
    assert body.thinking == "disabled"
    assert body.reasoning is None


def test_the_row_wins_over_the_clients_own_reasoning_pick():
    _, body = _route("tr_query", "copilot", reasoning="high")
    assert body.reasoning == "low"


def test_a_client_pick_survives_when_no_row_sets_one():
    _, body = _route("tr_query", reasoning="high")
    assert body.reasoning == "high"


def test_a_re_route_drops_the_copilot_effort():
    """The generation path re-resolves on the same request; the Copilot
    lane's effort must not ride into a document build."""
    request, body = _route("tr_query", "copilot")
    assert body.reasoning == "low"
    body = body.model_copy(update={"metadata": {**body.metadata,
                                               "call_type": "tr_parse_jd",
                                               "surface": None}})
    tier = SimpleNamespace(default_model="x/y")
    body = _apply_routed_thinking(request, _apply_routed_model(
        body, _resolve_model_routing(request, body, tier, "paid")))
    assert body.reasoning is None


def test_shouldersurf_sending_a_surface_is_untouched():
    """Only TR has surface rows; an SS send carrying the field routes as
    it did."""
    _, plain = _route("query", app_id="shouldersurf", tier_name="pro")
    _, with_surface = _route("query", "copilot", app_id="shouldersurf",
                             tier_name="pro")
    assert (with_surface.model, with_surface.thinking, with_surface.reasoning) == (
        plain.model, plain.thinking, plain.reasoning)


def test_surface_rows_never_shadow_a_real_call_type():
    """The `@` is what keeps `query` + surface `follow_up` from landing on
    the query_follow_up row."""
    for app in ROUTING["apps"].values():
        for name in app.get("call_types", {}):
            if "@" in name:
                base = name.split("@", 1)[0]
                assert base and "@" not in base


def test_the_router_will_actually_call_sonnet_5_5():
    """Selectable and routed is not callable: validate_model gates on the
    allow-list in providers.yml (test_routing_catalog_is_callable)."""
    cfg = yaml.safe_load((ROOT / "config/providers.yml").read_text())
    ids = [m["id"] for m in cfg["providers"]["anthropic"]["models"]]
    assert "claude-sonnet-5-5" in ids
    catalog = [m["id"] for m in ROUTING["models"]]
    assert "anthropic/claude-sonnet-5-5" in catalog


def test_the_wire_carries_effort_low_with_adaptive_thinking():
    from app.services.providers.anthropic import AnthropicAdapter
    req = ChatRequest(provider="anthropic", model="claude-sonnet-5-5",
                      user_content="q", system_prompt="s", reasoning="low")
    body, _ = AnthropicAdapter(
        api_key="test", base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key", auth_prefix="")._build_body(req)
    assert body.get("output_config") == {"effort": "low"}
    assert body.get("thinking") == {"type": "adaptive"}


@pytest.mark.parametrize("surface,expected_model,expected_reasoning", [
    ("copilot", "claude-sonnet-5-5", "low"),
    (None, "claude-haiku-4-5-20251001", None),
])
def test_the_real_chat_route_sends_it(client, pro_user, surface,
                                      expected_model, expected_reasoning):
    """Drives /v1/chat, so a router that stopped reading the surface or
    stopped applying the lane fails here even though the units pass."""
    from unittest.mock import AsyncMock, patch
    from app.models.chat import ChatResponse
    seen = []

    async def _capture(*args, **kwargs):
        for a in list(args) + list(kwargs.values()):
            if isinstance(a, ChatRequest):
                seen.append(a)
        return ChatResponse(text="ok", input_tokens=1, output_tokens=1,
                            model=expected_model, provider="anthropic", usage={},
                            raw_request_json="{}", raw_response_json="{}")

    meta = {"call_type": "tr_query"}
    if surface:
        meta["surface"] = surface
    with patch("app.services.anthropic_or_fallback.route_with_fallback",
               new=AsyncMock(side_effect=_capture)):
        r = client.post("/v1/chat", json={
            "provider": "auto", "model": "auto", "user_content": "what do I say",
            "system_prompt": "You help in a live interview.", "metadata": meta,
        }, headers={**pro_user["headers"], "X-App-ID": "techrehearsal"})
    assert r.status_code == 200, r.text[:300]
    assert seen, "the provider call was never reached"
    assert (seen[-1].model, seen[-1].reasoning) == (expected_model, expected_reasoning)
