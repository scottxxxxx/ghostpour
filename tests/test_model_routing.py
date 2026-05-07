"""Tests for `_resolve_model_routing` in `app/routers/chat.py`.

Pinning the ProjectChat preference: when `prompt_mode == "ProjectChat"`,
the resolver prefers the dedicated `project_chat` call_type entry in
the model-routing config over the `query` (or whatever the request's
actual `call_type`) entry. This lets the admin dashboard dial Project
Chat independently from other interactive paths sharing the same
`query` call_type.

Falls through to the regular call_type lookup when the project_chat
row is absent or missing an entry for the tier.
"""

from types import SimpleNamespace

from app.models.chat import ChatRequest
from app.routers.chat import _resolve_model_routing


def _request_state(app_id: str = "shouldersurf"):
    return SimpleNamespace(
        app_state=SimpleNamespace(
            remote_configs={},
        ),
        state=SimpleNamespace(app_id=app_id),
    )


def _mk_request(*, remote_configs: dict, app_id: str = "shouldersurf"):
    """Build a fake Request object with the bits _resolve_model_routing
    reads. Pydantic's Request is a Starlette wrapper; we only need the
    `app.state.remote_configs` dict and `state.app_id` attribute."""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(remote_configs=remote_configs)),
        state=SimpleNamespace(app_id=app_id),
    )


_TIER = SimpleNamespace(default_model="anthropic/claude-haiku-4-5-20251001")


def _routing(*, with_project_chat: bool):
    apps = {
        "shouldersurf": {
            "label": "Shoulder Surf",
            "tiers": ["free", "plus", "pro"],
            "call_types": {
                "query": {
                    "label": "Interactive Query",
                    "models": {
                        "free": "anthropic/claude-haiku-4-5-20251001",
                        "plus": "anthropic/claude-haiku-4-5-20251001",
                        "pro": "anthropic/claude-haiku-4-5-20251001",
                    },
                },
            },
        },
    }
    if with_project_chat:
        apps["shouldersurf"]["call_types"]["project_chat"] = {
            "label": "Project Chat",
            "models": {
                "free": "anthropic/claude-haiku-4-5-20251001",
                "plus": "anthropic/claude-haiku-4-5-20251001",
                "pro": "anthropic/claude-sonnet-4-6",
            },
        }
    return {"version": 6, "models": [], "apps": apps}


def test_project_chat_uses_dedicated_dial_when_present():
    request = _mk_request(remote_configs={"model-routing": _routing(with_project_chat=True)})
    body = ChatRequest(
        provider="auto",
        model="auto",
        system_prompt="",
        user_content="hi",
        call_type="query",
        prompt_mode="ProjectChat",
    )
    model = _resolve_model_routing(request, body, _TIER, "pro")
    assert model == "anthropic/claude-sonnet-4-6"


def test_non_project_chat_uses_call_type_dial():
    """Same routing config, but prompt_mode is not ProjectChat → resolver
    uses `query` entry (Haiku for Pro), not the `project_chat` row."""
    request = _mk_request(remote_configs={"model-routing": _routing(with_project_chat=True)})
    body = ChatRequest(
        provider="auto",
        model="auto",
        system_prompt="",
        user_content="hi",
        call_type="query",
        prompt_mode="MeetingChat",
    )
    model = _resolve_model_routing(request, body, _TIER, "pro")
    assert model == "anthropic/claude-haiku-4-5-20251001"


def test_project_chat_falls_back_when_dial_absent():
    """ProjectChat with no `project_chat` row in routing → resolver
    falls through to the call_type lookup (regression guard for older
    config files that pre-date the dial)."""
    request = _mk_request(remote_configs={"model-routing": _routing(with_project_chat=False)})
    body = ChatRequest(
        provider="auto",
        model="auto",
        system_prompt="",
        user_content="hi",
        call_type="query",
        prompt_mode="ProjectChat",
    )
    model = _resolve_model_routing(request, body, _TIER, "pro")
    assert model == "anthropic/claude-haiku-4-5-20251001"


def test_project_chat_falls_back_when_dial_missing_tier_entry():
    """`project_chat` row exists but has no entry for this tier → fall
    through to call_type entry. Defensive against partial dashboard
    edits (e.g., admin removed only the Pro slot)."""
    routing = _routing(with_project_chat=True)
    # Remove just the Pro entry from project_chat
    del routing["apps"]["shouldersurf"]["call_types"]["project_chat"]["models"]["pro"]
    request = _mk_request(remote_configs={"model-routing": routing})
    body = ChatRequest(
        provider="auto",
        model="auto",
        system_prompt="",
        user_content="hi",
        call_type="query",
        prompt_mode="ProjectChat",
    )
    model = _resolve_model_routing(request, body, _TIER, "pro")
    # No project_chat.pro → falls through to query.pro
    assert model == "anthropic/claude-haiku-4-5-20251001"


def test_no_routing_config_returns_tier_default():
    request = _mk_request(remote_configs={})
    body = ChatRequest(
        provider="auto",
        model="auto",
        system_prompt="",
        user_content="hi",
        call_type="query",
        prompt_mode="ProjectChat",
    )
    model = _resolve_model_routing(request, body, _TIER, "pro")
    assert model == _TIER.default_model
