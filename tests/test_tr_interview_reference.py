"""Tech Rehearsal's interview reference (tr_interview_reference).

TR handoff docs/handoffs/tr-interview-reference-2026-10-21.md, section 1,
and Scott's ruling the same day: paid only (TR's proposal), interview
scenarios only, researched with web search, GP owns every word of the
prompt. See app/services/tr_interview_reference.py.
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatRequest, ChatResponse
from app.services import tr_interview_reference as ref
from app.services.prompt_assembly import MissingPromptVariables, assemble_prompt

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/remote/techrehearsal/interview-reference.json").read_text())
ROUTING = json.loads((ROOT / "config/remote/model-routing.json").read_text())

INPUTS = {
    "candidate_name": "Dana Rivera", "role_title": "Senior Voice AI Architect",
    "company": "Acme Health", "job_description": "Build voice agents on Azure.",
    "resume": "Kore.ai 2024 to 2026: 150 production agents.",
}


def _meta(**over):
    md = {"call_type": ref.CALL_TYPE, "prompt_mode": "InterviewReference",
          "scenario": "interview", **INPUTS}
    md.update(over)
    return {k: v for k, v in md.items() if v is not None}


# --- the gate ----------------------------------------------------------------


@pytest.mark.parametrize("scenario,entitlement,expected", [
    ("interview", "paid", None),
    ("interview", "PAID", None),
    ("interview", "free", "plan"),
    ("interview", None, "plan"),
    ("interview", "", "plan"),
    ("negotiation", "paid", "scenario"),
    (None, "paid", "scenario"),
    ("pitch", "free", "scenario"),
])
def test_interview_only_and_paid_only(scenario, entitlement, expected):
    assert ref.refusal(ref.CALL_TYPE, scenario, entitlement) == expected


def test_no_other_call_type_is_touched():
    assert ref.refusal("tr_brief_analysis", None, None) is None
    assert ref.refusal("tr_query", "negotiation", "free") is None


def test_the_paywall_copy_is_served_and_has_a_floor():
    assert ref.plan_required_cta({ref.CONFIG_SLUG: CFG}) == CFG["planRequiredCta"]
    assert ref.plan_required_cta({})["text"]["en"]


# --- the prompt --------------------------------------------------------------


def test_the_prompt_carries_no_dash_punctuation():
    """Scott's standing rule; the model copies the punctuation it sees."""
    text = CFG["systemPrompt"] + CFG["userPromptTemplate"] + CFG["planRequiredCta"]["text"]["en"]
    assert not re.search("[–—]", text)
    assert not re.search(r"\s-\s|--", text)


def test_the_prompt_names_every_part_and_the_truth_rules():
    sp = CFG["systemPrompt"]
    for heading in ("# PART 1. FAST ANSWERS", "# PART 2. TECHNICAL PLAYBOOKS",
                    "# PART 3. DOMAIN DEPTH", "## 4. Why I'm looking", "NOT CONFIRMED",
                    "Not provided. Ask the candidate before the interview.",
                    "20,000 and 35,000 characters", "web_search"):
        assert heading in sp, heading


def test_the_first_real_run_findings_are_in_the_prompt():
    """TR's first real run (request 568106810d43, 2026-10-22): the model's
    search narration landed above the title in the exported file; known
    negatives from the notes went on NOT CONFIRMED hedges; year ranges lost
    their separator to the no dash rule; Part labels were 1, T and 5; an iOS
    document said ANR and carried a cloud section for a role with no cloud."""
    sp = CFG["systemPrompt"]
    for phrase in ("Write nothing before the title, not even between searches",
                   "that is a confirmed fact, not an open one, so it never goes on a NOT CONFIRMED line",
                   "I haven't used X in production; the closest I've done is",
                   '"2017 to 2021"', '"2021 to present"',
                   "no Android terms in an iOS document",
                   "write only: Not relevant for this role."):
        assert phrase in sp, phrase
    assert "PART T" not in sp and "PART 5" not in sp


def test_every_template_placeholder_is_declared():
    placeholders = set(re.findall(r"\{\{(\w+)\}\}", CFG["userPromptTemplate"]))
    declared = set(CFG["requiredVariables"]) | set(CFG["optionalVariables"])
    assert placeholders == declared


def test_it_assembles_from_flat_metadata_and_blanks_what_was_not_sent():
    out = assemble_prompt(ref.CALL_TYPE, "", {ref.CONFIG_SLUG: CFG},
                          prompt_mode="InterviewReference", variables=_meta())
    assert out["system_prompt"] == CFG["systemPrompt"]
    assert "Acme Health" in out["user_content"] and "150 production agents" in out["user_content"]
    assert "{{" not in out["user_content"], "an unsent optional must not survive as braces"
    assert out["max_tokens"] == 24000


@pytest.mark.parametrize("missing", ["role_title", "company", "job_description", "resume"])
def test_a_missing_required_input_is_refused(missing):
    with pytest.raises(MissingPromptVariables):
        assemble_prompt(ref.CALL_TYPE, "", {ref.CONFIG_SLUG: CFG},
                        variables=_meta(**{missing: None}))


# --- routing and the wire ----------------------------------------------------


def test_one_model_for_every_tier_with_medium_effort():
    row = ROUTING["apps"]["techrehearsal"]["call_types"][ref.CALL_TYPE]
    assert set(row["models"].values()) == {"anthropic/claude-sonnet-5"}
    assert row["reasoning"] == "medium" and "thinking" not in row


def test_the_wire_attaches_web_search_and_the_effort():
    from app.services.providers.anthropic import AnthropicAdapter
    req = ChatRequest(provider="anthropic", model="claude-sonnet-5", user_content="q",
                      system_prompt="s", reasoning="medium", max_tokens=24000,
                      metadata={"search_enabled": True})
    body, _ = AnthropicAdapter(
        api_key="test", base_url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key", auth_prefix="")._build_body(req)
    assert any(t.get("type", "").startswith("web_search") for t in body.get("tools", []))
    assert body.get("output_config") == {"effort": "medium"}
    assert body["max_tokens"] == 24000


# --- the real route ----------------------------------------------------------


def _post(client, user, entitlement, system_prompt=None, **meta_over):
    seen = []

    async def _capture(*args, **kwargs):
        for a in list(args) + list(kwargs.values()):
            if isinstance(a, ChatRequest):
                seen.append(a)
        return ChatResponse(text="# Dana Rivera: interview reference", input_tokens=1,
                            output_tokens=1, model="claude-sonnet-5", provider="anthropic",
                            usage={}, raw_request_json="{}", raw_response_json="{}")

    headers = {**user["headers"], "X-App-ID": "techrehearsal"}
    if entitlement is not None:
        headers["X-TR-Entitlement"] = entitlement
    with patch("app.services.anthropic_or_fallback.route_with_fallback",
               new=AsyncMock(side_effect=_capture)):
        payload = {"provider": "auto", "model": "auto", "user_content": "",
                   "metadata": _meta(**meta_over)}
        if system_prompt:
            payload.update(system_prompt=system_prompt, user_content="what do I say")
        r = client.post("/v1/chat", json=payload, headers=headers)
    return r, seen


def test_a_paid_call_is_assembled_routed_and_searched(client, pro_user):
    r, seen = _post(client, pro_user, "paid")
    assert r.status_code == 200, r.text[:300]
    assert seen, "the provider call was never reached"
    sent = seen[-1]
    assert (sent.model, sent.reasoning) == ("claude-sonnet-5", "medium")
    assert sent.get_meta("search_enabled") is True
    assert sent.system_prompt.startswith(CFG["systemPrompt"][:80])
    assert "Acme Health" in sent.user_content
    assert sent.max_tokens == 24000


@pytest.mark.parametrize("entitlement", ["free", None])
def test_free_and_anonymous_get_the_plan_required_envelope(client, pro_user, entitlement):
    r, seen = _post(client, pro_user, entitlement)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["text"] == "" and not seen, "refused before anything was spent"
    assert body["feature_state"]["code"] == "techrehearsal_plan_required"
    assert body["feature_state"]["cta"] == CFG["planRequiredCta"]


def test_another_scenario_is_a_400_whatever_the_plan(client, pro_user):
    r, seen = _post(client, pro_user, "paid", scenario="negotiation")
    assert r.status_code == 400, r.text[:300]
    assert r.json()["detail"]["code"] == "scenario_not_supported" and not seen


def test_search_stays_off_for_other_tr_call_types(client, pro_user):
    """The switch is this call type's alone."""
    r, seen = _post(client, pro_user, "paid", system_prompt="You help.", call_type="tr_query")
    assert r.status_code == 200, r.text[:300]
    assert seen and not seen[-1].get_meta("search_enabled")
