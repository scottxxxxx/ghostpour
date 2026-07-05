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
"""

import logging
import re

logger = logging.getLogger("ghostpour.prompt_assembly")

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
}


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


def assemble_prompt(
    call_type: str,
    user_content: str,
    remote_configs: dict,
) -> dict | None:
    """Assemble system_prompt + user_content from a prompt config.

    Returns {"system_prompt": ..., "user_content": ..., "max_tokens": ...}
    or None if no config exists for this call_type.
    """
    config_slug = _CALL_TYPE_TO_CONFIG.get(call_type)
    if not config_slug:
        return None

    config = _resolve_config(config_slug, remote_configs)
    if not config:
        logger.warning("prompt_assembly: no config for slug %s (call_type=%s)", config_slug, call_type)
        return None

    system_prompt = config.get("systemPrompt", "")
    user_template = config.get("userPromptTemplate", "")
    max_tokens = config.get("maxTokens")
    temperature = config.get("temperature")

    if not system_prompt:
        logger.warning("prompt_assembly: empty systemPrompt in %s", config_slug)
        return None

    # Replace {{placeholders}} in the user template with the raw user content.
    # The primary placeholder varies by config type:
    #   tr-jd-analysis: {{job_description}}
    #   tr-resume-analysis: {{resume_text}}
    # As a fallback, if no known placeholder is found, append user_content
    # to the template.
    if "{{" in user_template:
        # Replace all known placeholders
        assembled_user = user_template
        assembled_user = assembled_user.replace("{{job_description}}", user_content)
        assembled_user = assembled_user.replace("{{resume_text}}", user_content)
        assembled_user = assembled_user.replace("{{user_input}}", user_content)

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

    logger.info("prompt_assembly: assembled %s (system=%d chars, user=%d chars)",
                config_slug, len(system_prompt), len(assembled_user))
    return result
