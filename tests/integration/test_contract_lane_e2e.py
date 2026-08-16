"""The contract lane, driven end to end through the real router.

WHY THIS FILE EXISTS. The contract lane shipped with a suite of tests
that asserted source TEXT ("assert '...' in CHAT_SRC"). Those prove a
line was written. They cannot prove the path runs, and five separate
defects reached a live device in a row, each surfacing only once the
previous fix let a run travel far enough to hit it:

  1. the explicit-command fast path minted no offer, so the lane was
     unreachable from the phrasings most likely to want it
  2. the routing value kept its "anthropic/" prefix, so the provider
     answered 400 "The requested model is not available"
  3. a string `readme` raised inside the renderer
  4. `should_stream` did not exclude the lane, so a build took the chat
     stream: a 180s cap against a served 170s promise, and no
     generation events, so the client showed no build at all
  5. `raw_response_json` is a JSON string and the lane read it as a
     dict, so a finished 40-scenario workbook was thrown away

Every one of those is caught by driving a confirmed turn through the
real app with only the provider stubbed. That is what this file does.
The rule it encodes: the contract lane is exercised, not read.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, patch

import openpyxl
import pytest

from app.models.chat import ChatResponse

# The wire shape the Anthropic adapter actually hands back: `content` is
# a list of blocks, and the whole thing arrives as a STRING on
# ChatResponse.raw_response_json. Copied from a real 2026-08-16 response
# (sonnet-4-6, 183s, $0.1722, stop_reason tool_use) and trimmed.
def _emit_artifact_response(sheets: list[dict] | None = None,
                            readme: object = None) -> ChatResponse:
    payload = {
        "id": "msg_01",
        "stop_reason": "tool_use",
        "content": [{
            "type": "tool_use",
            "id": "toolu_01",
            "name": "emit_artifact",
            "input": {
                "filename": "cigna_demo_test_scenarios.xlsx",
                "title": "Cigna Demo Test Scenarios",
                "needs_computation": False,
                "readme": readme if readme is not None else {
                    "purpose": "Scenarios for the demo build.",
                    "scope": "P1 intents only.",
                    "reviewers": ["Mike"],
                },
                "sheets": sheets if sheets is not None else [
                    {"name": "Eligibility", "rows": [
                        {"id": "ELG-01", "scenario": "Active member",
                         "type": "Happy path", "preconditions": "None",
                         "test_data": "CIG100001",
                         "expected": "Eligible - coverage returned",
                         "notes": "n/a"},
                        {"id": "ELG-02", "scenario": "Termed member",
                         "type": "Failure mode", "preconditions": "None",
                         "test_data": "CIG100002",
                         "expected": "Not eligible", "notes": ""},
                    ]},
                    {"name": "Refill - Caregiver", "rows": [
                        {"id": "RFL-01", "scenario": "Caregiver refill",
                         "type": "Edge case", "preconditions": "Linked",
                         "test_data": "CIG100003",
                         "expected": "Refill accepted", "notes": ""},
                    ]},
                ],
            },
        }],
        "usage": {"input_tokens": 4000, "output_tokens": 10255},
    }
    return ChatResponse(
        text="",
        input_tokens=4000,
        output_tokens=10255,
        model="claude-sonnet-4-6",
        provider="anthropic",
        usage={"input_tokens": 4000, "output_tokens": 10255},
        raw_request_json="{}",
        # THE POINT: a string, not a dict. Defect 5 lived here.
        raw_response_json=json.dumps(payload),
    )


@pytest.fixture
def contract_lane(client, pro_user):
    """Turn the canary on for this user, and make the classifier name
    the artifact without a network call."""
    from app.main import app

    cfg = app.state.remote_configs
    docs = cfg.setdefault("client-config", {}).setdefault("documents", {})
    gen = docs.setdefault("generation", {})
    before = gen.get("contract_lane_users")
    gen["contract_lane_users"] = [pro_user["user_id"]]

    intent = {"file_request": True, "format": "xlsx", "gist": "test scenarios",
              "artifact": "test_plan", "artifact_confidence": "high"}
    with patch("app.services.document_generation.classify_generation_intent",
               new_callable=AsyncMock, return_value=intent):
        yield client
    if before is None:
        gen.pop("contract_lane_users", None)
    else:
        gen["contract_lane_users"] = before


def _send(client, pro_user, *, stream: bool = False, text: str | None = None,
          **meta):
    """One turn, as iOS sends it.

    Note what is NOT set: `generation_confirmed`. An explicit ask
    ("create a test plan spreadsheet") arms the build on this very turn
    through the fast path, which is the road defect 1 made unreachable
    and the one most real asks take. The confirm round trip is covered
    separately below.
    """
    return client.post(
        "/v1/chat",
        json={
            "provider": "auto",
            "model": "auto",
            "user_content": text or "create a test plan spreadsheet from the meeting",
            # The client assembles the prompt; PostMeetingChat sends one.
            "system_prompt": "You are helping with a meeting.",
            "stream": stream,
            "metadata": {
                "prompt_mode": "PostMeetingChat",
                "call_type": "meeting_chat",
                **meta,
            },
        },
        headers=pro_user["headers"],
    )


def _events(resp) -> list[tuple[str, dict]]:
    """A confirmed build is answered as SSE on EVERY surface, streaming
    or not (handoff Part 2): started -> progress -> result. Parsing it
    is part of what the client has to do, so the test does it too."""
    out = []
    for chunk in resp.text.split("\n\n"):
        if not chunk.strip():
            continue
        name = data = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name:
            out.append((name, json.loads(data) if data else {}))
    return out


def _result_of(resp) -> dict:
    for name, data in _events(resp):
        if name == "generation_result":
            return data
    raise AssertionError(
        f"no generation_result in stream: {[n for n, _ in _events(resp)]}")


def _files_from(resp) -> list[dict]:
    return _result_of(resp).get("generated_files") or []


class TestContractLaneBuildsAFile:
    def test_a_confirmed_ask_returns_a_real_workbook(self, contract_lane, pro_user):
        """The whole point, in one assertion chain: ask, get xlsx bytes
        that openpyxl can read, with the rows the model emitted."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            resp = _send(contract_lane, pro_user, stream=False)

        assert resp.status_code == 200, resp.text
        files = _files_from(resp)
        assert files, f"no file was produced: {resp.text[:600]}"

        # Files are STAGED, not inlined: the envelope carries a
        # file_id the client fetches. Pull it the way the app does, so
        # the download path is covered too.
        row = files[0]
        assert row["name"].endswith(".xlsx"), row
        got = contract_lane.get(f"/v1/generated-files/{row['file_id']}",
                                headers=pro_user["headers"])
        assert got.status_code == 200, got.text
        wb = openpyxl.load_workbook(io.BytesIO(got.content))
        assert "README" in wb.sheetnames
        rows = sum(ws.max_row - 2 for ws in wb.worksheets if ws.title != "README")
        assert rows == 3

    def test_the_model_string_reaches_the_provider_without_its_prefix(
            self, contract_lane, pro_user):
        """Defect 2: the routing config's value is "<provider>/<model>"
        and the model field goes on the wire verbatim, so an unsplit
        value asked Anthropic for "anthropic/claude-sonnet-4-6" and got
        400 back."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()) as route:
            _send(contract_lane, pro_user, stream=False)

        sent = route.await_args.args[1]
        assert "/" not in sent.model, sent.model
        assert sent.provider == "anthropic"

    def test_the_turn_is_billed_as_artifact_generation(
            self, contract_lane, pro_user, tmp_db_path):
        """The dedicated call_type is what makes per-artifact cost and
        demand answerable at all. It is also the dial's only hook."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            _send(contract_lane, pro_user, stream=False)

        import sqlite3
        conn = sqlite3.connect(tmp_db_path)
        rows = conn.execute(
            "SELECT call_type, metadata FROM usage_log ORDER BY rowid DESC"
        ).fetchall()
        conn.close()
        kinds = [r[0] for r in rows]
        assert "artifact_generation" in kinds, kinds
        metas = [json.loads(r[1] or "{}") for r in rows
                 if r[0] == "artifact_generation"]
        assert any(m.get("artifact_type") == "test_plan" for m in metas), metas


class TestContractLaneTransport:
    def test_a_streamed_build_announces_itself(self, contract_lane, pro_user):
        """Defect 4. A build must not ride the interactive chat stream:
        that path caps at 180s (below the 170s we promise for this very
        contract) and emits no generation events, so the client has
        nothing to render a build from and shows a plain thinking
        indicator for three minutes."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            resp = _send(contract_lane, pro_user, stream=True)

        assert resp.status_code == 200
        body = resp.text
        assert "event: generation_started" in body, body[:400]
        started = json.loads(
            body.split("event: generation_started\ndata: ")[1].split("\n")[0])
        # The served expectation is the contract's own, not the flat 150.
        assert started["expected_seconds"] == 170, started


class TestContractLaneDegradesHonestly:
    @pytest.mark.parametrize("readme", ["a plain paragraph", None, ["a", "b"]])
    def test_an_odd_readme_shape_still_yields_a_file(
            self, contract_lane, pro_user, readme):
        """Defect 3. The contract declares readme an object, but a tool
        schema is advisory unless the tool is strict, and losing the
        whole workbook over the cover sheet is the worst trade here."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response(readme=readme)):
            resp = _send(contract_lane, pro_user, stream=False)
        assert resp.status_code == 200
        assert _files_from(resp), resp.text[:400]

    def test_no_tool_call_falls_back_to_text_rather_than_500(
            self, contract_lane, pro_user):
        """The model answering in prose is a miss, not an outage."""
        plain = ChatResponse(
            text="Here is a plan in chat instead.",
            input_tokens=10, output_tokens=10, model="claude-sonnet-4-6",
            provider="anthropic", usage={},
            raw_response_json=json.dumps(
                {"content": [{"type": "text", "text": "..."}]}),
        )
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock, return_value=plain):
            resp = _send(contract_lane, pro_user, stream=False)
        assert resp.status_code == 200
        assert not _files_from(resp)
        assert _result_of(resp).get("text")

    def test_the_lane_stays_dark_for_everyone_else(self, client, pro_user):
        """No canary entry, no contract lane — the whole reason the
        rollout is safe."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()) as route:
            _send(client, pro_user, stream=False)
        if route.await_args:
            sent = route.await_args.args[1]
            assert not getattr(sent, "tools", None), (
                "contract tools armed for an unlisted user")


class TestTheOfferRoundTrip:
    """The road a phrasing without a format noun takes, and the one
    Scott used on device: ask -> "want the file?" -> "Yes" -> build.

    The artifact is chosen on the FIRST turn and has to survive onto the
    second, because the confirm send carries chat history rather than
    the original ask. That storage is the only reason the second turn
    knows which contract to use.
    """

    def test_an_unprefixed_ask_offers_first_then_builds(
            self, contract_lane, pro_user):
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()) as route:
            offer = _send(contract_lane, pro_user,
                          text="create a test plan from what we discussed")

        assert offer.status_code == 200
        body = offer.json()
        cta = ((body.get("feature_state") or {}).get("cta") or {})
        assert cta.get("kind") == "generation_offer", body
        offer_id = (cta.get("details") or {}).get("offer_id")
        assert offer_id, f"no offer minted: {body}"
        # Nothing was built yet, and the model was never asked to.
        assert not route.await_count or not getattr(
            route.await_args.args[1], "tools", None)

        # The reply interpreter is its own small model call; stub the
        # judgement, not the lane.
        yes = {"confirm": True, "format": "xlsx", "style": None,
               "version": None}
        with patch("app.services.document_generation.interpret_offer_reply",
                   new_callable=AsyncMock, return_value=yes), \
             patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            confirmed = _send(contract_lane, pro_user, text="Yes",
                              offer_id=offer_id, reply_text="Yes")

        assert confirmed.status_code == 200, confirmed.text
        assert _files_from(confirmed), confirmed.text[:600]
