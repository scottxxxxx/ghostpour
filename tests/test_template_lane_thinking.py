"""Template extraction runs with thinking off.

Extraction is transcription with a schema, not deliberation, and on a
model that thinks by default the reasoning shares `max_tokens` with the
JSON we actually want. Measured on the demo standups against Sonnet 5,
two runs each, 2026-07-30:

    thinking on   87s, 108s   $0.11 to $0.12   one run burned all 12000
                                               tokens and returned nothing
    thinking off  20s,  23s   $0.036 to $0.040 same 9 tasks, same stated
                                               percents, same commitments

So the lane declares `thinking: "disabled"` in the registry, and the
template lane hands it to the request. The failure mode this closes is
not slowness: it is a Pro user confirming a build and getting nothing.
"""

from __future__ import annotations

import pytest

from app.services.doc_templates import TEMPLATES


@pytest.mark.parametrize("template_id", ["gantt_smartsheet", "gantt_detailed"])
def test_extraction_templates_declare_thinking_off(template_id):
    assert TEMPLATES[template_id]["thinking"] == "disabled"


@pytest.mark.parametrize("template_id", ["gantt_smartsheet", "gantt_detailed"])
def test_served_expectation_matches_the_measured_lane(template_id):
    """The offer promises a duration out loud; it has to track what the
    lane actually does now, not what it did with thinking on."""
    assert TEMPLATES[template_id]["expected_seconds"] <= 30


def test_request_carries_the_disabled_block_on_a_thinking_model():
    """End of the chain: registry value reaches the outgoing body."""
    from app.models.chat import ChatRequest
    from tests.test_anthropic_cache_split import _adapter

    req = ChatRequest(
        provider="anthropic", model="claude-sonnet-5",
        system_prompt="extract the plan", user_content="build me a gantt",
        max_tokens=TEMPLATES["gantt_detailed"]["max_tokens"],
        thinking=TEMPLATES["gantt_detailed"]["thinking"],
    )
    body, _ = _adapter()._build_body(req)
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 12000


def test_older_models_omit_the_block_rather_than_send_it():
    from app.models.chat import ChatRequest
    from tests.test_anthropic_cache_split import _adapter

    req = ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="extract the plan", user_content="build me a gantt",
        thinking=TEMPLATES["gantt_detailed"]["thinking"],
    )
    body, _ = _adapter()._build_body(req)
    assert "thinking" not in body
