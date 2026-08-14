"""The generation lane needs room for the build, not just the reply.

2026-08-14, same meeting-chat workbook ask on three models:

    Opus 4.7   9,255 output tokens   (omitting `thinking` means no
                                      thinking on that model, so the
                                      whole budget went to the answer)
    Sonnet 5  15,395 output tokens   thinks by default; finished with
                                      605 tokens to spare
    Opus 5    16,238 output tokens   stop_reason=max_tokens, cut off
                                      mid tool call, NO file emitted

The 16,000 floor predated thinking-by-default models. When it binds, the
failure is quiet in the worst way: the turn returns 200, the assistant
writes a plausible reply, and the artifact the user asked for simply is
not there, because generation dies inside a code-execution call.

The ceiling is safe to raise because this lane streams (chat dispatches
through route_stream_with_fallback, and send_request_stream sets
stream=True upstream), so the ~16K guidance that keeps NON-streaming
requests under the HTTP timeout does not apply here.
"""

from __future__ import annotations

import re


SRC = "app/services/providers/anthropic.py"


def _floor() -> int:
    src = open(SRC).read()
    m = re.search(r'body\["max_tokens"\]\s*=\s*max\(\s*body\.get\("max_tokens"\)\s*or\s*0,\s*(\d+)\s*\)', src)
    assert m, "the generation max_tokens floor moved or changed shape"
    return int(m.group(1))


def test_generation_floor_clears_the_observed_need():
    """Opus 5 hit the cap at 16,238 tokens and was still mid-build, so the
    floor has to clear that with real headroom, not squeak past it."""
    floor = _floor()
    assert floor >= 32000, (
        f"generation max_tokens floor is {floor}; Opus 5 was truncated at "
        "16,238 tokens while still building, so anything near that ceiling "
        "silently drops the artifact"
    )


def test_the_floor_is_a_floor_not_an_override():
    """A caller asking for more must keep it — max(), never a bare assign."""
    src = open(SRC).read()
    assert 'max(body.get("max_tokens") or 0,' in src, (
        "the floor must not clobber a larger caller-supplied max_tokens")


def test_generation_lane_still_streams():
    """The whole justification for the raised ceiling is that this lane
    streams. If chat stops streaming, the ceiling needs revisiting."""
    chat = open("app/routers/chat.py").read()
    assert "route_stream_with_fallback" in chat
    prov = open(SRC).read()
    assert 'body["stream"] = True' in prov
