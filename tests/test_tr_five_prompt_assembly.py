"""The five remaining TR prompts (tr_intake, tr_brief_analysis, tr_debrief,
tr_rewrite, tr_resume_enhance) — GP-owned configs with per-scenario_kind
interpolation via the `scenarios` map.

Verbatimness anchor: at build time every prompt variant observed on the
pre-cutover wire (client-sent system prompts in usage_log) was byte-identical
to the assembled output of these configs; the unobserved variants come
verbatim from docs/handoffs/tr-remaining-five-prompts-handoff.md.
"""

import json

from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG, assemble_prompt

FIVE = {
    "tr_intake": "techrehearsal/intake",
    "tr_brief_analysis": "techrehearsal/brief-analysis",
    "tr_debrief": "techrehearsal/debrief",
    "tr_rewrite": "techrehearsal/rewrite",
    "tr_resume_enhance": "techrehearsal/resume-enhance",
}


def _cfgs():
    return {slug: json.load(open(f"config/remote/{slug}.json")) for slug in FIVE.values()}


def _asm(call_type, kind=None, scenario=None):
    return assemble_prompt(call_type, "USER DATA", _cfgs(),
                           scenario_kind=kind, scenario=scenario)


def test_call_types_are_mapped():
    for ct, slug in FIVE.items():
        assert _CALL_TYPE_TO_CONFIG.get(ct) == slug


def test_configs_have_required_shape():
    for slug in FIVE.values():
        cfg = json.load(open(f"config/remote/{slug}.json"))
        assert cfg["systemPrompt"]
        assert cfg.get("userPromptTemplate") == ""  # client data blob passes through
        assert cfg.get("maxTokens") == 4096         # matches the pre-cutover client value
        assert isinstance(cfg["version"], int)


def test_routing_has_all_five_tr_entries():
    r = json.load(open("config/remote/model-routing.json"))
    ct = r["apps"]["techrehearsal"]["call_types"]
    for call_type in FIVE:
        assert call_type in ct, f"missing routing for {call_type} — auto would 400 on flip"
        models = ct[call_type]["models"]
        for tier_key in ("free", "paid", "default"):
            assert models.get(tier_key), f"{call_type} missing {tier_key} model"


# --- scenario selection matrix ---

def test_intake_personal_kinds_get_distinct_guidance():
    hard = _asm("tr_intake", kind="hardConversation")["system_prompt"]
    repair = _asm("tr_intake", kind="repairConversation")["system_prompt"]
    protect = _asm("tr_intake", kind="protectConversation")["system_prompt"]
    assert "emotionally hard personal conversation" in hard
    assert "repair a strained or broken relationship" in repair
    assert "protect themselves" in protect
    assert len({hard, repair, protect}) == 3
    # one-question-at-a-time contract survives interpolation
    for p in (hard, repair, protect):
        assert "Ask ONE short, gentle question at a time" in p
        assert '"next_question"' in p and '"done"' in p


def test_pay_vs_purchase_negotiation_stay_distinct():
    pay = _asm("tr_brief_analysis", kind="payNegotiation")["system_prompt"]
    purchase = _asm("tr_brief_analysis", kind="purchaseNegotiation")["system_prompt"]
    assert "negotiating compensation" in pay
    assert "Hiring manager or boss" in pay
    assert "price of a large purchase" in purchase
    assert "Salesperson or vendor" in purchase
    assert pay != purchase


def test_bucket_fallback_for_older_clients_without_kind():
    # kind missing, coarse bucket present → bucket-keyed entry
    by_bucket = _asm("tr_brief_analysis", kind=None, scenario="negotiation")["system_prompt"]
    by_kind = _asm("tr_brief_analysis", kind="payNegotiation")["system_prompt"]
    assert by_bucket == by_kind  # negotiation bucket falls back to pay guidance
    personal = _asm("tr_intake", kind=None, scenario="personal")["system_prompt"]
    assert "emotionally hard personal conversation" in personal


def test_unknown_kind_uses_defaults_without_double_space():
    r = _asm("tr_brief_analysis", kind="somethingNew")["system_prompt"]
    # defaults carry empty guidance — placeholder must vanish cleanly
    assert "{{" not in r
    assert "  The user will play" not in r
    assert "conversation. The user will play" in r
    # NOTE "the The other person" is intentional: the client template reads
    # "the counterpart is the {counterpart}" and TR's table value is
    # "The other person" — the pre-cutover wire is byte-identical, so we
    # preserve the quirk rather than "fix" behavior mid-port.
    assert "the counterpart is the The other person" in r


def test_debrief_job_interview_framing_and_scorecard_contract():
    r = _asm("tr_debrief", kind="jobInterview")["system_prompt"]
    assert "debriefing a job interview answer or exchange" in r
    assert "The counterpart is the Interviewer." in r
    # the five score names are load-bearing enum keys — keep English, in order
    assert '"Clarity"|"Empathy"|"Confidence"|"Boundaries"|"Risk"' in r
    assert "Use exactly those five score names, in that order." in r
    # coarse bucket fallback matches the kind entry
    assert _asm("tr_debrief", scenario="interview")["system_prompt"] == r


def test_rewrite_job_interview_framing():
    r = _asm("tr_rewrite", kind="jobInterview")["system_prompt"]
    assert "use STAR framing when it's a behavioral answer" in r
    assert "The counterpart is the Interviewer." in r
    assert '"rewritten"' in r and '"why"' in r


def test_rewrite_protect_kind_keeps_safety_awareness():
    r = _asm("tr_rewrite", kind="protectConversation")["system_prompt"]
    assert "Be safety-aware" in r
    assert "prioritize the user's safety" in r


def test_resume_enhance_static_honesty_guardrails():
    r = _asm("tr_resume_enhance", kind="jobInterview")["system_prompt"]
    same = _asm("tr_resume_enhance")["system_prompt"]
    assert r == same  # static — no scenario branching
    for phrase in (
        "GROUND STRICTLY IN THE EVIDENCE",
        "Never invent experience, employers, titles, dates, metrics, tools, or outcomes",
        "CHANGE ONLY WHAT THE EVIDENCE TOUCHES",
        "return the résumé unchanged and set \"summary\" to an empty string",
        "Honesty over a forced edit",
        '"enhanced_resume"',
    ):
        assert phrase in r, f"missing honesty guardrail: {phrase!r}"


def test_user_content_passes_through_untouched():
    for ct in FIVE:
        r = _asm(ct, kind="hardConversation")
        assert r["user_content"] == "USER DATA"
        assert r["max_tokens"] == 4096


def test_debrief_calibration_guards():
    """TR calibration round (2026-07-09): ASR framing, anchored dimension
    scores, monotonicity on applied feedback, and permission to return an
    empty what_to_change (kill the feedback treadmill)."""
    cfg = json.load(open("config/remote/techrehearsal/debrief.json"))
    sp = cfg["systemPrompt"]
    for phrase in (
        "dictated speech",
        "NEVER cite transcription artifacts",
        "SCORE ANCHORS",
        "Use the full range",
        "MUST NOT score lower than the previous scores",
        "EMPTY what_to_change list rather than inventing criticism",
        "do not manufacture feedback",
    ):
        assert phrase in sp, f"missing calibration guard: {phrase!r}"
    assert cfg["temperature"] == 0.2
    r = _asm("tr_debrief", kind="jobInterview")
    assert r["temperature"] == 0.2
    # scenario interpolation and the five score names survive the calibration edit
    assert "debriefing a job interview answer or exchange" in r["system_prompt"]
    assert 'Use exactly those five score names, in that order.' in r["system_prompt"]


def test_scenario_kind_normalizer_preserves_case():
    from app.services.usage_tracker import _normalize_scenario_kind

    assert _normalize_scenario_kind(" jobInterview ") == "jobInterview"
    assert _normalize_scenario_kind("payNegotiation") == "payNegotiation"
    assert _normalize_scenario_kind("") is None
    assert _normalize_scenario_kind(None) is None
    assert _normalize_scenario_kind("x" * 100) == "x" * 40
