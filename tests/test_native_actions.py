"""Native action block (SS feature request 2026-07-19): chat answers to
action-items-shaped asks carry an optional additive `native_action`
envelope field (kind "reminders" + structured items) so the client can
render a one-tap Add to Reminders chip. Display text stays clean; the
items come from a post-answer extraction sub-call; every failure is
fail-open (block simply absent)."""

import json

import pytest

from app.services.native_actions import (
    _validate_items,
    looks_like_action_items_ask,
    native_actions_enabled,
)


def test_prefilter_vocabulary():
    assert looks_like_action_items_ask("what are my action items from this week?")
    assert looks_like_action_items_ask("give me a task list for the launch")
    assert looks_like_action_items_ask("add these to-dos as reminders")
    assert looks_like_action_items_ask("what are the next steps?")
    assert not looks_like_action_items_ask("summarize the meeting")
    assert not looks_like_action_items_ask("what time is the standup?")
    assert not looks_like_action_items_ask("")


def test_validate_items_shapes():
    raw = [
        {"title": "  Review HubSpot pipeline  ", "due": "2026-07-25", "owner": "Scott"},
        {"title": "Ping Doug", "due": "2026-07-25T09:30", "owner": None},
        {"title": "Bad due survives without due", "due": "next tuesday"},
        {"title": ""},                     # dropped: empty title
        {"no_title": True},                # dropped
        "not a dict",                      # dropped
        {"title": "x" * 500, "owner": "y" * 200},   # clamped
    ]
    items = _validate_items(raw)
    assert [i["title"] for i in items][:3] == [
        "Review HubSpot pipeline", "Ping Doug", "Bad due survives without due"]
    assert items[0]["due"] == "2026-07-25" and items[0]["owner"] == "Scott"
    assert items[1]["due"] == "2026-07-25T09:30" and "owner" not in items[1]
    assert "due" not in items[2]
    assert len(items[3]["title"]) == 200 and len(items[3]["owner"]) == 80
    assert _validate_items([]) is None
    assert _validate_items("junk") is None
    assert _validate_items([{"title": f"t{i}"} for i in range(30)]) is not None
    assert len(_validate_items([{"title": f"t{i}"} for i in range(30)])) == 20


def test_enabled_flag_reads_client_config():
    assert native_actions_enabled(
        {"client-config": {"native_actions": {"enabled": True}}})
    assert not native_actions_enabled(
        {"client-config": {"native_actions": {"enabled": False}}})
    assert not native_actions_enabled({"client-config": {}})
    assert not native_actions_enabled({})


def _enable(client):
    client.app.state.remote_configs.setdefault("client-config", {})[
        "native_actions"] = {"enabled": True, "kinds": ["reminders"]}


def test_chat_answer_carries_native_action_block(client, free_user, mock_provider):
    """JSON path e2e: action-items ask → main answer + extractor sub-call →
    native_action rides the envelope; display text untouched."""
    from tests.conftest import chat_request

    _enable(client)
    canned = mock_provider.canned_response
    answer = canned.model_copy(update={
        "text": "Here are your action items:\n1. Review HubSpot pipeline "
                "(Scott, by Friday)\n2. Ping Doug about response times"})
    extraction = canned.model_copy(update={"text": json.dumps({"items": [
        {"title": "Review HubSpot pipeline", "due": "2026-07-24", "owner": "Scott"},
        {"title": "Ping Doug about response times", "due": None, "owner": None},
    ]})})
    mock_provider.side_effect = [answer, extraction]

    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="What are my action items from this week?",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["text"].startswith("Here are your action items")
    na = data["native_action"]
    assert na["kind"] == "reminders"
    assert na["items"] == [
        {"title": "Review HubSpot pipeline", "due": "2026-07-24", "owner": "Scott"},
        {"title": "Ping Doug about response times"},
    ]


def test_ordinary_ask_never_calls_extractor(client, free_user, mock_provider):
    from tests.conftest import chat_request

    _enable(client)
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="Summarize the last meeting for me",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert "native_action" not in r.json()
    assert mock_provider.await_count == 1        # main answer only


def test_extractor_failure_fails_open(client, free_user, mock_provider):
    """Garbage extractor output → block absent, answer unharmed."""
    from tests.conftest import chat_request

    _enable(client)
    canned = mock_provider.canned_response
    answer = canned.model_copy(update={"text": "1. Do the thing"})
    garbage = canned.model_copy(update={"text": "not json at all"})
    mock_provider.side_effect = [answer, garbage]

    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="what are the action items?",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.json()["text"] == "1. Do the thing"
    assert "native_action" not in r.json()


def test_disabled_config_suppresses_block(client, free_user, mock_provider):
    from tests.conftest import chat_request

    client.app.state.remote_configs.setdefault("client-config", {})[
        "native_actions"] = {"enabled": False}
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="what are my action items?",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert "native_action" not in r.json()
    assert mock_provider.await_count == 1
