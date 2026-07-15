"""The {{context_quilt}} placeholder must never reach the model as literal.

GP owns this slot: the CQ hook fills it when CQ is enabled and recall has
content, and a safety net strips any leftover in every other path (recall
empty, teaser, CQ-disabled tier, or context_quilt flag off). So a client
can leave the literal placeholder in its template and never leak it.
"""

from tests.conftest import chat_request


def test_placeholder_stripped_when_cq_disabled(client, free_user, mock_provider):
    """Free tier has CQ disabled, so the hook is skipped entirely. The
    literal placeholder must still be stripped before the model sees it."""
    body = chat_request(
        system_prompt="Global rules.\n\n{{context_quilt}}\n\nAnswer the user.",
        context_quilt=True,
    )
    resp = client.post("/v1/chat", json=body, headers=free_user["headers"])
    assert resp.status_code == 200
    mock_provider.assert_called_once()
    sent = mock_provider.call_args.args[0]
    assert "{{context_quilt}}" not in sent.system_prompt
    # Surrounding prompt text is preserved.
    assert "Global rules." in sent.system_prompt
    assert "Answer the user." in sent.system_prompt


def test_placeholder_filled_in_place_when_cq_enabled(
    client_with_cq, pro_user, mock_provider, mock_cq
):
    """Pro tier has CQ enabled and recall returns content, so the slot is
    filled in place with the recalled memory, not left literal."""
    body = chat_request(
        system_prompt="Global rules.\n\n{{context_quilt}}\n\nAnswer the user.",
        context_quilt=True,
    )
    resp = client_with_cq.post("/v1/chat", json=body, headers=pro_user["headers"])
    assert resp.status_code == 200
    mock_provider.assert_called_once()
    sent = mock_provider.call_args.args[0]
    assert "{{context_quilt}}" not in sent.system_prompt
    # Recalled context landed where the placeholder was.
    assert "User prefers concise answers" in sent.system_prompt


# --- memory capability line (the #431 pattern, for memory) ---

def test_memory_capability_line_when_flag_absent(client, pro_user, mock_provider):
    """Live 2026-07-15: SS follow-up sends drop the context_quilt flag
    even with the chip on; the model told Scott it had 'no access to
    Context Quilt'. Meeting Memory users now get a steering line on
    chat turns that arrive without the flag."""
    from tests.conftest import chat_request

    r = client.post("/v1/chat", json=chat_request(
        user_content="Use context quilt to find past interactions with Mike",
        metadata={"prompt_mode": "PostMeetingChat",
                  "call_type": "meeting_chat_follow_up"},
    ), headers=pro_user["headers"])
    assert r.status_code == 200
    sent = mock_provider.await_args_list[-1].args[0]
    assert "MEMORY CAPABILITY" in sent.system_prompt
    assert "NOT included in this conversation" in sent.system_prompt


def test_no_memory_line_when_recall_ran(client, pro_user, mock_provider, mock_cq):
    """With the flag on, recall speaks for itself — the line would lie
    ('not included') when the block IS included."""
    from tests.conftest import chat_request

    r = client.post("/v1/chat", json=chat_request(
        user_content="What do you remember about Mike?",
        context_quilt=True,
        metadata={"prompt_mode": "PostMeetingChat",
                  "call_type": "meeting_chat"},
    ), headers=pro_user["headers"])
    assert r.status_code == 200
    sent = mock_provider.await_args_list[-1].args[0]
    assert "MEMORY CAPABILITY" not in sent.system_prompt
    assert "CONTEXT FROM PREVIOUS MEETINGS" in sent.system_prompt


def test_no_memory_line_below_tier_or_off_surface(client, free_user, mock_provider):
    """Free tier has Meeting Memory disabled in the matrix — a static
    line would lie to them. And non-chat surfaces never get it."""
    from tests.conftest import chat_request

    r = client.post("/v1/chat", json=chat_request(
        user_content="Use context quilt for past meetings",
        metadata={"prompt_mode": "PostMeetingChat",
                  "call_type": "meeting_chat_follow_up"},
    ), headers=free_user["headers"])
    assert "MEMORY CAPABILITY" not in \
        mock_provider.await_args_list[-1].args[0].system_prompt

    r2 = client.post("/v1/chat", json=chat_request(
        user_content="Summarize the meeting",
        metadata={"call_type": "analysis"},
    ), headers=free_user["headers"])
    assert r2.status_code == 200
    assert "MEMORY CAPABILITY" not in \
        mock_provider.await_args_list[-1].args[0].system_prompt
