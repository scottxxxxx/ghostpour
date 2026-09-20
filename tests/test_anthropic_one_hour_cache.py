"""The N-400 interviewer's system prompt is cached for one hour.

Prod, 2026-09-20: the lane's first turn waited 9.7s for the stream to open
and every later turn about 1.3s. The default cache lives five minutes and
the prompt is about 24k tokens, so every session met it cold.
"""
from app.models.chat import ChatRequest
from app.services.providers.anthropic import _ONE_HOUR_CACHE_CALL_TYPES, _build_system_blocks

ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}
FIVE_MIN = {"type": "ephemeral"}


def _req(call_type=None, system="S" * 5000, **meta):
    md = dict(meta)
    if call_type:
        md["call_type"] = call_type
    return ChatRequest(provider="anthropic", model="claude-sonnet-5", system_prompt=system,
                       user_content="hi", metadata=md or None)


def test_the_interviewer_lane_gets_one_hour():
    (block,) = _build_system_blocks(_req("n400_interviewer_turn"))
    assert block["cache_control"] == ONE_HOUR


def test_every_other_lane_keeps_five_minutes():
    # The dearer write is only worth paying where a person waits on a cold,
    # large, byte stable prompt. Nothing else moves without a decision.
    for ct in (None, "chat", "report", "n400_interview_turn", "generation_intent"):
        (block,) = _build_system_blocks(_req(ct))
        assert block["cache_control"] == FIVE_MIN, ct
    assert _ONE_HOUR_CACHE_CALL_TYPES == {"n400_interviewer_turn"}


def test_a_request_with_no_metadata_does_not_crash():
    (block,) = _build_system_blocks(_req())
    assert block["cache_control"] == FIVE_MIN


def test_with_a_recall_split_the_longer_entry_comes_first():
    # Anthropic rejects a one hour entry placed after a five minute one.
    system = "PREFIX " * 400 + "RECALL-TEXT" + " SUFFIX" * 50
    blocks = _build_system_blocks(_req("n400_interviewer_turn", system=system,
                                       cq_recall_block="RECALL-TEXT"))
    ttls = [b.get("cache_control", {}).get("ttl") for b in blocks if "cache_control" in b]
    assert ttls == ["1h", None]
    assert "".join(b["text"] for b in blocks) == system, "the split must not change the prompt"
