"""{{cache_break}}: the stable head of an assembled user message caches.

Steven's 11-13 N-400 export: every interviewer turn billed about 5,000
uncached input tokens beside a 41.8k cached system prompt, because the user
message had no cache breakpoint and opened with TURN ID. v67 puts the stable
blocks first and ends them with {{cache_break}}; the Anthropic adapter caches
that head as its own block.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.models.chat import ChatRequest
from app.services.prompt_assembly import CACHE_BREAK, assemble_prompt
from app.services.providers.anthropic import AnthropicAdapter
from tests.conftest import chat_request

ROOT = Path(__file__).resolve().parent.parent
CFG = {
    "systemPrompt": "sys",
    "userPromptTemplate": "HEAD {{a}}\n" + CACHE_BREAK + "\nTAIL {{b}}\n{{user_input}}",
    "requiredVariables": ["a", "b"],
}


def _assemble(cfg=CFG, user="hi", **variables):
    return assemble_prompt("n400_interviewer_turn", user, {"n400/interviewer-turn": cfg},
                           variables={"a": "1", "b": "2", **variables})


def _adapter():
    return AnthropicAdapter(api_key="t", base_url="https://api.anthropic.com/v1/messages",
                            auth_header="x-api-key", auth_prefix="")


def _user_parts(**kw):
    req = ChatRequest(provider="anthropic", model="claude-sonnet-5", system_prompt="sys",
                      metadata={"call_type": "n400_interviewer_turn"}, **kw)
    body, _ = _adapter()._build_body(req)
    return body["messages"][0]["content"]


# --- assembly -------------------------------------------------------------------

def test_assembly_marks_where_the_head_ends_and_removes_the_marker():
    out = _assemble()
    assert out is not None
    text, n = out["user_content"], out["user_cache_prefix_chars"]
    assert "cache_break" not in text and "\x00" not in text
    assert text[:n] == "HEAD 1\n" and text[n:] == "\nTAIL 2\nhi"


def test_a_client_value_carrying_the_marker_cannot_move_the_split():
    out = _assemble(a="evil " + CACHE_BREAK + " text")
    text, n = out["user_content"], out["user_cache_prefix_chars"]
    assert text[:n] == "HEAD evil " + CACHE_BREAK + " text\n"


def test_a_template_without_the_marker_has_no_split():
    out = _assemble(cfg={**CFG, "userPromptTemplate": "HEAD {{a}} {{b}} {{user_input}}"})
    assert "user_cache_prefix_chars" not in out


# --- the adapter ----------------------------------------------------------------

def test_the_head_is_its_own_cached_block():
    parts = _user_parts(user_content="HEADtail", user_cache_prefix_chars=4)
    assert [p["text"] for p in parts] == ["HEAD", "tail"]
    assert parts[0]["cache_control"]["type"] == "ephemeral"
    assert "cache_control" not in parts[1]


def test_no_split_without_a_prefix():
    parts = _user_parts(user_content="HEADtail")
    assert parts == [{"type": "text", "text": "HEADtail"}]


def test_no_split_when_references_already_spend_the_breakpoints():
    parts = _user_parts(user_content="HEADtail", user_cache_prefix_chars=4,
                        reference_text="ref")
    assert parts[-1] == {"type": "text", "text": "HEADtail"}


# --- the router -----------------------------------------------------------------

def test_a_client_sent_prefix_never_reaches_the_provider(client, free_user, mock_provider):
    body = chat_request(user_cache_prefix_chars=3)
    r = client.post("/v1/chat", json=body, headers=free_user["headers"])
    assert r.status_code == 200, r.text
    assert mock_provider.call_args.args[0].user_cache_prefix_chars is None


# --- the shipped template -------------------------------------------------------

def test_v67_head_holds_only_stable_blocks_and_turn_id_sits_last():
    t = json.loads((ROOT / "config/remote/n400/interviewer-turn.json").read_text())["userPromptTemplate"]
    head, tail = t.split(CACHE_BREAK)
    for per_turn in ("turn_id", "known_facts", "agenda", "conversation", "user_input"):
        assert "{{%s}}" % per_turn not in head, per_turn
    assert tail.index("{{turn_id}}") > tail.index("{{spoken_numerals}}")
    assert tail.rstrip().endswith("{{user_input}}")
