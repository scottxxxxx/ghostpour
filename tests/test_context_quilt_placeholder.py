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
