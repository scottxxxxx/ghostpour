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

# Map call_type → config slug for server-side prompt assembly
_CALL_TYPE_TO_CONFIG = {
    "tr_parse_jd": "tr-jd-analysis",
    "tr_parse_resume": "tr-resume-analysis",
    "tr_mock_interview": "tr-mock-interview",
    "tr_response_analysis": "tr-response-analysis",
}


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

    config = remote_configs.get(config_slug)
    if not config:
        logger.warning("prompt_assembly: no config for slug %s (call_type=%s)", config_slug, call_type)
        return None

    system_prompt = config.get("systemPrompt", "")
    user_template = config.get("userPromptTemplate", "")
    max_tokens = config.get("maxTokens")

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

    logger.info("prompt_assembly: assembled %s (system=%d chars, user=%d chars)",
                config_slug, len(system_prompt), len(assembled_user))
    return result
