"""Server-side prompt assembly for call_type-driven requests.

When a client sends call_type without a system_prompt, GP looks up the
prompt config from remote configs and assembles the full system + user
message server-side. This means:
- Prompts are tunable via the admin dashboard without app updates
- The client only sends the user's data (e.g., raw job description)
- Model selection, max_tokens, and hyperparameters are GP-controlled

Config naming convention: the call_type maps to a config slug.
  call_type "tr_parse_jd" → config slug "tr-jd-analysis"

Each prompt config has:
  - systemPrompt: the system message (used as-is)
  - userPromptTemplate: template with {{placeholders}} replaced by user data
  - maxTokens: override for max_tokens (optional)
  - modes: per-prompt_mode overrides (optional). A call type can serve several
    distinct prompts distinguished by the client's prompt_mode meta (e.g.
    tr_response_analysis is both the mid-interview judge and the end-of-session
    scorecard). modes[<prompt_mode>] holds the fields that differ; anything
    absent inherits the top-level value. An unknown or missing prompt_mode gets
    the top-level prompt unchanged.
  - scenarios: per-scenario_kind interpolation (optional). TR's scenario-driven
    prompts embed {{scenario_guidance}} and {{counterpart}} in systemPrompt;
    scenarios[<scenario_kind>] supplies {"guidance", "counterpart"} for the
    fine-grained kind (jobInterview, payNegotiation, hardConversation, ...).
    Lookup order: scenarios[scenario_kind] → scenarios[scenario] (the coarse
    4-bucket analytics tag, as fallback for older clients that don't send the
    kind) → scenarioDefaults. Empty guidance is substituted cleanly (no
    dangling double space). See docs/handoffs/tr-remaining-five-prompts-handoff.md.
"""

import logging
import re

logger = logging.getLogger("ghostpour.prompt_assembly")


class MissingPromptVariables(Exception):
    """A config declared `requiredVariables` and the caller did not send them.

    Raised rather than warned, and the difference matters. The existing
    behaviour on an unreplaced placeholder is a log line plus a prompt
    containing the literal text "{{answer_text}}", and the behaviour when
    assembly returns None is a request that proceeds with NO system prompt.
    Both are fail-OPEN: a model answering immigration questions with no
    instructions is far worse than a refused turn the caller can see.

    Only configs that declare `requiredVariables` can raise this, so every
    existing prompt behaves exactly as before.
    """

    def __init__(self, config_slug: str, missing: list[str]):
        self.config_slug = config_slug
        self.missing = missing
        super().__init__(
            f"{config_slug} requires {missing} and they were not provided")

# Map call_type → config slug for server-side prompt assembly. Post-B2 (#249)
# these are per-app composite slugs (techrehearsal/<name>); _resolve_config
# falls back to the legacy flat `tr-<name>` slug so assembly works whether or
# not the prod persistent dir has been migrated yet.
_CALL_TYPE_TO_CONFIG = {
    "tr_parse_jd": "techrehearsal/jd-analysis",
    "tr_parse_resume": "techrehearsal/resume-analysis",
    "tr_mock_interview": "techrehearsal/mock-interview",
    "tr_response_analysis": "techrehearsal/response-analysis",
    "tr_match_analysis": "techrehearsal/match-analysis",
    "tr_research_interviewer": "techrehearsal/research-interviewer",
    "tr_research_company": "techrehearsal/company-research",
    # The five remaining client prompts (docs/handoffs/tr-remaining-five-prompts-handoff.md)
    "tr_intake": "techrehearsal/intake",
    "tr_brief_analysis": "techrehearsal/brief-analysis",
    "tr_debrief": "techrehearsal/debrief",
    "tr_rewrite": "techrehearsal/rewrite",
    "tr_resume_enhance": "techrehearsal/resume-enhance",
    "tr_compare_reality": "techrehearsal/compare-reality",
    # Live counterpart turns (2026-07-16, Scott's finding: the rehearsal
    # counterpart was a pre-generated script that ignored his answers) —
    # the model plays the other person per turn, in character.
    "tr_counterpart_turn": "techrehearsal/counterpart-turn",
    # N-400 Helper (2026-09-01). Registering it here is what makes the config
    # READ: `interview-turn.json` is server_only, so /v1/config 404s it on
    # purpose, and without this row assemble_prompt returns None and the
    # request proceeds with NO system prompt at all. A prompt config nothing
    # is mapped to is a file, not a lane.
    "n400_interview_turn": "n400/interview-turn",
}


def shadowed_config_keys(
    call_type: str,
    remote_configs: dict,
    prompt_mode: str | None = None,
) -> set[str]:
    """Which served values a client prompt is about to ignore, if any.

    Assembly runs only for promptless calls, so a client that sends its own
    system_prompt silently discards every value we serve for that call type.
    The config keeps looking live in the dashboard while being decoration on
    the wire, which is how a maxTokens fix can be deployed, verified in prod,
    and still not reach the call it was written for. That happened twice on
    LiveRoundScore.

    Returns the config keys that would have applied. Empty when we hold no
    config for this call type, which is the ordinary case for a client
    prompt and not worth a word.
    """
    config_slug = _CALL_TYPE_TO_CONFIG.get(call_type)
    if not config_slug:
        return set()
    config = _resolve_config(config_slug, remote_configs or {})
    if not config:
        return set()
    mode_overrides = (config.get("modes") or {}).get(prompt_mode) if prompt_mode else None
    if mode_overrides:
        config = {**config, **mode_overrides}
    # Only the keys assembly would actually have applied. `modes`, `version`
    # and friends are structure rather than values, and naming them would
    # make the warning noisy enough to ignore.
    return {k for k in ("systemPrompt", "userPromptTemplate", "maxTokens",
                        "temperature", "thinking")
            if config.get(k) not in (None, "")}


def _resolve_config(config_slug: str, remote_configs: dict) -> dict | None:
    """Look up a prompt config by its composite slug, falling back to the
    legacy flat `tr-<name>` slug during the B2 migration window (when the
    prod persistent dir may still hold the prefixed flat file)."""
    cfg = remote_configs.get(config_slug)
    if cfg is not None:
        return cfg
    if "/" in config_slug:
        legacy = "tr-" + config_slug.split("/", 1)[1]
        return remote_configs.get(legacy)
    return None


def _apply_scenario(
    system_prompt: str,
    config: dict,
    scenario_kind: str | None,
    scenario: str | None,
) -> str:
    """Interpolate {{scenario_guidance}} / {{counterpart}} from the config's
    scenarios map. No-op when the config has no scenarios map or the template
    carries no placeholders."""
    scen_map = config.get("scenarios")
    if not scen_map:
        return system_prompt
    entry = (
        (scenario_kind and scen_map.get(scenario_kind))
        or (scenario and scen_map.get(scenario))
        or config.get("scenarioDefaults")
        or {}
    )
    defaults = config.get("scenarioDefaults") or {}
    # Fall back to scenarioDefaults per key, same as rating_anchors below.
    # A scenario entry that defines guidance but not counterpart otherwise
    # renders "You are playing: ." — an empty identity anchor (the 2026-07-17
    # counterpart role-inversion: the model consoled the news-breaker instead
    # of playing the 12-year-old hearing it).
    guidance = entry.get("guidance") or defaults.get("guidance", "")
    counterpart = entry.get("counterpart") or defaults.get("counterpart", "")
    if guidance:
        system_prompt = system_prompt.replace("{{scenario_guidance}}", guidance)
    else:
        # Drop the placeholder AND one adjacent space so an empty guidance
        # doesn't leave "conversation.  The" style double spaces behind.
        system_prompt = system_prompt.replace(" {{scenario_guidance}}", "")
        system_prompt = system_prompt.replace("{{scenario_guidance}} ", "")
        system_prompt = system_prompt.replace("{{scenario_guidance}}", "")
    # {{rating_anchors}}: per-scenario grading anchors (2026-07-16 grader
    # eval — the STAR rubric was scoring hard conversations; anchors now
    # branch by kind). Falls back to scenarioDefaults so an unknown kind
    # keeps today's behavior instead of shipping an anchorless grader.
    if "{{rating_anchors}}" in system_prompt:
        anchors = entry.get("rating_anchors") or defaults.get("rating_anchors", "")
        system_prompt = system_prompt.replace("{{rating_anchors}}", anchors)
    # {{dimensions}}: the same bug as the anchors above, pointing the other
    # way (2026-08-09, Scott). July branched the RATINGS by kind because a
    # STAR rubric was grading hard conversations. The five DIMENSIONS were
    # left hard-coded, so Clarity/Empathy/Confidence/Boundaries/Judgment,
    # written for a difficult personal conversation, were grading job
    # interviews. Empathy is the wrong virtue there and Boundaries is the
    # right one under a name borrowed from somewhere else.
    #
    # Comparability is preserved where it means something: within a scenario
    # the practice and live scorers interpolate the SAME block, so a
    # rehearsal and the real round stay directly comparable. Across scenario
    # kinds they differ, which is correct, because an interview score and a
    # family-conversation score were never comparable to begin with.
    if "{{dimensions}}" in system_prompt:
        dims = entry.get("dimensions") or defaults.get("dimensions", "")
        system_prompt = system_prompt.replace("{{dimensions}}", dims)
    return system_prompt.replace("{{counterpart}}", counterpart)


def assemble_prompt(
    call_type: str,
    user_content: str,
    remote_configs: dict,
    prompt_mode: str | None = None,
    scenario_kind: str | None = None,
    scenario: str | None = None,
    jurisdiction: str | None = None,
    variables: dict | None = None,
) -> dict | None:
    """Assemble system_prompt + user_content from a prompt config.

    Returns {"system_prompt": ..., "user_content": ..., "max_tokens": ...}
    or None if no config exists for this call_type.

    Three selectors can override fields on the way through, and they compose
    in this order, later winning:

      modes[prompt_mode]           which surface is asking
      jurisdictions[jurisdiction]  where the caller is

    `jurisdictions` (2026-09-01, Scott) is how one call_type serves a
    different prompt by location while the app keeps calling one endpoint.
    It is deliberately the SAME mechanism as `modes` rather than a new one:
    variants live inside one document, so the dashboard shows Texas and the
    default side by side in a single editor and there is no file per state
    per language to drift apart. An absent or unrecognised jurisdiction
    inherits the base prompt, which is the safe direction: a location we
    have not written a variant for gets the general one rather than nothing.

    `variables` supplies {{placeholder}} values for the user template. A
    config may declare `requiredVariables`; if it does and any are missing,
    this raises MissingPromptVariables rather than sending a prompt with
    literal braces in it.
    """
    config_slug = _CALL_TYPE_TO_CONFIG.get(call_type)
    if not config_slug:
        return None

    config = _resolve_config(config_slug, remote_configs)
    if not config:
        logger.warning("prompt_assembly: no config for slug %s (call_type=%s)", config_slug, call_type)
        return None

    mode_overrides = (config.get("modes") or {}).get(prompt_mode) if prompt_mode else None
    if mode_overrides:
        config = {**config, **mode_overrides}

    # Location variant. Applied AFTER modes so a jurisdiction can override a
    # surface-specific prompt, which is the order that matches why it exists:
    # the mode says what is being asked, the jurisdiction says what we are
    # allowed to say there.
    juris_key = (jurisdiction or "").strip()
    juris_overrides = (config.get("jurisdictions") or {}).get(juris_key) if juris_key else None
    if juris_overrides:
        config = {**config, **juris_overrides}
        logger.info("prompt_assembly: jurisdiction variant %s applied to %s",
                    juris_key, config_slug)
    elif juris_key and config.get("jurisdictions"):
        logger.info("prompt_assembly: no %s variant for %s, using base",
                    juris_key, config_slug)

    system_prompt = config.get("systemPrompt", "")
    user_template = config.get("userPromptTemplate", "")
    max_tokens = config.get("maxTokens")
    temperature = config.get("temperature")
    thinking = config.get("thinking")

    if not system_prompt:
        logger.warning("prompt_assembly: empty systemPrompt in %s", config_slug)
        return None

    system_prompt = _apply_scenario(system_prompt, config, scenario_kind, scenario)

    # Replace {{placeholders}} in the user template with the raw user content.
    # The primary placeholder varies by config type:
    #   tr-jd-analysis: {{job_description}}
    #   tr-resume-analysis: {{resume_text}}
    # As a fallback, if no known placeholder is found, append user_content
    # to the template.
    if "{{" in user_template:
        # The three legacy names all mean "the single payload the client
        # sent". They predate configs that need more than one value and are
        # kept so every existing prompt substitutes exactly as before.
        assembled_user = user_template
        for legacy in ("{{job_description}}", "{{resume_text}}", "{{user_input}}"):
            assembled_user = assembled_user.replace(legacy, user_content)

        # Named variables, supplied by the caller from request metadata. A
        # config declares what it needs instead of this function growing a
        # hardcoded name per app.
        required = list(config.get("requiredVariables") or [])
        supplied = dict(variables or {})
        # `str(None)` is "None", which is truthy and would have substituted
        # the literal word None into the prompt. Check the value, not its
        # repr. Caught by a parametrized test rather than by reading.
        def _absent(name: str) -> bool:
            value = supplied.get(name)
            return value is None or not str(value).strip()

        missing = [name for name in required if _absent(name)]
        if missing:
            # Fail closed. The alternative is a prompt that reaches a model
            # with "{{known_facts}}" written in it, which reads as a
            # perfectly formed request and is not one.
            logger.error("prompt_assembly: %s missing required variables %s",
                         config_slug, missing)
            raise MissingPromptVariables(config_slug, missing)
        for name, value in supplied.items():
            assembled_user = assembled_user.replace("{{%s}}" % name, str(value))

        # Check if any unreplaced placeholders remain
        remaining = re.findall(r"\{\{(\w+)\}\}", assembled_user)
        if remaining:
            logger.warning("prompt_assembly: unreplaced placeholders in %s: %s", config_slug, remaining)
    else:
        # No template — just use user_content directly
        assembled_user = user_content

    result = {
        "system_prompt": system_prompt,
        "user_content": assembled_user,
    }
    if max_tokens:
        result["max_tokens"] = max_tokens
    # GP-controlled sampling temperature (optional). Low values make a
    # structured call (e.g. tr_parse_jd radar axes) reproducible run-to-run.
    if temperature is not None:
        result["temperature"] = temperature
    # GP-controlled thinking for this lane. "disabled" keeps a short
    # max_tokens budget entirely for the reply on models that think by
    # default; see anthropic_accepts_disabled_thinking().
    if thinking:
        result["thinking"] = thinking

    scenario_note = ""
    if config.get("scenarios"):
        scenario_note = f" kind={scenario_kind or scenario or 'default'}"
    logger.info("prompt_assembly: assembled %s%s%s (system=%d chars, user=%d chars)",
                config_slug, f" mode={prompt_mode}" if mode_overrides else "",
                scenario_note, len(system_prompt), len(assembled_user))
    return result
