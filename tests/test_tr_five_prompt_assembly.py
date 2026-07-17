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
    "tr_response_analysis": "techrehearsal/response-analysis",
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


def test_compare_reality_config_and_contract_guards():
    """tr_compare_reality (docs/handoffs/tr-compare-reality-contract.md):
    plan-anchored comparison of a real conversation vs the rehearsal. Guards
    pin the calibration clauses (drifted may be empty, applied coaching never
    reads worse), both capture modes' trust framing, the missing-practice-
    analysis rule, the output contract, and the judge-grade dials."""
    from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG, assemble_prompt

    assert _CALL_TYPE_TO_CONFIG["tr_compare_reality"] == "techrehearsal/compare-reality"
    cfg = json.load(open("config/remote/techrehearsal/compare-reality.json"))
    sp = cfg["systemPrompt"]
    for phrase in (
        # calibration (the debrief-treadmill lessons, contractually required)
        "ANCHORED TO THE PLAN",
        '"drifted" may be EMPTY',
        "Do not invent criticism",
        "never characterize the user as worse off for following the coaching",
        # capture-mode trust framing
        "speech-to-text output",
        "NEVER cite transcription artifacts",
        "self-reported and unverified",
        "never penalize the brevity of the recap",
        # missing practice analysis
        "compare against the REHEARSAL PLAN alone and say so in the verdict",
        # thin-recap calibration (2026-07-10 field failure: a two-line recap
        # produced a wall of red MISSED sections for topics it never mentioned)
        "In a USER RECAP, absence is NOT evidence",
        "do NOT create a section for it",
        "punishes the brevity of the recap",
        "ONE coaching line encouraging a fuller recap",
        "fewer grounded sections always beat padded ones",
        # output contract
        '"delta": one of "landed" | "drifted" | "missed" | "unplanned"',
        '"next_best_focus"',
        "2-4 word noun phrase",

        "no ```json",
    ):
        assert phrase in sp, f"missing contract guard: {phrase!r}"
    # judge-grade dials: reproducible comparisons, analysis-lane routing
    assert cfg["temperature"] == 0.2
    r = json.load(open("config/remote/model-routing.json"))
    models = r["apps"]["techrehearsal"]["call_types"]["tr_compare_reality"]["models"]
    assert all("sonnet" in models[k] for k in ("free", "paid", "default"))

    cfgs = {"techrehearsal/compare-reality": cfg}
    job = assemble_prompt("tr_compare_reality", "BLOB", cfgs, scenario_kind="jobInterview")
    assert "comparing a real job interview against their rehearsal" in job["system_prompt"]
    assert "The counterpart is the Interviewer." in job["system_prompt"]
    assert job["temperature"] == 0.2 and job["max_tokens"] == 4096
    hard = assemble_prompt("tr_compare_reality", "BLOB", cfgs, scenario_kind="hardConversation")
    assert "emotionally hard personal conversation" in hard["system_prompt"]
    assert job["system_prompt"] != hard["system_prompt"]


def test_scenario_kind_normalizer_preserves_case():
    from app.services.usage_tracker import _normalize_scenario_kind

    assert _normalize_scenario_kind(" jobInterview ") == "jobInterview"
    assert _normalize_scenario_kind("payNegotiation") == "payNegotiation"
    assert _normalize_scenario_kind("") is None
    assert _normalize_scenario_kind(None) is None
    assert _normalize_scenario_kind("x" * 100) == "x" * 40


def test_rewrite_mandate_fixes_approach_and_consumes_assessment():
    """Scott 2026-07-16 ('Say it better' returned his weak answer ~90%
    identical): the rewrite must fix the APPROACH, not tidy wording, and
    when the client includes the analysis verdict (HOW IT WAS ASSESSED)
    the rewrite must clear it. The jobInterview persona on his hard
    conversation was the client's scenario_kind bug (relayed), but the
    mandate applies to every scenario."""
    r = _asm("tr_rewrite", kind="hardConversation")["system_prompt"]
    assert "fix the APPROACH" in r
    assert "failed rewrite" in r
    assert "HOW IT WAS ASSESSED" in r
    assert "would no longer apply" in r
    # hard-conversation guidance rides along (the right coach this time)
    assert "empathy" in r.lower()


def test_response_analysis_anchors_branch_by_scenario():
    """Grader eval 2026-07-16: the STAR rubric was scoring hard
    conversations (prod combo was the worst orderer in the eval, rho
    0.762 vs 0.97 calibrated). Anchors now branch by scenario_kind;
    unknown kinds fall back to the interview anchors (today's
    behavior), never an anchorless grader."""
    hard = _asm("tr_response_analysis", kind="hardConversation")["system_prompt"]
    assert "acknowledges what the other person is feeling" in hard
    assert "false reassurance" in hard.lower()
    assert "cannot be below Strong" in hard          # gap-size rule
    assert "STAR arc" not in hard                    # interview rubric gone
    assert "{{rating_anchors}}" not in hard

    interview = _asm("tr_response_analysis", kind="jobInterview")["system_prompt"]
    assert "STAR arc" in interview                   # unchanged for interviews
    assert "Bar Raiser" in interview

    fallback = _asm("tr_response_analysis", kind="somethingNew")["system_prompt"]
    assert "STAR arc" in fallback                    # default = today's grader
    # shared mechanics survive in every variant
    for sp in (hard, interview, fallback):
        assert "per_question" in sp and "TRANSCRIPT NOTE" in sp


def test_counterpart_turn_config():
    """Live counterpart lane (2026-07-16): the rehearsal counterpart was
    a pre-generated script that ignored the user's answers. The model
    now plays the other person per turn, scenario-aware, in character."""
    import json as _json
    from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG
    assert _CALL_TYPE_TO_CONFIG["tr_counterpart_turn"] == "techrehearsal/counterpart-turn"
    cfgs = dict(_cfgs())
    cfgs["techrehearsal/counterpart-turn"] = _json.load(
        open("config/remote/techrehearsal/counterpart-turn.json"))
    r = assemble_prompt("tr_counterpart_turn", "THE BRIEF: ...",
                        cfgs, scenario_kind="hardConversation")
    sp = r["system_prompt"]
    assert "never ask it again" in sp                 # continuity mandate
    assert "shock and disbelief first" in sp          # scenario realism
    assert "conversation_over" in sp                  # JSON contract
    assert "never break character" in sp.lower() or "never break character" in sp
    assert r.get("temperature") == 0.8 and r.get("max_tokens") == 300
