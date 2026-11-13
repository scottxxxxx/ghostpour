"""ConversationHint: one line to say now in a non interview practice (TR
contract docs/tr_conversation_hint-contract.md at c73bd1a; Scott approved
2026-11-12 and confirmed in chat 2026-11-13).

A mode under tr_mock_interview beside InterviewHint. TR authored the prompt and
asked GP to host it verbatim, plain text out, maxTokens 100, thinking off; the
client refuses any response that stops on max_tokens, so the cap is part of
the contract, not a tuning knob.
"""
import json
import pathlib

from app.services.prompt_assembly import assemble_prompt

CFG = pathlib.Path("config/remote/techrehearsal/mock-interview.json")


def _cfg():
    return json.loads(CFG.read_text())


def _mode():
    return _cfg()["modes"]["ConversationHint"]


def test_the_mode_carries_the_contract_settings():
    m = _mode()
    assert m["maxTokens"] == 100
    assert m["thinking"] == "disabled"


def test_the_prompt_is_tr_s_text():
    p = " ".join(_mode()["systemPrompt"].split())
    assert p.startswith("You write one line for someone rehearsing a real conversation.")
    for rule in ("Exactly one sentence, 25 words or fewer.",
                 "It answers THE LINE ON SCREEN.",
                 "Never invent a fact, number, price, date, name or promise that the input does not support.",
                 "never write \"you should\"",
                 "hardConversation, repairConversation or protectConversation",
                 "no quote marks around it, no label, no preamble, no markdown"):
        assert rule in p, rule
    assert "—" not in p and "–" not in p and " - " not in p


def test_the_mode_resolves_to_its_own_prompt_not_the_question_generator():
    """If the mode did not resolve, assembly would fall through to the base
    question generator and return JSON, which the client discards."""
    cfgs = {"techrehearsal/mock-interview": _cfg()}
    r = assemble_prompt("tr_mock_interview", "THE LINE ON SCREEN (they just said this, the line answers it):\nHi",
                        cfgs, prompt_mode="ConversationHint", scenario_kind="purchaseNegotiation")
    assert r["system_prompt"].startswith("You write one line for someone rehearsing")
    assert r["max_tokens"] == 100
    other = assemble_prompt("tr_mock_interview", "x", cfgs, prompt_mode="InterviewHint")
    assert not other["system_prompt"].startswith("You write one line"), "InterviewHint untouched"
