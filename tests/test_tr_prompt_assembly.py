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
        "the radar must agree with your gaps list",
        "MUST be <= 0.5",
        "Never invent skills",
        "no ```json",  # anti-fence instruction must survive
    ):
        assert phrase in sp, f"missing calibration guardrail: {phrase!r}"


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


def test_returns_none_when_config_absent():
    # Mirrors today's behavior before deploy: no config => no server assembly,
    # so the client's own prompt is used (nothing breaks until cutover).
    assert assemble_prompt("tr_mock_interview", "x", {}) is None
