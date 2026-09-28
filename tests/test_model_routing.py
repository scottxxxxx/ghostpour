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


def _resolve_with_documents(call_type: str | None, prompt_mode: str | None, tier: str = "pro"):
    from app.models.chat import DocumentAttachment
    request = _mk_request(remote_configs={"model-routing": _routing_full()})
    body = ChatRequest(
        provider="auto", model="auto",
        system_prompt="", user_content="hi",
        call_type=call_type, prompt_mode=prompt_mode,
        documents=[DocumentAttachment(name="f.pdf", media_type="application/pdf", data="QQ==")],
    )
    return _resolve_model_routing(request, body, _TIER, tier)


def test_documents_upgrade_follow_up_to_first_send_dial():
    """Documents upgrade the turn (2026-07-10): a document-carrying send
    resolves through the surface's first-send dial even on follow-ups —
    the cheap lane's provider-side PDF page ceiling is lower than the
    served passthrough caps assume, and reading a document is first-class
    work. Applies to both chat surfaces; non-document follow-ups keep the
    cheap lane."""
    assert _resolve_with_documents("project_chat_follow_up", "ProjectChat") == SONNET
    assert _resolve_with_documents("meeting_chat_follow_up", "PostMeetingChat") == SONNET
    # first sends unchanged; non-document follow-ups unchanged
    assert _resolve_with_documents("project_chat", "ProjectChat") == SONNET
    assert _resolve("project_chat_follow_up", "ProjectChat") == HAIKU
    assert _resolve("meeting_chat_follow_up", "PostMeetingChat") == HAIKU


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


def test_every_routing_target_is_a_registered_provider_model():
    """Regression guard for the tr_research_company 400 ("Model
    'perplexity/sonar' not found for provider 'openrouter'"): every
    model-routing target must be a provider/model actually registered in
    config/providers.yml, or ProviderRouter.validate_model rejects the call at
    dispatch. Mirrors that check — only enforced when the provider lists models.
    """
    import json
    import yaml

    routing = json.load(open("config/remote/model-routing.json"))
    providers = yaml.safe_load(open("config/providers.yml"))["providers"]
    registered = {p: {m["id"] for m in (cfg.get("models") or [])} for p, cfg in providers.items()}

    bad = []
    for app, adef in (routing.get("apps") or {}).items():
        for call_type, cdef in (adef.get("call_types") or {}).items():
            for tier, target in (cdef.get("models") or {}).items():
                if not isinstance(target, str) or "/" not in target:
                    continue
                provider, model = target.split("/", 1)
                ids = registered.get(provider)
                if ids and model not in ids:  # provider lists models -> must contain this one
                    bad.append(f"{app}.{call_type}.{tier} -> {provider}/{model}")
    assert not bad, f"routing targets not registered in providers.yml: {bad}"


def test_shoulder_surf_auto_summary_dials_sonnet_for_paid_tiers_in_the_shipped_file():
    """2026-09-05, Scott's company meeting: the opening three minutes were
    garbled on the wire and Haiku declared the WHOLE 2400-word transcript
    unusable, twice, while Sonnet 4.6 wrote a correct report from the same
    text. Plus and Pro summaries move to Sonnet 4.6 (automation mirrors Pro,
    per test_automation_tier); Free stays on Haiku. 2026-09-28: the Sonnet
    row moved to Sonnet 5 with the rest of SS (Scott); still Sonnet, not Haiku.
    Read from the shipped file through the real resolver, not a fixture,
    so a dial that parses and does not resolve fails here."""
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from app.routers.chat import _resolve_model_routing

    routing = json.loads((Path(__file__).parent.parent / "config" / "remote" / "model-routing.json").read_text())
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(remote_configs={"model-routing": routing})),
                          state=SimpleNamespace(app_id="shouldersurf"))

    class _B:
        def get_meta(self, k):
            return "summary" if k == "call_type" else None

    fb = SimpleNamespace(default_model="anthropic/fallback")
    assert _resolve_model_routing(req, _B(), fb, "plus") == "anthropic/claude-sonnet-5"
    assert _resolve_model_routing(req, _B(), fb, "pro") == "anthropic/claude-sonnet-5"
    assert _resolve_model_routing(req, _B(), fb, "automation") == "anthropic/claude-sonnet-5"
    assert _resolve_model_routing(req, _B(), fb, "free") == "anthropic/claude-haiku-4-5-20251001"


def test_ss_queries_route_to_sonnet_5_on_every_tier():
    """Scott, 2026-09-28: in-meeting queries move from Sonnet 4.6 to Sonnet 5
    (A/B on a real capture: about half the total time, about 15% cheaper, and
    4.6 dropped a route from the screen in 3 of 3 answers). Read from the REAL
    bundle, so a later edit that puts 4.6 back is caught here. Summary and the
    chat surfaces are deliberately unchanged until each gets its own look."""
    import json
    from pathlib import Path

    routing = json.loads((Path(__file__).resolve().parent.parent
                          / "config/remote/model-routing.json").read_text())
    request = _mk_request(remote_configs={"model-routing": routing})
    for call_type in ("query", "query_follow_up"):
        for tier in ("free", "plus", "pro", "automation"):
            body = ChatRequest(provider="auto", model="auto", system_prompt="s",
                               user_content="u", call_type=call_type)
            assert _resolve_model_routing(request, body, _TIER, tier) == "anthropic/claude-sonnet-5", (call_type, tier)


def test_no_shoulder_surf_route_is_left_on_sonnet_4_6():
    """Scott, 2026-09-28: every SS route on Sonnet 4.6 moves to Sonnet 5 with
    default reasoning (queries first, then the rest the same day, with more
    testing of summary, chat and report owed later). Haiku rows stay Haiku."""
    import json
    from pathlib import Path

    routing = json.loads((Path(__file__).resolve().parent.parent
                          / "config/remote/model-routing.json").read_text())
    rows = routing["apps"]["shouldersurf"]["call_types"]
    left = [(c, t) for c, v in rows.items() for t, m in v["models"].items()
            if m == "anthropic/claude-sonnet-4-6"]
    # File generation stays on 4.6: it won a measured bench against Sonnet 5
    # on substance (2026-08-15, test_artifact_generation_dial). Held for
    # Scott's call rather than switched by a sweep.
    assert left == [("artifact_generation", "pro"), ("artifact_generation", "automation")], left
    assert rows["summary"]["models"]["pro"] == "anthropic/claude-sonnet-5"
    assert rows["analysis"]["models"]["free"] == "anthropic/claude-haiku-4-5-20251001"
