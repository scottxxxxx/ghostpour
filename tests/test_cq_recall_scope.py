"""The people-scoped recall lane (decision 2026-08-11).

People launches at full value on every tier, and the standing rule is
that the assistant may know exactly what the user's own screens show
them. Before this lane, context_quilt disabled meant the CQ hook never
ran, so a free user's People tab showed colleagues and commitments while
the assistant in the same app claimed it had never heard of them.

The mapping is server-side entitlement only (Scott, option one): the
client's context_quilt flag follows the SERVED entitlement state, so
free builds never send it, and BYOK plus Apple-FM traffic bypasses GP
entirely. The wire contract with CQ: metadata.recall_scope "people"
selects the tab-equivalent scoped render, and the key ABSENT means the
full render, so the enabled lane must never send it in any spelling.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatRequest
from app.models.user import UserRecord
from app.services.features.context_quilt_hook import ContextQuiltHook
from tests.conftest import chat_request


def _user(tier: str = "free") -> UserRecord:
    return UserRecord(
        id="u-scope-1",
        apple_sub="apple-u-scope-1",
        tier=tier,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def _body(**kwargs) -> ChatRequest:
    defaults = dict(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt="BASE INSTRUCTIONS\n\n{{context_quilt}}",
        user_content="what does Vijay owe me?",
    )
    defaults.update(kwargs)
    return ChatRequest(**defaults)


# --- hook level: what the lane sends -----------------------------------


@pytest.mark.asyncio
async def test_free_lane_sends_recall_scope_people():
    """The wire contract in one assertion: disabled state means recall
    still runs, scoped to what the People tab shows."""
    hook = ContextQuiltHook()
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("free"), body=_body(), tier=None,
            feature_state="disabled", skip_teasers=set())
    recall.assert_awaited_once()
    assert recall.await_args.kwargs["metadata"]["recall_scope"] == "people"
    assert recall.await_args.kwargs["subscription_tier"] == "free"


@pytest.mark.asyncio
async def test_free_lane_does_not_need_the_client_flag():
    """Option one. SS sends context_quilt by served entitlement state, so
    a free build never sends it; gating the lane on the flag would keep
    the lane permanently dark for exactly the users it exists for."""
    hook = ContextQuiltHook()
    body = _body(context_quilt=False)
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("free"), body=body, tier=None,
            feature_state="disabled", skip_teasers=set())
    recall.assert_awaited_once()


@pytest.mark.asyncio
async def test_enabled_lane_omits_the_key_entirely():
    """Absent means full. Not "full", not None: the key must not exist,
    because CQ keys the render cache on the whole request shape and an
    explicit spelling would split the cache for identical renders."""
    hook = ContextQuiltHook()
    body = _body(context_quilt=True)
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "ctx", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("pro"), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
    sent = recall.await_args.kwargs["metadata"] or {}
    assert "recall_scope" not in sent


@pytest.mark.asyncio
async def test_client_cannot_choose_its_own_scope():
    """recall_scope is entitlement-derived, so it is set server-side
    after the allowlist composition, never copied from the request. A
    free client asking for the full scope still gets "people"; a paid
    client asking for a scope still sends nothing."""
    hook = ContextQuiltHook()
    free_body = _body(metadata={"recall_scope": "full"})
    paid_body = _body(context_quilt=True, metadata={"recall_scope": "people"})
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("free"), body=free_body, tier=None,
            feature_state="disabled", skip_teasers=set())
        assert recall.await_args.kwargs["metadata"]["recall_scope"] == "people"
        await hook.before_llm(
            user=_user("pro"), body=paid_body, tier=None,
            feature_state="enabled", skip_teasers=set())
        assert "recall_scope" not in (recall.await_args.kwargs["metadata"] or {})


@pytest.mark.asyncio
async def test_free_lane_injects_and_stashes_like_the_enabled_lane():
    """Doc 17 composition carries over unchanged: fill the placeholder or
    append last, and stash the exact text on cq_recall_block so the
    Anthropic adapter can split the cache at the recall boundary."""
    hook = ContextQuiltHook()
    recall_text = "[PEOPLE] Vijay Rao: owes you the Q3 forecast (due Friday)"
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": recall_text, "matched_entities": ["Vijay Rao"]},
    ):
        new_body, result = await hook.before_llm(
            user=_user("free"), body=_body(), tier=None,
            feature_state="disabled", skip_teasers=set())
    assert recall_text in new_body.system_prompt
    assert "{{context_quilt}}" not in new_body.system_prompt
    assert new_body.metadata["cq_recall_block"] == recall_text
    assert result["cq_result"]["matched_entities"] == ["Vijay Rao"]


@pytest.mark.asyncio
async def test_free_lane_carries_the_shared_metadata_keys():
    """The lane reuses the same composition as the enabled lane, so the
    contract keys (project scoping, locale, memory_signals passthrough)
    ride along rather than forking into a second, staler allowlist."""
    hook = ContextQuiltHook()
    body = _body(metadata={"prompt_mode": "ProjectChat", "project": "Kore",
                           "project_id": "proj-1", "memory_signals": True})
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "", "matched_entities": []},
    ) as recall:
        await hook.before_llm(
            user=_user("free"), body=body, tier=None,
            feature_state="disabled", skip_teasers=set())
    sent = recall.await_args.kwargs["metadata"]
    assert sent["project_id"] == "proj-1"
    assert sent["memory_signals"] is True
    assert sent["token_budget"] == 1200
    assert sent["locale"] == "en"


@pytest.mark.asyncio
async def test_free_lane_fires_no_edit_lanes_and_no_capture():
    """Corrections, completions, and capture stay enabled-only. A free
    user phrasing a correction gets the scoped block and a normal answer,
    not a memory write."""
    hook = ContextQuiltHook()
    body = _body(user_content="Set the record straight, Robin owns that",
                 metadata={"prompt_mode": "ProjectChat", "project_id": "p1"})
    with patch(
        "app.services.features.context_quilt_hook.cq.recall",
        new_callable=AsyncMock,
        return_value={"context": "ctx", "matched_entities": []},
    ), patch(
        "app.services.features.context_quilt_hook.cq.capture",
        new_callable=AsyncMock,
    ) as capture:
        new_body, result = await hook.before_llm(
            user=_user("free"), body=body, tier=None,
            feature_state="disabled", skip_teasers=set())
        await asyncio.sleep(0)
        # after_llm is gated to enabled, so the turn writes nothing.
        await hook.after_llm(
            user=_user("free"), body=new_body, response=None,
            hook_result=result, feature_state="disabled")
    capture.assert_not_awaited()
    assert "MEMORY CORRECTION" not in new_body.system_prompt
    assert "MEMORY COMPLETION" not in new_body.system_prompt


# --- dispatch level: the entitlement mapping end to end ----------------
#
# The real matrix is the fixture here (conftest client runs the lifespan):
# context_quilt free=disabled, people free=enabled, so a free user IS the
# people lane and a pro user IS the full lane, with nothing stubbed.


def test_free_chat_turn_runs_people_scoped_recall(client_with_cq, free_user, mock_provider, mock_cq):
    """End to end through /v1/chat: the dispatch routes a disabled-state
    context_quilt hook call, the lane scopes it, and the block reaches
    the model. No client flag in the request, per option one."""
    resp = client_with_cq.post("/v1/chat", json=chat_request(
        user_content="what does Bob owe me?",
    ), headers=free_user["headers"])
    assert resp.status_code == 200
    mock_cq["recall"].assert_awaited_once()
    sent = mock_cq["recall"].await_args.kwargs["metadata"]
    assert sent["recall_scope"] == "people"
    # The scoped block was injected for the model, not just fetched.
    sent_prompt = mock_provider.call_args.args[0].system_prompt
    assert "User prefers concise answers" in sent_prompt


def test_pro_chat_turn_still_omits_the_key(client_with_cq, pro_user, mock_provider, mock_cq):
    resp = client_with_cq.post("/v1/chat", json=chat_request(
        user_content="what does Bob owe me?", context_quilt=True,
    ), headers=pro_user["headers"])
    assert resp.status_code == 200
    mock_cq["recall"].assert_awaited_once()
    sent = mock_cq["recall"].await_args.kwargs["metadata"] or {}
    assert "recall_scope" not in sent


def test_people_toggle_closes_the_free_lane(client_with_cq, free_user, mock_cq):
    """The dashboard People row must actually close the door, same
    requirement the People proxy routes carry: with people disabled too,
    a free turn makes no recall call at all."""
    def _state(configs, tier, feature):
        return "disabled" if feature in ("context_quilt", "people") else "enabled"

    with patch("app.routers.chat.entitlement_state", side_effect=_state):
        resp = client_with_cq.post("/v1/chat", json=chat_request(
            user_content="what does Bob owe me?",
        ), headers=free_user["headers"])
    assert resp.status_code == 200
    mock_cq["recall"].assert_not_awaited()


# --- Free context_quilt = teaser (Scott 2026-08-24) ---------------------------

def _set_free_cq(state):
    from app.main import app
    matrix = app.state.remote_configs.setdefault("entitlements", {}).setdefault("matrix", {})
    cell = matrix.setdefault("context_quilt", {})
    prev = cell.get("free")
    cell["free"] = state
    return prev


def test_free_teaser_without_client_flag_still_runs_the_people_lane(client_with_cq, free_user, mock_provider, mock_cq):
    """The flip must not drop Free recall: a teaser state on a client
    that sends no flag (every Free build) routes to the People-scoped
    lane exactly like disabled did, so by_scope keeps arriving."""
    prev = _set_free_cq("teaser")
    try:
        resp = client_with_cq.post("/v1/chat", json=chat_request(
            user_content="what does Bob owe me?",
        ), headers=free_user["headers"])
        assert resp.status_code == 200
        mock_cq["recall"].assert_awaited_once()
        assert mock_cq["recall"].await_args.kwargs["metadata"]["recall_scope"] == "people"
        assert "User prefers concise answers" in mock_provider.call_args.args[0].system_prompt
    finally:
        _set_free_cq(prev)


def test_free_teaser_with_client_flag_keeps_the_metadata_only_teaser_recall(client_with_cq, free_user, mock_provider, mock_cq):
    prev = _set_free_cq("teaser")
    try:
        resp = client_with_cq.post("/v1/chat", json=chat_request(
            user_content="what does Bob owe me?", context_quilt=True,
        ), headers=free_user["headers"])
        assert resp.status_code == 200
        mock_cq["recall"].assert_awaited_once()
        sent = mock_cq["recall"].await_args.kwargs["metadata"] or {}
        assert "recall_scope" not in sent                      # the hook's own teaser lane
        assert "User prefers concise answers" not in mock_provider.call_args.args[0].system_prompt  # not injected
    finally:
        _set_free_cq(prev)


def test_bundled_matrix_serves_free_context_quilt_as_teaser():
    import json
    with open("config/remote/entitlements.json") as f:
        assert json.load(f)["matrix"]["context_quilt"]["free"] == "teaser"
