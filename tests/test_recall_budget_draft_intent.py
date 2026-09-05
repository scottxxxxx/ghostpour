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


# --- the echo (check-the-echo rule) ---------------------------------------

def test_the_echo_names_the_budget_sent_and_whether_the_classifier_fired():
    from app.services.features.context_quilt_hook import recall_echo
    draft = _req("draft the follow up email")
    assert recall_echo(_build_recall_metadata(draft), draft) == {"token_budget": 300, "draft_intent": True}
    plain = _req("what are the current blockers")
    assert recall_echo(_build_recall_metadata(plain), plain) == {"token_budget": 1200, "draft_intent": False}
    meeting = _req("how do I handle Dana", prompt_mode="PostMeetingChat")
    assert recall_echo(_build_recall_metadata(meeting), meeting) == {"token_budget": None, "draft_intent": False}


def test_the_response_model_carries_the_echo_additively():
    from app.models.chat import ChatResponse
    r = ChatResponse(text="x", model="m", provider="p")
    assert r.recall is None and "recall" in r.model_dump()


def test_the_hook_stores_the_echo_before_and_sets_it_after():
    src = open("app/services/features/context_quilt_hook.py").read()
    assert src.count('result["recall_echo"] = recall_echo(cq_metadata, body)') == 2
    i = src.index("async def after_llm(")
    assert "response.recall = echo" in src[i:i + 1500] and "recall_echo request_id=%s token_budget=%s draft_intent=%s" in src[i:i + 1500]
    assert src[i:i + 1500].index("response.recall = echo") < src[i:i + 1500].index('if feature_state != "enabled" or not body.context_quilt:')


def test_the_log_line_carries_the_middleware_request_id_not_the_app_id():
    from app.request_context import current_app_id, current_request_id
    from app.services.features.context_quilt_hook import _request_id
    current_app_id.set("shouldersurf"); current_request_id.set("abc123def456")
    assert _request_id() == "abc123def456"
    mw = open("app/middleware/request_logging.py").read()
    assert "current_request_id.set(request_id)" in mw
    assert mw.index("current_app_id.set(app_id)") < mw.index("current_request_id.set(request_id)")
