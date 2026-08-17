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


def test_the_explicit_command_fast_path_can_reach_the_lane() -> None:
    """Measured live 2026-08-16, the first real device run: the canary
    was on and the build still went through the provider sandbox.

    `explicit_file_ask` is a regex that fires on an imperative verb plus
    a named format ("...create the spreadsheet"), and its hit takes the
    fast path, which arms generation immediately and mints NO offer.
    `artifact_id` is only ever stamped on an offer, so the contract lane
    was unreachable from the phrasings most likely to want it: vague
    asks routed to it, explicit ones could not, and explicit is the
    common case. The regex knows a file was asked for and never which
    artifact, so the branch has to buy that answer from the classifier.
    """
    fast = CHAT_SRC.index('logger.info(\n                            '
                          '"generation_fast_path armed=1')
    lookup = CHAT_SRC.index("contract_fast_path_lookup")
    arm = CHAT_SRC.index("if _gen_armed and _contract_id:")
    # Resolved inside the fast path, before the lane is read.
    assert lookup < fast < arm
    assert "if _contract_lane_on and _intent.get(\"format\") == \"xlsx\":" in CHAT_SRC


def test_a_contract_is_never_named_for_a_non_xlsx_ask() -> None:
    """Every contract renders an xlsx. Naming one for a Word ask answers
    the request with the wrong file type: artifact recognised, format
    silently overridden, user handed a confidently wrong document."""
    intent = {"artifact": "action_register", "artifact_confidence": "high"}
    assert contract_candidate(True, intent, fmt="xlsx") == "action_register"
    assert contract_candidate(True, intent, fmt="docx") is None
    assert contract_candidate(True, intent, fmt="pdf") is None
    # None means "not checked" — unchanged for callers with no format.
    assert contract_candidate(True, intent, fmt=None) == "action_register"


def test_both_lane_entry_points_pass_the_format_through() -> None:
    """The guard is worthless on a call site that omits it."""
    assert CHAT_SRC.count("fmt=_intent.get(\"format\")") == 2


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


def test_a_contract_can_demand_cross_meeting_context() -> None:
    """The topic tracker is the one artifact nobody else can build, and
    it is worthless without it. Measured 2026-08-15: on single-meeting
    input every row came back times_discussed=1 with first_raised equal
    to last_discussed. CQ serves the meeting-grouped dossier only for a
    project-scoped rundown ask, and ZERO of 24 real topic-tracker
    phrasings trip that detector, so the contract declares the need
    instead of hoping the ask looks like a rundown."""
    assert CONTRACTS["topic_tracker"].needs_dossier is True
    assert [n for n, c in CONTRACTS.items() if c.needs_dossier] == [
        "topic_tracker"]
    assert "contract_lane_dossier" in CHAT_SRC
    assert "contract lane dossier fetch failed" in CHAT_SRC


def test_the_dossier_fetch_falls_open() -> None:
    """A thin artifact beats a dead turn."""
    i = CHAT_SRC.index("contract lane dossier fetch failed")
    assert "except Exception" in CHAT_SRC[i - 400:i]


def test_the_tracker_ships_counts_and_dates_not_verdicts() -> None:
    """CQ's review 2026-08-15, and they were right twice.

    A movement column (Progressing / Stalled / Reopened / Resolved /
    Escalating) was a parallel vocabulary for their item ledger, whose
    modes carry a headline plus every other applicable mode plus
    patch_ids_by_mode so every count opens into its patches. And
    "Escalating" had no observation under it: if it rested on mention
    volume, they measured volume near level across people whose
    follow-through was opposite, so the claim contradicts the data.

    Their rule now binds this contract: ship the count never the cause,
    instances never traits, no ratio at any denominator.
    """
    cols = CONTRACTS["topic_tracker"].columns
    keys = {c.key for c in cols}
    assert "movement" not in keys
    assert "gap_days" in keys

    # No column may OFFER a trajectory as a value. Naming one inside a
    # prohibition is the opposite of the defect, so this checks the
    # enumerations rather than blocklisting words.
    for c in cols:
        desc = (c.description or "").lower()
        if "one of:" in desc:
            enum = desc.split("one of:", 1)[1]
            for verdict in ("escalating", "stalled", "progressing",
                            "at risk", "losing momentum"):
                assert verdict not in enum, f"{c.key} offers {verdict!r}"

    # And the free-text column has to forbid grading outright.
    status = next(c for c in cols if c.key == "status")
    assert "do not grade" in (status.description or "").lower()


def test_the_count_carries_its_definition_on_the_wire() -> None:
    """A cue count observes meetings that left a trace, not meetings
    where a thing was discussed. That distinction goes next to the
    number, not in a doc nobody opens."""
    col = next(c for c in CONTRACTS["topic_tracker"].columns
               if c.key == "times_discussed")
    assert "memory" in col.label.lower()
    assert "floor" in col.description.lower()


def test_the_counting_artifact_asks_for_everything() -> None:
    """The cap was OURS, not CQ's. Their limit is caller-supplied and
    absent by default, so sending one is what makes them truncate.
    Measured on the wire 2026-08-17: Scott's quilt reports
    total_available 2136 against the 500 we were asking for, so a
    COUNTING artifact was counting from under a quarter of the material
    and putting a confident number in a cell.

    Asserts the outbound REQUEST rather than the source line that builds
    it: the previous version of this test checked `"limit=500" in
    CHAT_SRC`, which proves a literal was typed and would have passed
    just as happily against a fetch nobody ever called.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.services import context_quilt as cq

    get = AsyncMock()
    get.return_value = type("R", (), {
        "status_code": 200, "json": lambda self: {"meetings": []},
        "raise_for_status": lambda self: None})()
    with patch.object(cq, "get_settings",
                      lambda: type("S", (), {"cq_base_url": "http://cq.test",
                                             "cq_dossier_timeout_ms": 2000})()), \
         patch.object(cq, "_get_client", lambda: type("C", (), {"get": get})()), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        asyncio.run(cq.quilt_dossier("u1", "p1", limit=None))

    params = get.call_args.kwargs["params"]
    assert "limit" not in params, (
        f"a limit was sent, so CQ will truncate: {params}")
    assert params["group_by"] == "origin"


def test_ordinary_recall_still_carries_a_cap() -> None:
    """Removing the cap globally to fix one surface was the wrong trade:
    unbounded context on every CQ-backed call is a cost on every turn.
    Only the counting artifact opts out."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.services import context_quilt as cq

    get = AsyncMock()
    get.return_value = type("R", (), {
        "status_code": 200, "json": lambda self: {"meetings": []},
        "raise_for_status": lambda self: None})()
    with patch.object(cq, "get_settings",
                      lambda: type("S", (), {"cq_base_url": "http://cq.test",
                                             "cq_dossier_timeout_ms": 2000})()), \
         patch.object(cq, "_get_client", lambda: type("C", (), {"get": get})()), \
         patch.object(cq, "_get_auth_headers", AsyncMock(return_value={})):
        asyncio.run(cq.quilt_dossier("u1", "p1"))   # default caller

    assert get.call_args.kwargs["params"]["limit"] == cq.DOSSIER_LIMIT


def test_a_truncated_dossier_says_so_with_cqs_own_numbers() -> None:
    """The old disclosure inferred truncation from `total >= limit`,
    which can only ever say "possibly" and says it wrongly when the
    count lands on the cap by coincidence. CQ now returns `truncated`
    and `total_available` counted BEFORE the cap, which is the real
    denominator."""
    from app.services import context_quilt as cq

    block = cq.format_dossier({
        "meetings": [{"patches": [
            {"patch_id": "p1", "created_at": "2026-08-01T00:00:00Z",
             "text": "a thing"}]}],
        "truncated": True, "total_available": 2136,
    }, limit=None)
    assert "2136" in block, block[:300]
    assert "FLOOR" in block.upper()


def test_a_project_less_turn_can_still_pull_multi_meeting_memory() -> None:
    """project_id is a filter, not the boundary."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app/services/context_quilt.py").read_text()
    assert '**({"project_id": project_id} if project_id else {})' in src


def test_the_tool_schema_actually_reaches_the_wire() -> None:
    """CQ's post-mortem 2026-08-15, applied to us.

    Their entity extraction fell from 4.37 entities per meeting to 1.24
    on the day of an Anthropic-direct cutover, because the client
    accepted a json_schema and did not put it on the wire, and their
    prompt had never carried the contract. The schema was the ONLY thing
    specifying it, so when it silently vanished the output degraded and
    looked like a model problem for two months.

    This lane has the same shape: the columns live in the tool schema
    and the prompt only says they are already decided. So assert the
    schema is in the body the adapter builds, rather than trusting that
    setting request.tools is the same as sending them.
    """
    from app.models.chat import ChatRequest
    from app.services.artifact_types import TEST_PLAN, contract_tool_schema
    from app.services.providers.anthropic import AnthropicAdapter

    schema = contract_tool_schema(TEST_PLAN)
    req = ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="sys", user_content="build it",
        tools=[{"name": "emit_artifact", "description": "d",
                "input_schema": schema}])
    adapter = AnthropicAdapter(
        api_key="test", base_url="https://api.anthropic.com/v1",
        auth_header="x-api-key", auth_prefix="")
    body, _headers = adapter._build_body(req)

    tools = body.get("tools") or []
    assert tools, "tools vanished between the request and the body"
    emitted = next(t for t in tools if t.get("name") == "emit_artifact")
    props = emitted["input_schema"]["properties"]["sheets"]["items"][
        "properties"]["rows"]["items"]
    # The columns themselves, not just the envelope.
    assert "expected" in props["required"], "the contract lost its columns"
    assert props["additionalProperties"] is False


def test_a_search_turn_keeps_its_own_tool_alongside_ours() -> None:
    """The adapter assigns body["tools"] for search. Appending rather
    than assigning is the whole reason both survive."""
    from app.models.chat import ChatRequest
    from app.services.providers.anthropic import AnthropicAdapter

    req = ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        user_content="x", metadata={"search_enabled": True},
        tools=[{"name": "emit_artifact", "input_schema": {"type": "object"}}])
    adapter = AnthropicAdapter(
        api_key="test", base_url="https://api.anthropic.com/v1",
        auth_header="x-api-key", auth_prefix="")
    body, _ = adapter._build_body(req)
    names = {t.get("name") or t.get("type") for t in body.get("tools") or []}
    assert "web_search" in names and "emit_artifact" in names, names


def test_the_prompt_names_the_columns_too() -> None:
    """Defense in depth, from CQ's post-mortem. A schema that silently
    stops reaching the wire should produce something recognisably wrong,
    not something plausible that degrades quietly for two months."""
    assert "The columns are: " in CHAT_SRC
    i = CHAT_SRC.index("The columns are: ")
    assert "col.label for col in _c.columns" in CHAT_SRC[i:i + 200]


def test_routing_never_reaches_the_wire_with_a_provider_prefix() -> None:
    """Measured live 2026-08-16, the first turn that ever reached the
    contract lane: `400 The requested model is not available`.

    `_resolve_model_routing` returns the config's own string, which is
    "<provider>/<model>" in every routing row we ship, and the model
    field goes onto the wire verbatim. Two of the three call sites split
    that prefix inline; the contract lane assigned the whole string, so
    Anthropic was asked for a model named
    "anthropic/claude-sonnet-4-6". Same resolution copied three times,
    one copy wrong.
    """
    from app.models.chat import ChatRequest
    from app.routers.chat import _apply_routed_model

    body = ChatRequest(provider="anthropic", model="old", user_content="x")

    routed = _apply_routed_model(body, "anthropic/claude-sonnet-4-6")
    assert routed.model == "claude-sonnet-4-6"
    assert routed.provider == "anthropic"

    # A bare value (no prefix) still applies, and a no-op stays a no-op.
    assert _apply_routed_model(body, "claude-opus-5").model == "claude-opus-5"
    assert _apply_routed_model(body, "old") is body
    assert _apply_routed_model(body, None) is body


def test_one_resolution_path_not_three() -> None:
    """The bug was three copies of the same four lines with one of them
    wrong. A fourth copy is the same bug waiting."""
    assert CHAT_SRC.count("_apply_routed_model(body, _resolve_model_routing(") == 3
    assert "_re_model" not in CHAT_SRC


def test_a_contract_build_does_not_take_the_plain_chat_stream() -> None:
    """Two symptoms, one omission, both live on 2026-08-16.

    `should_stream` excluded the generation and template lanes but not
    the contract lane, so a confirmed contract build went down the
    interactive chat stream. That path caps the stream at 180s while the
    test plan contract's own served expectation is 170s, and the build
    died at 181.0s having done the work. The same path emits no
    generation events, so the client showed its generic thinking
    indicator for three minutes with no sign a file was being built.
    """
    assert "and not _contract_id\n    )" in CHAT_SRC
    # And the build transport must recognise it, or the turn streams
    # nothing useful even once it is on the right road.
    assert "(body.generation or _template_id or _contract_id)" in CHAT_SRC


def test_the_adapter_gives_a_contract_turn_the_build_timeout() -> None:
    """The contract lane never sets `generation`; it ships a tool schema
    instead. Keyed on the flag alone it inherited the 180s client
    default, which is below the 170s we promise for a test plan."""
    from app.models.chat import ChatRequest
    from app.services.providers.anthropic import AnthropicAdapter as A

    plain = ChatRequest(provider="anthropic", model="m", user_content="x")
    sandbox = ChatRequest(provider="anthropic", model="m", user_content="x",
                          generation=True)
    contract = ChatRequest(provider="anthropic", model="m", user_content="x",
                           tools=[{"name": "emit_artifact"}])

    assert A._is_build(plain) is False
    assert A._is_build(sandbox) is True
    assert A._is_build(contract) is True


def test_the_raw_response_is_parsed_before_it_is_read() -> None:
    """`raw_response_json` is a JSON STRING — declared that way on
    ChatResponse, built by the adapter's _pretty_json. The sandbox lane
    has always parsed it before reading (document_generation
    ._walk_file_ids does json.loads); this lane read it as a dict.

    Every contract build therefore died on `'str' object has no
    attribute 'get'` at the very last step, AFTER the model had done the
    work. Measured live 2026-08-16: 183s, $0.1722, stop_reason tool_use,
    a valid emit_artifact call carrying 40 scenarios across 4 sheets —
    parsed, rendered and discarded by one missing json.loads.
    """
    import json as _json

    from app.models.chat import ChatResponse

    assert ChatResponse.model_fields["raw_response_json"].annotation in (
        str | None, "str | None")

    src = CHAT_SRC[CHAT_SRC.index("if _contract_id and response"):]
    src = src[:src.index("_emitted = next(")]
    assert "isinstance(_raw, str)" in src
    assert "_json.loads" in src or "json.loads" in src

    # The shape the adapter actually hands us round-trips to the block
    # the lane looks for.
    wire = _json.dumps({"content": [
        {"type": "tool_use", "name": "emit_artifact",
         "input": {"filename": "f.xlsx", "sheets": []}}]})
    parsed = _json.loads(wire)
    emitted = next((b.get("input") for b in (parsed.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"), None)
    assert emitted["filename"] == "f.xlsx"
