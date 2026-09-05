"""The recall block is sized to the intent: a draft ask gets the small block."""

from app.models.chat import ChatRequest
from app.services.features.context_quilt_hook import (
    DRAFT_RECALL_TOKEN_BUDGET, _build_recall_metadata, is_draft_intent,
)


def _req(user_content, prompt_mode="ProjectChat"):
    return ChatRequest(provider="anthropic", model="auto", user_content=user_content,
                       system_prompt="x", metadata={"prompt_mode": prompt_mode, "project_id": "p1"})


def test_draft_asks_are_recognised_in_the_served_locales():
    for q in ("draft the follow up email", "Write a message to Dana about the deadline",
              "can you compose a thank-you note", "reply to Marcus",
              "redacta un correo para el equipo", "rédige un courriel à Marcus",
              "メールを書いて"):
        assert is_draft_intent(q), q


def test_questions_that_are_not_drafts_keep_the_full_block():
    for q in ("how do I handle Dana", "what are the current blockers",
              "what did we decide about the steering prep", "who owns the gateway"):
        assert not is_draft_intent(q), q
    assert not is_draft_intent("") and not is_draft_intent(None)


def test_a_draft_ask_on_a_project_chat_sends_the_small_budget():
    meta = _build_recall_metadata(_req("draft the follow up email"))
    assert meta["token_budget"] == DRAFT_RECALL_TOKEN_BUDGET == 300


def test_a_non_draft_project_chat_keeps_the_scoped_budget():
    assert _build_recall_metadata(_req("what are the current blockers"))["token_budget"] == 1200


def test_a_non_draft_meeting_chat_sends_no_budget_and_a_draft_one_sends_the_small_one():
    assert "token_budget" not in _build_recall_metadata(_req("how do I handle Dana", prompt_mode="PostMeetingChat"))
    assert _build_recall_metadata(_req("write Dana a message", prompt_mode="PostMeetingChat"))["token_budget"] == 300


def test_the_question_portion_is_what_is_classified_not_the_transcript():
    """A meeting transcript that mentions 'send the email' inside the
    material must not turn a 'how do I handle Dana' ask into a draft."""
    from app.services.document_generation import _question_portion
    content = "Transcript:\n[Dana] please send the email today\n\nCurrent question: how do I handle Dana"
    q = _question_portion(content)
    assert "how do I handle Dana" in q
    assert not is_draft_intent(q)
