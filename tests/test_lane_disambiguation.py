"""Ambiguous plan-file asks draw a version question before any build.

Scott's ruling (2026-08-11), from the field: "make me a detailed project
plan that has our view and also the progress reported progress curve"
said "project plan" and "progress curve", missed every match_template
hint, and silently rode the freeform sandbox lane past a registry
template built for exactly that ask. Both outputs are legitimate; when a
file request is ambiguous between them the assistant asks ONE short
question describing the two versions in user terms, and an unambiguous
ask routes with no question.
"""

from __future__ import annotations

import json as _json

import pytest

from tests.test_document_generation import _enable_confirmed_generation
from tests.test_gantt_detailed import _DPLAN

FIELD_ASK = ("Can you make me a detailed project plan that has our view "
             "and also the progress reported progress curve")


# --- unit: the ambiguity matcher ---

def test_field_case_ask_is_ambiguous():
    from app.services.doc_templates import ambiguous_plan_ask
    assert ambiguous_plan_ask(FIELD_ASK) is True
    assert ambiguous_plan_ask(FIELD_ASK, format="xlsx") is True


def test_template_hints_are_not_ambiguous():
    # naming the template's own vocabulary is an unambiguous template ask
    from app.services.doc_templates import ambiguous_plan_ask
    assert ambiguous_plan_ask("build a gantt chart of our project plan") is False
    assert ambiguous_plan_ask("make me a project timeline in excel") is False


def test_unrelated_file_asks_are_not_ambiguous():
    from app.services.doc_templates import ambiguous_plan_ask
    assert ambiguous_plan_ask("make me a spreadsheet of team birthdays") is False
    assert ambiguous_plan_ask("export the attendee list as xlsx") is False


def test_non_xlsx_format_vetoes_ambiguity():
    # the registry only builds xlsx: a docx plan wish is unambiguously custom
    from app.services.doc_templates import ambiguous_plan_ask
    assert ambiguous_plan_ask("write the project plan as a word doc",
                              format="docx") is False
    assert ambiguous_plan_ask("project plan deck please", format="pptx") is False


def test_ambiguity_matcher_speaks_es_and_ja():
    from app.services.doc_templates import ambiguous_plan_ask
    assert ambiguous_plan_ask("hazme un plan de proyecto en excel") is True
    assert ambiguous_plan_ask("プロジェクト計画のファイルを作って") is True


# --- unit: the question envelope ---

def test_lane_question_envelope_shape_and_copy():
    from app.services.document_generation import (
        _CONFIRMATION_DEFAULTS,
        build_lane_question_envelope,
    )
    env = build_lane_question_envelope(_CONFIRMATION_DEFAULTS,
                                       gist="for the rollout",
                                       offer_id="lq1")
    fs = env["feature_state"]
    assert fs["feature"] == "document_generation"
    assert fs["state"] == "confirmation_required"
    cta = fs["cta"]
    # same envelope family the client already renders verbatim: the
    # question ships with no client build (doc 17 discipline)
    assert cta["kind"] == "generation_offer"
    assert cta["action"] == "confirm_generation"
    text = cta["text"]
    # both versions described in user terms (Scott's wording guidance)
    assert "Gantt timeline" in text
    assert "slip history" in text
    assert "receipts" in text
    assert "progress curve" in text
    assert "custom spreadsheet" in text
    assert "for the rollout" in text
    assert "—" not in text and "–" not in text   # served copy carries no dashes
    assert "{" not in text                        # no dangling placeholders
    assert cta["details"]["offer_id"] == "lq1"
    assert cta["details"]["template_id"] == "gantt_detailed"
    assert cta["details"]["expected_format"] == "xlsx"
    # no gist -> no double space, no dangling braces
    env2 = build_lane_question_envelope(_CONFIRMATION_DEFAULTS)
    assert "{" not in env2["feature_state"]["cta"]["text"]
    assert "  " not in env2["feature_state"]["cta"]["text"]


def test_bundled_lane_question_agrees_with_default():
    # the served bundle carries the question so the wording stays GP's to
    # change without a build anywhere; it must satisfy the same contract
    c = _json.load(open("config/remote/client-config.json"))
    text = (c["documents"]["generation"]["confirmation"]
            ["lane_question_text"])
    assert "{gist}" in text
    assert "Gantt timeline" in text and "custom" in text
    assert "—" not in text and "–" not in text


# --- unit: the reply interpreter's version verdict ---

@pytest.mark.asyncio
async def test_interpreter_returns_version_word():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.document_generation import interpret_offer_reply
    offer = {"format": "xlsx", "gist": "plan", "lane_choice": "asked"}
    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": true, "format": null, "version": "workbook"}'))
    out = await interpret_offer_reply(router, offer, "the workbook please",
                                      verbatim=True)
    assert out["version"] == "workbook"
    # the judge is told both versions were on the table
    sent = router.route.await_args.args[0].user_content
    assert "two versions" in sent

    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": true, "format": null, "version": "custom"}'))
    out = await interpret_offer_reply(router, offer, "custom", verbatim=True)
    assert out["version"] == "custom"

    # junk version values fail closed to None
    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": true, "format": null, "version": "fancy"}'))
    out = await interpret_offer_reply(router, offer, "yes", verbatim=True)
    assert out["version"] is None

    # plain offers never mention a version choice to the judge
    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": true, "format": null}'))
    await interpret_offer_reply(router, {"format": "xlsx", "gist": "x"},
                                "yes", verbatim=True)
    assert "two versions" not in router.route.await_args.args[0].user_content


# --- e2e: the field case, replayed ---

def _classify_xlsx(monkeypatch, gist="for the project"):
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    monkeypatch.setattr(dg, "classify_generation_intent", AsyncMock(
        return_value={"file_request": True, "format": "xlsx", "gist": gist}))


def _ask_field_case(client, free_user, monkeypatch):
    from tests.conftest import chat_request
    _classify_xlsx(monkeypatch)
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content=FIELD_ASK,
    ), headers=free_user["headers"])
    return r.json()["feature_state"]["cta"]


def test_field_case_draws_the_question_not_a_build(
        client, free_user, mock_provider, monkeypatch):
    _enable_confirmed_generation(client)
    cta = _ask_field_case(client, free_user, monkeypatch)
    assert "workbook or custom" in cta["text"]
    assert cta["details"]["template_id"] == "gantt_detailed"
    assert cta["details"]["offer_id"]
    # nothing generated: the question came BEFORE any build
    mock_provider.assert_not_awaited()


def test_workbook_reply_builds_the_template(
        client, free_user, mock_provider, monkeypatch):
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    oid = _ask_field_case(client, free_user, monkeypatch)["details"]["offer_id"]
    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": None,
                      "version": "workbook"}))
    mock_provider.canned_response.text = _json.dumps(_DPLAN)
    mock_provider.return_value = mock_provider.canned_response
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_id": "gen-lane-wb",
                  "reply_text": "the workbook"},
        user_content="Current question: the workbook",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    result = _json.loads(
        r.text.split("event: generation_result\ndata: ")[1].split("\n")[0])
    assert "Gantt_Detailed" in result["generated_files"][0]["name"]
    # extraction leg, not a sandbox turn
    sent = mock_provider.await_args_list[-1].args[0]
    assert sent.generation is False
    assert "FILE BUILD OVERRIDE" in sent.system_prompt or "JSON" in sent.system_prompt
    # the build ran against the ORIGINATING ask, not the reply's history
    assert "progress reported progress curve" in sent.user_content


def test_custom_reply_builds_freeform(
        client, free_user, mock_provider, monkeypatch):
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    oid = _ask_field_case(client, free_user, monkeypatch)["details"]["offer_id"]
    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": None,
                      "version": "custom"}))
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_id": "gen-lane-cu",
                  "reply_text": "custom please, exactly what I described"},
        user_content="Current question: custom please",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    sent = mock_provider.await_args_list[-1].args[0]
    assert sent.generation is True               # sandbox lane, no template


def test_bare_yes_defaults_to_the_workbook(
        client, free_user, mock_provider, monkeypatch):
    """Users saying "project plan" expect the Gantt (Scott's ruling): a
    confirm that never picks a version builds the structured workbook."""
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    oid = _ask_field_case(client, free_user, monkeypatch)["details"]["offer_id"]
    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": None,
                      "version": None}))
    mock_provider.canned_response.text = _json.dumps(_DPLAN)
    mock_provider.return_value = mock_provider.canned_response
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_id": "gen-lane-yes",
                  "reply_text": "yes"},
        user_content="Current question: yes",
    ), headers=free_user["headers"])
    result = _json.loads(
        r.text.split("event: generation_result\ndata: ")[1].split("\n")[0])
    assert "Gantt_Detailed" in result["generated_files"][0]["name"]


def test_pill_tap_at_the_question_builds_the_workbook_default(
        client, free_user, mock_provider, monkeypatch):
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    oid = _ask_field_case(client, free_user, monkeypatch)["details"]["offer_id"]
    mock_provider.canned_response.text = _json.dumps(_DPLAN)
    mock_provider.return_value = mock_provider.canned_response
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_confirmed": True,
                  "generation_id": "gen-lane-tap"},
        user_content="yes",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    result = _json.loads(
        r.text.split("event: generation_result\ndata: ")[1].split("\n")[0])
    assert "Gantt_Detailed" in result["generated_files"][0]["name"]


def test_explicit_ambiguous_ask_skips_the_fast_path(
        client, free_user, mock_provider, monkeypatch):
    """An explicit file verb normally arms on the spot; ambiguous plan
    vocabulary keeps the question first (ask BEFORE generating)."""
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    _classify_xlsx(monkeypatch)
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="make me a file with the project plan and the "
                     "progress curve",
    ), headers=free_user["headers"])
    cta = r.json()["feature_state"]["cta"]
    assert "workbook or custom" in cta["text"]
    mock_provider.assert_not_awaited()


def test_unambiguous_explicit_ask_still_fast_paths(
        client, free_user, mock_provider, monkeypatch):
    """No question when the request unambiguously matches one lane."""
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    _classify_xlsx(monkeypatch, gist="of team birthdays")
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"generation_id": "gen-lane-fp"},
        user_content="make me a spreadsheet of team birthdays",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert "event: generation_result" in r.text   # armed, no question
    sent = mock_provider.await_args_list[-1].args[0]
    assert sent.generation is True


def test_gantt_ask_still_gets_the_template_intercept(
        client, free_user, mock_provider, monkeypatch):
    """Naming the template is unambiguous: the existing gantt offer flow
    is untouched, no version question."""
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    _classify_xlsx(monkeypatch, gist="of the plan")
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="build a gantt chart of our project plan",
    ), headers=free_user["headers"])
    cta = r.json()["feature_state"]["cta"]
    assert cta["details"]["template_id"] == "gantt_smartsheet"
    assert "workbook or custom" not in cta["text"]
    assert "simple or detailed" in cta["text"]


def test_teaser_yes_over_ambiguous_ask_draws_the_question(
        client, free_user, mock_provider, monkeypatch):
    """Soft plan vocabulary gets the teaser; the typed yes converts it to
    a real file request that is still ambiguous, so the question comes
    before any build."""
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    monkeypatch.setattr(dg, "classify_generation_intent", AsyncMock(
        return_value={"file_request": False, "format": None, "gist": ""}))
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="Current question: our project plan and progress "
                     "curve look rough in this spreadsheet",
    ), headers=free_user["headers"])
    fs = r.json().get("feature_state")
    assert fs and fs["cta"]["kind"] == "generation_teaser"
    oid = fs["cta"]["details"]["offer_id"]
    _awaits_after_teaser = mock_provider.await_count

    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": None,
                      "version": None}))
    r2 = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "reply_text": "yes"},
        user_content="Current question: yes",
    ), headers=free_user["headers"])
    cta2 = r2.json()["feature_state"]["cta"]
    assert "workbook or custom" in cta2["text"]
    q_oid = cta2["details"]["offer_id"]
    assert q_oid and q_oid != oid
    # the question turn generated nothing (no provider call at all)
    assert mock_provider.await_count == _awaits_after_teaser

    # the question's answer then builds, against the ORIGINATING ask
    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": None,
                      "version": "workbook"}))
    mock_provider.canned_response.text = _json.dumps(_DPLAN)
    mock_provider.return_value = mock_provider.canned_response
    r3 = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": q_oid, "generation_id": "gen-lane-tsr",
                  "reply_text": "workbook"},
        user_content="Current question: workbook",
    ), headers=free_user["headers"])
    result = _json.loads(
        r3.text.split("event: generation_result\ndata: ")[1].split("\n")[0])
    assert "Gantt_Detailed" in result["generated_files"][0]["name"]
    sent = mock_provider.await_args_list[-1].args[0]
    assert "look rough in this spreadsheet" in sent.user_content


def test_pill_tap_at_teaser_over_ambiguous_ask_draws_the_question(
        client, free_user, mock_provider, monkeypatch):
    """The pill tap at a teaser normally arms on the spot; over an
    ambiguous plan ask the version question was never asked, so the tap
    draws it instead of a build."""
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    monkeypatch.setattr(dg, "classify_generation_intent", AsyncMock(
        return_value={"file_request": False, "format": None, "gist": ""}))
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="Current question: the project plan and progress "
                     "curve in that spreadsheet worry me",
    ), headers=free_user["headers"])
    fs = r.json().get("feature_state")
    assert fs and fs["cta"]["kind"] == "generation_teaser"
    oid = fs["cta"]["details"]["offer_id"]
    _awaits_after_teaser = mock_provider.await_count

    r2 = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_confirmed": True},
        user_content="Current question: the project plan and progress "
                     "curve in that spreadsheet worry me",
    ), headers=free_user["headers"])
    cta2 = r2.json()["feature_state"]["cta"]
    assert "workbook or custom" in cta2["text"]
    assert cta2["details"]["offer_id"] != oid
    assert mock_provider.await_count == _awaits_after_teaser
