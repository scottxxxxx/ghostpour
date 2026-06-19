"""Server-side strip of stray ```json code fences on managed JSON responses.

Models wrap JSON in ```json fences despite "no code fences" instructions.
GP unwraps it on the non-stream path — but only when the whole response is a
fenced block wrapping valid JSON, so prose/markdown is never touched.
"""

from app.routers.chat import _strip_json_code_fence
from tests.conftest import chat_request


# --- unit: the helper ----------------------------------------------------

def test_unwraps_json_fence_with_lang_tag():
    assert _strip_json_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_unwraps_bare_fence():
    assert _strip_json_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'


def test_leaves_raw_json_untouched():
    assert _strip_json_code_fence('{"a": 1}') == '{"a": 1}'


def test_leaves_prose_with_inner_code_block_untouched():
    # A markdown answer that merely contains a code block must not be mangled.
    md = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert _strip_json_code_fence(md) == md


def test_leaves_non_json_fence_untouched():
    md = '```\njust some text, not json\n```'
    assert _strip_json_code_fence(md) == md


def test_leaves_markdown_brief_untouched():
    brief = '# Interviewer Brief: Jane Doe\n\n- VP of Eng\n- probes on system design'
    assert _strip_json_code_fence(brief) == brief


def test_empty_and_none_safe():
    assert _strip_json_code_fence("") == ""
    assert _strip_json_code_fence(None) is None


# --- integration: wired into the non-stream chat response ----------------

def test_chat_response_unwraps_fenced_json(client, free_user, mock_provider):
    mock_provider.return_value.text = '```json\n{"result": "ok"}\n```'
    resp = client.post(
        "/v1/chat",
        json=chat_request(call_type="tr_match_analysis"),
        headers=free_user["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == '{"result": "ok"}'
