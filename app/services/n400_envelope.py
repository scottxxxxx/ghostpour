"""The N-400 interviewer lane's response must be the JSON object, every turn.

conf-v15 turn 76 (2026-09-05): on the applicant's yes to the last section
summary the model returned the whole read-back as plain prose, 644
tokens, finish_reason end_turn, not truncated, just not JSON. The client
could not parse it, showed a failed-turn banner on the last step of the
interview, and when she resent her yes the lane reopened a confirmed
part. The prompt (v17) now names the read-back as the last step that is
still an object; this module is the backstop behind that sentence.

The route retries ONCE with the same request plus a one-line reminder.
Every outcome is metered and visible: the discarded first attempt is
logged with status `envelope_retry`, a successful retry carries
`envelope_retried: true` in the object, and a retry that is also prose is
returned as it came with a warning log, so the audit can count both the
way it counts guard hits.
"""

from __future__ import annotations

import json

CALL_TYPE = "n400_interviewer_turn"

ENVELOPE_REMINDER = (
    "\n\nREMINDER: your previous attempt at this turn was prose, not the JSON "
    "object, and the applicant saw an error. Return ONLY the JSON object "
    "described in your instructions, with the spoken line (the read-back "
    "included, in full) inside `reply`."
)


def is_envelope(text: str | None) -> bool:
    """True when the text is the lane's object: a JSON object with a `reply`."""
    if not text:
        return False
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(obj, dict) and "reply" in obj


def extract_envelope(text: str | None) -> str | None:
    """The object embedded in a response that has prose around it, or None.

    conf-v17: four of five "prose" responses were a paragraph of out-loud
    deliberation FOLLOWED by the complete object. Extracting it costs
    nothing; a second model call costs a turn's money and four seconds of
    silence on the phone. Takes the span from the first brace to the last
    and accepts it only if it parses to the lane's object.
    """
    if not text:
        return None
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    candidate = text[i:j + 1]
    return candidate if is_envelope(candidate) else None


def mark_extracted(text: str) -> str:
    """Add `envelope_extracted: true` so the audit can count these too."""
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(obj, dict):
        return text
    obj["envelope_extracted"] = True
    return json.dumps(obj, ensure_ascii=False)


def mark_retried(text: str) -> str:
    """Add `envelope_retried: true` to an object that came from the retry.

    Visible on purpose: the audit counts these. Returns the text unchanged
    when it is not the object, which cannot happen on the path that calls
    this, and is the safe direction if it ever does.
    """
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(obj, dict):
        return text
    obj["envelope_retried"] = True
    return json.dumps(obj, ensure_ascii=False)
