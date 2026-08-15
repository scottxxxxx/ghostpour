"""The contract lane, wired but dark.

Merging this changes nothing for anybody. The canary list is empty by
default, so every user keeps the provider sandbox lane byte for byte
until an identity is added to documents.generation.contract_lane_users.
Rollback is a config edit rather than a revert, and the blast radius of
a first live run is one account.

The lane itself is the template lane's shape (the model emits structure,
we draw the file) with one difference that carries the measured value:
the columns ride as a TOOL SCHEMA rather than a prompt, so a missing
expected-result field is an API-level rejection instead of a hole that
renders into a workbook looking complete. Three identical runs per model
2026-08-15: freeform put that column in 1 of 3, contracted 3 of 3.
"""

from __future__ import annotations

import pathlib

import pytest

from app.models.chat import ChatRequest
from app.services import generation_offers
from app.services.artifact_routing import contract_candidate
from app.services.artifact_types import CONTRACTS
from app.services.document_generation import contract_lane_enabled

CHAT_SRC = (pathlib.Path(__file__).resolve().parents[1]
            / "app/routers/chat.py").read_text()


def test_the_lane_is_dark_until_an_identity_is_listed() -> None:
    assert contract_lane_enabled({}, {"anyone"}) is False
    cfg = {"client-config": {"documents": {"generation": {}}}}
    assert contract_lane_enabled(cfg, {"anyone"}) is False
    cfg2 = {"client-config": {"documents": {"generation": {
        "contract_lane_users": ["canary"]}}}}
    assert contract_lane_enabled(cfg2, {"canary"}) is True
    assert contract_lane_enabled(cfg2, {"someone-else"}) is False


def test_the_shipped_config_lists_nobody() -> None:
    """A canary that ships switched on is not a canary."""
    import json
    ent = json.loads(
        (pathlib.Path(__file__).resolve().parents[1]
         / "config/remote/entitlements.json").read_text())
    gen = ((ent.get("documents") or {}).get("generation") or {})
    assert not gen.get("contract_lane_users")


def test_the_artifact_survives_from_offer_to_confirm() -> None:
    """The confirm send carries chat history, not the original ask, so
    the classifier's read has to be stored when it happens."""
    oid = generation_offers.create(
        "u1", "xlsx", "gist", artifact_id="risk_register")
    offer = generation_offers.take("u1", oid)
    assert offer["artifact_id"] == "risk_register"


def test_a_template_match_beats_a_contract() -> None:
    """The Gantt registry is purpose built and better than anything
    generic, and an ambiguous plan ask owes the user its version
    question before any build."""
    intent = {"artifact": "action_register", "artifact_confidence": "high"}
    assert contract_candidate(True, intent) == "action_register"
    assert contract_candidate(True, intent, skip=True) is None


def test_a_low_confidence_read_is_not_stored() -> None:
    """Low means two artifacts fit equally. Silently picking one at
    offer time hides a question we should have asked."""
    assert contract_candidate(
        True, {"artifact": "budget", "artifact_confidence": "low"}) is None


def test_the_request_model_can_carry_a_tool_schema() -> None:
    """Only the Anthropic adapter reads these, which is enough: every
    lane that sets them is already gated to that provider."""
    r = ChatRequest(provider="anthropic", model="m", user_content="x",
                    tools=[{"name": "emit_artifact"}],
                    tool_choice={"type": "auto"})
    assert r.tools[0]["name"] == "emit_artifact"
    assert ChatRequest(provider="anthropic", model="m",
                       user_content="x").tools is None


def test_the_adapter_appends_tools_rather_than_replacing_them() -> None:
    """A search-enabled turn must keep its own tool."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app/services/providers/anthropic.py").read_text()
    assert 'body.setdefault("tools", []).extend(request.tools)' in src


def test_the_lane_is_checked_before_the_sandbox_default() -> None:
    """Source-level guard: the sandbox branch is the catch-all, so a
    contract that resolved after it would never build."""
    contract = CHAT_SRC.index("if _gen_armed and _contract_id:")
    sandbox = CHAT_SRC.index('body = body.model_copy(update={"generation": True})')
    assert contract < sandbox


def test_the_artifact_call_carries_its_own_call_type() -> None:
    """Without it the artifact absorbs into the surface's chat call type
    and is invisible in cost and timing curves, and the dedicated model
    dial can never resolve."""
    assert '"call_type": "artifact_generation"' in CHAT_SRC


def test_the_build_records_which_artifact_it_was() -> None:
    """The only honest answer to which artifacts real users generate is
    usage, and it is unrecoverable for as long as it is unrecorded."""
    assert '_gmeta["artifact_type"] = _contract_id' in CHAT_SRC


@pytest.mark.parametrize("name,contract", sorted(CONTRACTS.items()))
def test_every_contract_promises_a_measured_duration(name, contract) -> None:
    """expected_seconds reaches the user as literal text. The served
    default of 150 promised the same wait for a 22 second open-questions
    log and a 187 second test plan."""
    assert 20 <= contract.expected_seconds <= 300, name
    assert contract.expected_seconds != 150 or name == "test_plan"


def test_a_render_failure_falls_back_instead_of_killing_the_turn() -> None:
    assert "contract lane failed, serving raw text" in CHAT_SRC
    assert "contract lane: model declared computation" in CHAT_SRC
