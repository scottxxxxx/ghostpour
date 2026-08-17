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

import asyncio
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


class TestAPausedBuildResumes:
    """The last of the family. Every defect on this lane was behaviour
    keyed on `request.generation`, which the contract lane deliberately
    does not set because it ships a tool schema instead of using the
    sandbox. The pause_turn continuation loop was the final one.

    It matters most for the case Scott actually wanted: research the
    topic, then build the artifact. Mixing web_search into a build turn
    means the server-side tool loop can hit its iteration limit, and a
    paused contract turn ends with no emit_artifact block at all — so
    the lane finds no tool call, falls back to text, and the user waits
    out a three minute build for nothing.
    """

    def test_a_paused_turn_is_resumed_rather_than_dropped(self):
        import asyncio
        from unittest.mock import MagicMock

        from app.models.chat import ChatRequest
        from app.services.providers.anthropic import AnthropicAdapter

        paused = {"stop_reason": "pause_turn", "content": [
            {"type": "server_tool_use", "name": "web_search", "id": "s1"}],
            "container": {"id": "cont_1"}, "usage": {}}
        finished = json.loads(_emit_artifact_response().raw_response_json)

        adapter = AnthropicAdapter.__new__(AnthropicAdapter)
        adapter.base_url = "https://example/v1/messages"
        adapter.api_key = "k"
        adapter._build_headers = lambda: {}

        posts = []

        async def fake_post(url, body, headers, timeout=None):
            posts.append(body)
            data = paused if len(posts) == 1 else finished
            return 200, data, "{}", json.dumps(data)

        adapter._post = fake_post
        req = ChatRequest(provider="anthropic", model="claude-sonnet-4-6",
                          user_content="x", system_prompt="s",
                          tools=[{"name": "emit_artifact",
                                  "input_schema": {"type": "object"}}])
        resp = asyncio.run(adapter.send_request(req))

        assert len(posts) == 2, "a paused build was not resumed"
        # The continuation reuses the container and appends the partial.
        assert posts[1].get("container") == "cont_1"
        assert posts[1]["messages"][-1]["role"] == "assistant"
        # And the finished artifact survives to the caller.
        blocks = json.loads(resp.raw_response_json)["content"]
        assert any(b.get("name") == "emit_artifact" for b in blocks)

    def test_a_plain_chat_turn_is_still_never_resumed(self):
        """The loop is for builds. An ordinary turn that somehow pauses
        must not be silently re-billed."""
        from app.models.chat import ChatRequest
        from app.services.providers.anthropic import AnthropicAdapter as A

        assert A._is_build(ChatRequest(
            provider="anthropic", model="m", user_content="x")) is False


def test_all_three_build_lanes_are_recognised_as_builds():
    """The adapter's build test decides two things: the 400s timeout and
    whether a paused turn resumes. Each lane signals differently, and a
    lane the adapter cannot recognise silently gets a chat turn's
    treatment — which is how the contract lane ended up on a 180s cap
    against its own 170s promise.

      sandbox   `generation=True`
      contract  a tool schema
      template  neither, so the router marks it
    """
    from app.models.chat import ChatRequest
    from app.services.providers.anthropic import AnthropicAdapter as A

    chat = ChatRequest(provider="anthropic", model="m", user_content="x")
    sandbox = ChatRequest(provider="anthropic", model="m", user_content="x",
                          generation=True)
    contract = ChatRequest(provider="anthropic", model="m", user_content="x",
                           tools=[{"name": "emit_artifact"}])
    template = ChatRequest(provider="anthropic", model="m", user_content="x",
                           metadata={"build_lane": "template"})

    assert A._is_build(chat) is False
    for req in (sandbox, contract, template):
        assert A._is_build(req) is True


def test_the_template_lane_marks_itself():
    """The mark has to be set where the lane arms, or the flag above is
    decoration."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "app/routers/chat.py").read_text()
    arm = src.index("elif _gen_armed and _template_id:")
    assert '"build_lane": "template"' in src[arm:arm + 900]


class TestResearchIntentSurvivesTheOffer:
    """A researched file ask goes: turn 1 mints "want the file?", turn 2
    builds. The search flag rides turn 1. If it does not reach turn 2,
    the user gets a file with no research in it and nothing says so —
    failure shaped exactly like success.

    The offer already remembers ask_content and images for this reason.
    Research intent belongs in the same bag.
    """

    def test_the_offer_remembers_and_the_confirm_inherits(self):
        from app.services import generation_offers

        oid = generation_offers.create(
            "u-search", "xlsx", "g", artifact_id="test_plan",
            search_enabled=True)
        assert generation_offers.peek("u-search", oid)["search_enabled"] is True
        # peek must not consume: the reply still has an offer to claim.
        assert generation_offers.take("u-search", oid)["search_enabled"] is True
        assert generation_offers.peek("u-search", oid) is None

    def test_an_ask_without_research_stays_without_it(self):
        from app.services import generation_offers
        oid = generation_offers.create("u-plain", "xlsx", "g")
        assert generation_offers.take("u-plain", oid)["search_enabled"] is False

    def test_the_flag_is_inherited_before_the_cap_gate_not_after(self):
        """The load-bearing detail. Inheriting after the gate would
        attach the search tool with no tier or monthly cap ruling on it
        and no counter movement — a cap bypass wearing a bugfix."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "app/routers/chat.py").read_text()
        inherit = src.index("search_intent_inherited_from_offer")
        # Anchor on the gate itself, not on an import of its module —
        # search_caps is also imported by an unrelated helper earlier in
        # the file, which is not the thing that must come second.
        gate = src.index("if body.get_meta(\"search_enabled\"):", inherit - 4000)
        assert inherit < gate, "inheritance must precede the search gate"
        # And it must sit inside the same request flow, not in a helper.
        assert 0 < gate - inherit < 2000

    def test_the_lane_question_carries_it_across_a_remint(self):
        """An ambiguous plan ask re-mints the offer to ask which version.
        The intent has to survive that hop too, or research is lost by
        the user answering a question we asked."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "app/routers/chat.py").read_text()
        assert src.count('_offer.get("search_enabled")') >= 2


class TestTheConfirmTurnRecoversResearchIntent:
    """VERIFIED with the client team 2026-08-17, by them reading their
    own code rather than recalling it: **the client sends
    `search_enabled: false` on the confirm turn.** Their flag resets at
    the end of every completed send, deliberately, so an armed search
    never leaks into the next question. They are right not to change it
    — a flag that quietly persists is how someone spends a capped, paid
    allowance they did not ask to spend. We are the only party that
    knows the offer and the reply are the same piece of work, so the
    recovery belongs here.

    That makes this LOAD BEARING rather than belt and braces. If it
    regresses: a researched build silently produces a file with no
    research in it, AND the estimate is 85 seconds short on precisely
    the turns that run long. Failure shaped exactly like success, twice.

    Existing coverage was a store-level unit test plus two assertions
    about SOURCE TEXT (`src.index`, `src.count`). Those prove a line was
    written, never that the path runs — the exact habit that put five
    defects on a device one at a time. This drives the real turn and
    asserts on the request the provider actually receives, because that
    is what decides whether the search tool gets attached at all.
    """

    def _confirm(self, client, pro_user, *, offer_search: bool, **meta):
        """Arrange an offer that does or does not remember research,
        then answer it the way the client really answers it."""
        from app.services import generation_offers

        oid = generation_offers.create(
            pro_user["user_id"], "xlsx", "test scenarios",
            artifact_id="test_plan", search_enabled=offer_search)

        yes = {"confirm": True, "format": "xlsx", "style": None,
               "version": None}
        with patch("app.services.document_generation.interpret_offer_reply",
                   new_callable=AsyncMock, return_value=yes), \
             patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()) as route:
            resp = _send(client, pro_user, text="Yes", offer_id=oid,
                         reply_text="Yes",
                         # What iOS actually puts on the wire here.
                         search_enabled=False, **meta)
        return resp, route

    def test_the_build_still_searches_though_the_client_said_false(
            self, contract_lane, pro_user):
        """Since the research leg landed, the recovered flag shows up as
        a research CALL rather than a tool on the build call, so this
        asserts on the first leg. If the recovery regresses there is no
        research leg at all and the count drops to one, which is what
        makes this still catch it."""
        resp, route = self._confirm(contract_lane, pro_user,
                                    offer_search=True)
        assert resp.status_code == 200, resp.text[:400]
        assert route.await_count == 2, (
            f"expected a research leg then a build, got {route.await_count} "
            "call(s); the confirm turn dropped the research the ask opted "
            "into and the user gets a file with no research in it")
        researched = route.await_args_list[0].args[1]
        assert researched.get_meta("search_enabled") is True
        assert not researched.tools, (
            "the research leg was handed the build tool, so it can build "
            "immediately and the boundary is not real")

    def test_the_estimate_still_covers_the_research(
            self, contract_lane, pro_user):
        """The second half of the same regression, and the one a user
        actually watches: a short promise on a long turn."""
        resp, _ = self._confirm(contract_lane, pro_user, offer_search=True)
        started = next((d for n, d in _events(resp)
                        if n == "generation_started"), None)
        assert started, [n for n, _ in _events(resp)]
        assert "search_expected_seconds" in started, started

    def test_an_offer_without_research_is_not_given_any(
            self, contract_lane, pro_user):
        """The client's false must still MEAN false when there is no
        opted-in research to recover. Otherwise the recovery is just a
        cap bypass that happens to be shaped like a bugfix."""
        resp, route = self._confirm(contract_lane, pro_user,
                                    offer_search=False)
        assert resp.status_code == 200, resp.text[:400]
        sent = route.await_args.args[1]
        assert not sent.get_meta("search_enabled"), (
            "a plain ask was silently upgraded to a paid search")


class TestResearchPlusFileSkipsTheOffer:
    """Scott, 2026-08-16, after watching it fail three times in a row.

    He asked us to find information online and put it into a test plan.
    That is one request with two parts. We answered by deferring both
    and asking a question he had already answered, and the offer turn
    never calls the main model, so the search he opted into did not run.
    Meanwhile the client showed "Searching the web..." off the toggle,
    which said it had. Three sends, three offers, zero searches, counter
    unmoved at 3.

    Turning search on spends from a monthly cap, so it is deliberate
    rather than a default. Paired with a file request there is nothing
    left to confirm.
    """

    def _armed_tools(self, client, pro_user, **meta):
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()) as route:
            _send(client, pro_user,
                  text="find what typically breaks these bots online and put "
                       "it into a complete test plan",
                  **meta)
        if not route.await_args:
            return None
        return getattr(route.await_args.args[1], "tools", None)

    def test_search_plus_file_builds_instead_of_asking(
            self, contract_lane, pro_user):
        tools = self._armed_tools(contract_lane, pro_user, search_enabled=True)
        assert tools, "a research-and-build ask still only got an offer"
        assert any(t.get("name") == "emit_artifact" for t in tools), tools

    def test_the_same_ask_without_search_still_offers_first(
            self, contract_lane, pro_user):
        """The opt-in is what removes the ambiguity. Without it this is
        an ordinary file ask and the confirmation still earns its place."""
        assert not self._armed_tools(contract_lane, pro_user)

    def test_the_build_is_not_charged_a_second_classifier_call(
            self, contract_lane, pro_user):
        """The research path already ran the classifier, so re-asking it
        which artifact this is would be paying twice for one answer."""
        from app.services import document_generation as dg

        intent = {"file_request": True, "format": "xlsx", "gist": "g",
                  "artifact": "test_plan", "artifact_confidence": "high"}
        with patch.object(dg, "classify_generation_intent",
                          new_callable=AsyncMock, return_value=intent) as clf, \
             patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            _send(contract_lane, pro_user,
                  text="find what breaks these bots online and put it into a "
                       "complete test plan",
                  search_enabled=True)
        assert clf.await_count <= 1, (
            f"classifier called {clf.await_count}x for one answer")


class TestTheEstimateCoversTheResearch:
    """Every per-artifact estimate we hold was measured on a build
    ALONE. A research-backed build searches first, inside the same turn,
    and that time is invisible to those numbers.

    Live 2026-08-16: a test plan promised 170s, took 258s, and the user
    watched the counter sail past the promise wondering what had broken.
    Same artifact and model without research: 169.7s and 183.0s. With
    four searches: 255.0s.
    """

    def _started(self, client, pro_user, **meta):
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            resp = _send(client, pro_user, stream=True, **meta)
        for name, data in _events(resp):
            if name == "generation_started":
                return data
        raise AssertionError(f"no generation_started: {resp.text[:300]}")

    def test_a_plain_build_promises_the_artifact_time(
            self, contract_lane, pro_user):
        assert self._started(contract_lane, pro_user)["expected_seconds"] == 170

    def test_a_researched_build_promises_the_research_too(
            self, contract_lane, pro_user):
        from app.routers.chat import SEARCH_PHASE_SECONDS
        got = self._started(contract_lane, pro_user,
                            search_enabled=True)["expected_seconds"]
        assert got == 170 + SEARCH_PHASE_SECONDS
        # And the promise now brackets what the real turn took.
        assert 240 <= got <= 280, got

    def test_the_allowance_is_grounded_in_measurement(self):
        """A number nobody measured is a number nobody can defend."""
        import pathlib
        from app.routers.chat import SEARCH_PHASE_SECONDS
        assert 60 <= SEARCH_PHASE_SECONDS <= 120
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "app/routers/chat.py").read_text()
        i = src.index("SEARCH_PHASE_SECONDS = ")
        assert "Measured" in src[i - 700:i]


class TestResearchIsItsOwnLeg:
    """Research runs as its own call, finishes, and hands its findings
    to the build. Two calls we sequence rather than one call juggling
    two tools, so the search/build boundary is known because we drew it.

    The failures this guards are all silent. A build that keeps the
    search tool can research again and the boundary stops being true. A
    research leg whose searches nobody counts spends a capped paid
    allowance with the meter stopped. A findings block that never
    reaches the build means we paid for research and threw it away.
    """

    def _run(self, client, pro_user, research_text="FINDING: rate is 4.2%"):
        from app.models.chat import ChatResponse

        research = ChatResponse(
            text=research_text, input_tokens=10, output_tokens=20,
            model="claude-sonnet-4-6", provider="anthropic",
            usage={"web_search_requests": 3},
            raw_request_json="{}", raw_response_json="{}")

        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   side_effect=[research, _emit_artifact_response()]) as route:
            resp = _send(client, pro_user, stream=True,
                         text="find what typically breaks these bots online "
                              "and put it into a complete test plan",
                         search_enabled=True)
        return resp, route

    def test_research_runs_first_and_the_build_follows(
            self, contract_lane, pro_user):
        resp, route = self._run(contract_lane, pro_user)
        assert route.await_count == 2, route.await_count
        first, second = (c.args[1] for c in route.await_args_list)
        assert not first.tools, "research leg must not be able to build"
        assert any(t["name"] == "emit_artifact" for t in (second.tools or [])), (
            "the second leg is the build and must carry the build tool")

    def test_the_build_cannot_search_again(self, contract_lane, pro_user):
        """The boundary is only real if the build cannot cross back."""
        _, route = self._run(contract_lane, pro_user)
        build = route.await_args_list[1].args[1]
        assert not build.get_meta("search_enabled"), (
            "the build kept the search tool, so it can research mid-build "
            "and the phase we just reported stops being true")

    def test_the_findings_reach_the_build(self, contract_lane, pro_user):
        _, route = self._run(contract_lane, pro_user)
        build = route.await_args_list[1].args[1]
        assert "FINDING: rate is 4.2%" in (build.system_prompt or ""), (
            "we paid for research and did not hand it to the build")
        assert "cannot search again" in (build.system_prompt or ""), (
            "the build was not told the tool is gone, so a named gap "
            "becomes something it quietly fills instead")

    def test_the_research_legs_searches_still_count(
            self, contract_lane, pro_user):
        """The meter must not stop when the feature starts working."""
        resp, _ = self._run(contract_lane, pro_user)
        state = _result_of(resp).get("search_state") or {}
        assert state.get("was_used") is True, state

    def test_a_failed_research_leg_still_delivers_the_file(
            self, contract_lane, pro_user):
        """Falls open on purpose: a research failure must not cost the
        user the document they asked for."""
        from httpx import HTTPError
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   side_effect=[HTTPError("research died"),
                                _emit_artifact_response()]):
            resp = _send(contract_lane, pro_user, stream=True,
                         text="find what breaks these bots online and put "
                              "it into a complete test plan",
                         search_enabled=True)
        assert _files_from(resp), resp.text[:400]

    def test_a_plain_build_runs_exactly_one_leg(
            self, contract_lane, pro_user):
        """No search opted into means no boundary to report and nothing
        to change: one call, as before."""
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()) as route:
            _send(contract_lane, pro_user, stream=True,
                  text="create a test plan spreadsheet from the meeting")
        assert route.await_count == 1, route.await_count


class TestThePhaseIsObservedNotTimed:
    """SS maps `phase: "searching"` client side already, so the day we
    emit it their card retitles itself with no client release. It must
    mean a search is in flight RIGHT NOW, never a guess from a clock.

    Every test here drives REAL progress ticks: the tick interval is
    patched down and the stubbed provider is made slow enough to cross
    it. Without that a stub returns before the first tick, the event
    list is empty, and "searching not in []" passes while the server
    shouts "searching" at every user. That is a test that cannot fail,
    which is worse than no test because it is why nobody looks.
    """

    def _phases(self, client, pro_user, *, legs, delay=0.05, text=None,
                **meta):
        async def _slow(*a, **kw):
            await asyncio.sleep(delay)
            return legs.pop(0)

        with patch("app.routers.chat._PROGRESS_TICK_SECONDS", 0.01), \
             patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock, side_effect=_slow):
            resp = _send(client, pro_user, stream=True,
                         text=text or ("find what breaks these bots online "
                                       "and put it into a complete test plan"),
                         **meta)
        phases = [d.get("phase") for n, d in _events(resp)
                  if n == "generation_progress"]
        assert phases, "no progress ticks fired; this test proves nothing"
        return phases

    def _research(self):
        from app.models.chat import ChatResponse
        return ChatResponse(
            text="found things", input_tokens=1, output_tokens=1,
            model="claude-sonnet-4-6", provider="anthropic",
            usage={"web_search_requests": 2},
            raw_request_json="{}", raw_response_json="{}")

    def test_a_plain_build_never_claims_to_be_searching(
            self, contract_lane, pro_user):
        phases = self._phases(
            contract_lane, pro_user, legs=[_emit_artifact_response()],
            # Format noun, so this arms a build on this turn rather than
            # minting an offer. Without it there is no build to report a
            # phase for and the assertion would pass on an empty list.
            text="create a test plan spreadsheet from the meeting")
        assert "searching" not in phases, phases
        assert set(phases) == {"working"}, phases

    def test_a_researched_build_reports_searching_while_it_searches(
            self, contract_lane, pro_user):
        """The positive case. Ticks fire during the research leg, and
        those must say searching rather than claiming to build a file
        that nothing has started building."""
        phases = self._phases(contract_lane, pro_user, delay=0.12,
                              legs=[self._research(),
                                    _emit_artifact_response()],
                              search_enabled=True)
        assert "searching" in phases, (
            f"the card spent the search claiming to build: {phases}")

    def test_it_stops_saying_searching_once_research_returns(
            self, contract_lane, pro_user):
        """And it has to STOP. A phase that latches is the same lie
        pointing the other way."""
        phases = self._phases(contract_lane, pro_user, delay=0.12,
                              legs=[self._research(),
                                    _emit_artifact_response()],
                              search_enabled=True)
        assert phases[-1] == "working", (
            f"still claiming to search after research returned: {phases}")


class TestTheEstimateSaysWhichPartIsWhich:
    """A single number explains that the wait is long but never why.
    The client can say "usually about 1:25 searching, then 2:50
    building" only if we break the total down.

    Agreed with the client team 2026-08-17: they render this as caption
    copy and NEVER as a phase claim, because a timer-driven retitle
    would be the same fabrication we refused to emit ourselves. The
    observed phase token supersedes it later, additively, with nothing
    to rip out.
    """

    def _started(self, client, pro_user, **meta):
        with patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            resp = _send(client, pro_user, stream=True, **meta)
        for name, data in _events(resp):
            if name == "generation_started":
                return data
        raise AssertionError(f"no generation_started: {resp.text[:300]}")

    def test_a_researched_build_breaks_its_estimate_down(
            self, contract_lane, pro_user):
        from app.routers.chat import SEARCH_PHASE_SECONDS
        got = self._started(contract_lane, pro_user, search_enabled=True)
        assert got["search_expected_seconds"] == SEARCH_PHASE_SECONDS

    def test_the_parts_cannot_disagree_with_the_total(
            self, contract_lane, pro_user):
        """Build slice is the subtraction, so it is always exact."""
        got = self._started(contract_lane, pro_user, search_enabled=True)
        build = got["expected_seconds"] - got["search_expected_seconds"]
        assert build == 170, (
            f"build slice {build} is not the artifact's own estimate")

    def test_a_plain_build_sends_no_breakdown(
            self, contract_lane, pro_user):
        """Absence is the signal that there is one expectation to
        render. Sending a 0 would invite a "0:00 searching" caption."""
        got = self._started(contract_lane, pro_user)
        assert "search_expected_seconds" not in got, got
class TestTheCountingArtifactPullsEverything:
    """topic_tracker is the only contract that needs cross-meeting
    context, and its whole job is counting. The cap that truncated it
    was OURS: CQ's limit is caller-supplied and absent by default, so
    sending one is what made them truncate. Measured on the wire
    2026-08-17, Scott's quilt reports total_available 2136 against the
    500 we asked for.

    This drives the real turn. A service-level test that calls
    quilt_dossier(limit=None) directly proves the service honours None
    and would pass happily while the lane went on sending 500, which is
    exactly what happened to the first version of it.
    """

    def _dossier_call(self, client, pro_user):
        from app.services import context_quilt as cq

        intent = {"file_request": True, "format": "xlsx",
                  "gist": "what keeps coming up",
                  "artifact": "topic_tracker", "artifact_confidence": "high"}
        dossier = AsyncMock(return_value={"meetings": [
            {"patches": [{"patch_id": "p1", "created_at": "2026-08-01",
                          "text": "pricing came up"}]}]})
        with patch("app.services.document_generation."
                   "classify_generation_intent",
                   new_callable=AsyncMock, return_value=intent), \
             patch.object(cq, "quilt_dossier", dossier), \
             patch("app.services.anthropic_or_fallback.route_with_fallback",
                   new_callable=AsyncMock,
                   return_value=_emit_artifact_response()):
            _send(client, pro_user, stream=True,
                  text="build a topic tracker spreadsheet",
                  project_id="PROJ-1")
        return dossier

    def test_the_lane_asks_for_the_whole_quilt(self, contract_lane, pro_user):
        d = self._dossier_call(contract_lane, pro_user)
        assert d.await_count == 1, (
            f"dossier fetched {d.await_count}x for one counting build")
        assert d.await_args.kwargs.get("limit") is None, (
            "the counting artifact asked for a capped dossier, so its "
            "counts are computed from a truncated set and the cell is "
            "confidently wrong: "
            f"limit={d.await_args.kwargs.get('limit')}")
