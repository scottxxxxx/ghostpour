"""GP-owned prompts for TR's mock-interview and response-analysis calls.

These two call types were TR-authored client prompts; we now serve the
system prompt from config/remote/tr-*.json and assemble it server-side
when the client omits its own system_prompt. The user's data blob (role +
resume + briefs, or role + Q&A transcript) passes through unchanged —
userPromptTemplate is empty, so GP owns the instructions and the client
keeps owning the data.
"""

import json

from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG, assemble_prompt

CASES = [
    ("tr_mock_interview", "techrehearsal/mock-interview", "You are an expert technical interviewer"),
    ("tr_response_analysis", "techrehearsal/response-analysis", "You are an interview coach"),
    ("tr_match_analysis", "techrehearsal/match-analysis", "You are an expert technical recruiter"),
    ("tr_research_interviewer", "techrehearsal/research-interviewer", "You are looking at a screenshot"),
]


def _load_configs():
    cfgs = {}
    for _, slug, _ in CASES:
        cfgs[slug] = json.load(open(f"config/remote/{slug}.json"))
    return cfgs


def test_call_types_are_mapped():
    for call_type, slug, _ in CASES:
        assert _CALL_TYPE_TO_CONFIG.get(call_type) == slug


def test_parse_jd_zero_temperature_passes_through_assembly():
    # tr_parse_jd runs at temperature 0.0 so the radar axes are reproducible
    # run-to-run; assembly must surface it so chat.py can set it on the request.
    # 0.0 is falsy — this also guards the `is not None` plumbing end to end.
    cfg = json.load(open("config/remote/techrehearsal/jd-analysis.json"))
    assert cfg["temperature"] == 0.0
    assembled = assemble_prompt("tr_parse_jd", "JD TEXT", {"tr-jd-analysis": cfg})
    assert assembled["temperature"] == 0.0


def test_match_low_temperature_passes_through_assembly():
    # tr_match_analysis carries 0.3: the radar numbers must be stable across
    # the strengthen-loop re-match, while example_excerpt prose stays natural.
    cfg = json.load(open("config/remote/techrehearsal/match-analysis.json"))
    assert cfg["temperature"] == 0.3
    assembled = assemble_prompt("tr_match_analysis", "DATA", {"tr-match-analysis": cfg})
    assert assembled["temperature"] == 0.3


def test_temperature_absent_when_config_omits_it():
    # Configs without a temperature key must not inject one (provider default).
    # mock-interview deliberately omits it — question variety across runs is a
    # feature there, not jitter.
    cfg = json.load(open("config/remote/techrehearsal/mock-interview.json"))
    assert "temperature" not in cfg
    assembled = assemble_prompt("tr_mock_interview", "DATA", {"tr-mock-interview": cfg})
    assert "temperature" not in assembled


def test_configs_have_required_shape():
    for _, slug, head in CASES:
        cfg = json.load(open(f"config/remote/{slug}.json"))
        assert cfg["systemPrompt"].startswith(head)
        assert "version" in cfg and isinstance(cfg["version"], int)
        assert cfg.get("maxTokens") == 4096
        assert cfg.get("recommendedModel") == "claude-sonnet-4-6"
        # Empty template => client data blob passes through verbatim.
        assert cfg.get("userPromptTemplate") == ""


def test_assembles_system_and_passes_user_through():
    cfgs = _load_configs()
    for call_type, _, head in CASES:
        r = assemble_prompt(call_type, "RAW CLIENT DATA BLOB", cfgs)
        assert r is not None
        assert r["system_prompt"].startswith(head)
        assert r["user_content"] == "RAW CLIENT DATA BLOB"  # passthrough, no template
        assert r["max_tokens"] == 4096


def test_match_prompt_keeps_calibration_guardrails():
    """The match prompt's anti-optimism calibration is the point — guard it
    so an edit can't silently strip it back to a naive scorer."""
    cfg = json.load(open("config/remote/techrehearsal/match-analysis.json"))
    sp = cfg["systemPrompt"]
    for phrase in (
        "Use the FULL range",
        "the radar must agree with BOTH your gaps list and your strengths list",
        # Radar carries two comparable series per axis (role bar vs you).
        "role_level",
        "candidate_level",
        "candidate_level MUST be at least 0.35 below",
        # role_level is anchored to the parse demand weights, rescaled to the
        # top axis so the with-résumé and no-résumé rings line up exactly.
        "ANCHORED to the demand weights",
        "divide each axis's demand weight by the LARGEST",
        # a documented strength must not render as a phantom gap.
        "do not show a phantom gap on a documented strength",
        "Never invent skills",
        "no ```json",  # anti-fence instruction must survive
    ):
        assert phrase in sp, f"missing calibration guardrail: {phrase!r}"


def test_match_prompt_gaps_carry_closeability_guidance():
    """Each gap must tell the user whether sharing real experience can close it
    and, when it can, give a concrete example of what to share — so users don't
    waste effort on gaps nothing they say could satisfy."""
    cfg = json.load(open("config/remote/techrehearsal/match-analysis.json"))
    sp = cfg["systemPrompt"]
    # the gap object schema exposes the closeability + Strengthen fields
    for field in ('"closeable": boolean', '"share_prompt": string', '"example_excerpt": string'):
        assert field in sp, f"gap schema missing field: {field!r}"
    # the guidance distinguishes closeable-by-evidence from hard requirements
    assert "closeable = true when" in sp
    assert "closeable = false when nothing they could say" in sp
    # and tells the model to split a proprietary core from its adjacent skill
    assert "split them" in sp
    # example_excerpt is an editable first-person draft, empty when not closeable
    assert "a concrete, first-person draft" in sp
    assert "edit to match what they actually did" in sp


def test_interviewer_assembly_preserves_image():
    """tr_research_interviewer is a vision call: the LinkedIn screenshot
    rides in `images`, separate from the prompt. The chat handler assembles
    by model_copy-ing only system_prompt/user_content/max_tokens, so the
    image must survive. Guard that invariant."""
    from app.models.chat import ChatRequest

    cfgs = {"techrehearsal/research-interviewer": json.load(open("config/remote/techrehearsal/research-interviewer.json"))}
    body = ChatRequest(
        provider="auto", model="auto", user_content="Screenshot attached. Produce the brief.",
        images=["BASE64IMAGEDATA"], call_type="tr_research_interviewer",
    )
    assembled = assemble_prompt("tr_research_interviewer", body.user_content, cfgs)
    assert assembled is not None
    updated = body.model_copy(update={
        "system_prompt": assembled["system_prompt"],
        "user_content": assembled["user_content"],
    })
    assert updated.system_prompt.startswith("You are looking at a screenshot")
    assert updated.images == ["BASE64IMAGEDATA"]  # image preserved through assembly


def test_response_analysis_follow_up_mode_gets_judge_prompt():
    """tr_response_analysis serves TWO prompts distinguished by prompt_mode:
    the mid-interview follow-up judge (InterviewFollowUp) and the end-of-session
    scorecard (InterviewScorecard / default). #273 ported only the scorecard, so
    post-cutover judge calls silently got the scorecard schema back and the TR
    client never asked follow-ups. Guard the mode split and the judge contract."""
    cfg = json.load(open("config/remote/techrehearsal/response-analysis.json"))
    cfgs = {"techrehearsal/response-analysis": cfg}

    judge = assemble_prompt("tr_response_analysis", "Q&A", cfgs, prompt_mode="InterviewFollowUp")
    assert judge["system_prompt"].startswith("You are a seasoned, kind interviewer")
    for field in ('"should_follow_up": boolean', '"follow_up": string', '"stalled": boolean'):
        assert field in judge["system_prompt"], f"judge contract missing: {field!r}"
    assert "per_question" not in judge["system_prompt"]
    # fields absent from the mode override inherit the top level
    assert judge["max_tokens"] == 4096
    assert judge["user_content"] == "Q&A"  # data blob still passes through


def test_response_analysis_scorecard_and_default_get_scorecard_prompt():
    cfg = json.load(open("config/remote/techrehearsal/response-analysis.json"))
    cfgs = {"techrehearsal/response-analysis": cfg}
    for mode in ("InterviewScorecard", None, "SomeFutureMode"):
        r = assemble_prompt("tr_response_analysis", "Q&A", cfgs, prompt_mode=mode)
        assert r["system_prompt"].startswith("You are an interview coach grading a full mock interview")
        assert '"per_question"' in r["system_prompt"]
        assert "should_follow_up" not in r["system_prompt"]


def test_scorecard_calibration_guards():
    """TR calibration round (2026-07-09): ASR-noise framing, observable tier
    anchors (top tier not held in reserve), and a mechanical overall derived
    from the tier mix — guard so an edit can't regress the judge back to a
    compressed, unanchored scale."""
    cfg = json.load(open("config/remote/techrehearsal/response-analysis.json"))
    sp = cfg["systemPrompt"]
    for phrase in (
        "speech-to-text transcript",
        "NEVER cite transcription artifacts",
        "Judge what the candidate plainly meant",
        "do not hold the top tier in reserve",
        "missing at most ONE Bar Raiser element",
        "Bar Raiser 95, Strong 80, Meets 60, Weak 35",
        "Different tier mixes MUST produce different overalls",
    ):
        assert phrase in sp, f"missing calibration guard: {phrase!r}"
    # judge determinism: temperature pinned and flows through assembly for
    # BOTH modes (modes inherit absent fields, incl. temperature)
    assert cfg["temperature"] == 0.2
    cfgs = {"techrehearsal/response-analysis": cfg}
    scorecard = assemble_prompt("tr_response_analysis", "X", cfgs, prompt_mode="InterviewScorecard")
    judge = assemble_prompt("tr_response_analysis", "X", cfgs, prompt_mode="InterviewFollowUp")
    assert scorecard["temperature"] == 0.2 and judge["temperature"] == 0.2
    # the follow-up judge PROMPT is untouched by the scorecard calibration
    assert "speech-to-text transcript" not in judge["system_prompt"]
    assert judge["system_prompt"].startswith("You are a seasoned, kind interviewer")


def test_returns_none_when_config_absent():
    # Mirrors today's behavior before deploy: no config => no server assembly,
    # so the client's own prompt is used (nothing breaks until cutover).
    assert assemble_prompt("tr_mock_interview", "x", {}) is None
