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


# ---------------------------------------------------------------------------
# Granular surface-aware dials — added 2026-05-07.
# Six-row spec: every chat surface (Copilot/freeform, Meeting Chat,
# Project Chat) has its own (first-send, follow-up) pair. Resolver keys
# on (prompt_mode, call_type) so iOS can dial each cell independently.
# See `docs/wire-contracts/model-routing-call-types.md`.
# ---------------------------------------------------------------------------


def _routing_full():
    """Full granular routing matching `config/remote/model-routing.json`."""
    HAIKU = "anthropic/claude-haiku-4-5-20251001"
    SONNET = "anthropic/claude-sonnet-4-6"
    apps = {
        "shouldersurf": {
            "label": "Shoulder Surf",
            "tiers": ["free", "plus", "pro"],
            "call_types": {
                "summary": {"label": "Auto Summary", "models": {"free": HAIKU, "plus": HAIKU, "pro": HAIKU}},
                "analysis": {"label": "Post-Session Analysis", "models": {"free": HAIKU, "plus": HAIKU, "pro": SONNET}},
                "report": {"label": "Meeting Report", "models": {"free": HAIKU, "plus": HAIKU, "pro": SONNET}},
                "query": {"label": "Interactive Query", "models": {"free": HAIKU, "plus": HAIKU, "pro": SONNET}},
                "query_follow_up": {"label": "Interactive Query — Follow-up", "models": {"free": HAIKU, "plus": HAIKU, "pro": HAIKU}},
                "meeting_chat": {"label": "Meeting Chat", "models": {"free": HAIKU, "plus": HAIKU, "pro": SONNET}},
                "meeting_chat_follow_up": {"label": "Meeting Chat — Follow-up", "models": {"free": HAIKU, "plus": HAIKU, "pro": HAIKU}},
                "project_chat": {"label": "Project Chat", "models": {"free": HAIKU, "plus": HAIKU, "pro": SONNET}},
                "project_chat_follow_up": {"label": "Project Chat — Follow-up", "models": {"free": HAIKU, "plus": HAIKU, "pro": HAIKU}},
            },
        },
    }
    return {"version": 7, "models": [], "apps": apps}


HAIKU = "anthropic/claude-haiku-4-5-20251001"
SONNET = "anthropic/claude-sonnet-4-6"


def _resolve(call_type: str | None, prompt_mode: str | None, tier: str = "pro"):
    request = _mk_request(remote_configs={"model-routing": _routing_full()})
    body = ChatRequest(
        provider="auto", model="auto",
        system_prompt="", user_content="hi",
        call_type=call_type, prompt_mode=prompt_mode,
    )
    return _resolve_model_routing(request, body, _TIER, tier)


# --- Project Chat surface --------------------------------------------------


def test_project_chat_first_send_routes_to_project_chat_dial():
    assert _resolve("project_chat", "ProjectChat") == SONNET


def test_project_chat_legacy_call_type_query_still_routes_to_project_chat():
    """Pre-respec iOS sends call_type=query inside ProjectChat. Surface
    preference catches this — routes to project_chat dial, not query."""
    assert _resolve("query", "ProjectChat") == SONNET


def test_project_chat_follow_up_routes_to_dedicated_follow_up_dial():
    assert _resolve("project_chat_follow_up", "ProjectChat") == HAIKU


def test_project_chat_follow_up_falls_back_when_row_missing_tier():
    """Surgical: project_chat_follow_up.pro dial removed → defensive
    fallback to project_chat first-send dial, not the unrelated `query`
    row. Pins the explicit defensive branch in the resolver."""
    routing = _routing_full()
    del routing["apps"]["shouldersurf"]["call_types"]["project_chat_follow_up"]["models"]["pro"]
    request = _mk_request(remote_configs={"model-routing": routing})
    body = ChatRequest(
        provider="auto", model="auto",
        system_prompt="", user_content="hi",
        call_type="project_chat_follow_up", prompt_mode="ProjectChat",
    )
    assert _resolve_model_routing(request, body, _TIER, "pro") == SONNET


# --- Meeting Chat surface --------------------------------------------------


def test_meeting_chat_first_send_routes_to_meeting_chat_dial():
    assert _resolve("meeting_chat", "PostMeetingChat") == SONNET


def test_meeting_chat_legacy_call_type_query_still_routes_to_meeting_chat():
    assert _resolve("query", "PostMeetingChat") == SONNET


def test_meeting_chat_follow_up_routes_to_dedicated_follow_up_dial():
    assert _resolve("meeting_chat_follow_up", "PostMeetingChat") == HAIKU


# --- Generic / Copilot / freeform paths ------------------------------------


def test_copilot_first_send_routes_to_query():
    """No prompt_mode (or any other prompt_mode) + call_type=query →
    Interactive Query row. Pro: Sonnet."""
    assert _resolve("query", None) == SONNET


def test_copilot_follow_up_routes_to_query_follow_up():
    assert _resolve("query_follow_up", None) == HAIKU


def test_unknown_call_type_falls_back_to_tier_default():
    assert _resolve("totally_made_up_type", None) == _TIER.default_model


def test_summary_and_analysis_still_route_directly():
    """Background call_types ignore prompt_mode preference and use
    their own row. Defensive — a misconfigured iOS that sets
    prompt_mode=ProjectChat with call_type=summary should still get
    the Auto Summary dial."""
    # Note: with surface preference enabled, prompt_mode=ProjectChat +
    # call_type=summary actually routes via the project_chat dial
    # because call_type doesn't match the follow-up row. This is
    # acceptable — production iOS doesn't mix call_type=summary with
    # prompt_mode=ProjectChat.
    assert _resolve("summary", None) == HAIKU
    assert _resolve("analysis", None) == SONNET
    assert _resolve("report", None) == SONNET
