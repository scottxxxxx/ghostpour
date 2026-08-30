from typing import Any, Literal

from pydantic import BaseModel, model_validator


_CHAT_META_FIELDS = (
    "call_type", "prompt_mode", "image_count", "session_duration_sec",
    "meeting_id", "project", "project_id", "locale", "transcript_source",
)


# The reasoning value sent on the wire is one of the literal strings in
# the model's `reasoningLevels` array in model-capabilities.json. iOS picks
# one of those values; the value is passed through to the provider's native
# field by app/services/providers/reasoning.py.
#
# Values are provider-native and differ per model:
#   OpenAI gpt-5.5/5.2     → none | low | medium | high | xhigh
#   OpenAI gpt-5-mini/nano → minimal | low | medium | high
#   Anthropic effort path  → low | medium | high | xhigh (Opus 4.7) | max
#   Gemini 3 Flash/Lite    → minimal | low | medium | high
#   Gemini 3 Pro           → low | medium | high
#   xAI Grok               → none | low | medium | high
#   Kimi (k2.5/k2.6)       → disabled | enabled
#   DeepSeek V4            → disabled | enabled
#
# No normalization; the adapter passes the value through verbatim. Models
# without a string-vocabulary (Anthropic Haiku integer, Qwen bool, Gemini 2.5
# integer) are `supportsReasoning: false` in model-capabilities.json — the
# picker is hidden for them.
ReasoningLevel = str  # any literal from the model's reasoningLevels array


class DocumentAttachment(BaseModel):
    """A user-attached file passed through raw (#359 documents spec).

    `data` is base64 of the file exactly as picked — no client conversion.
    Caps are defined against the RAW byte size (before base64), served in
    client-config's `documents` key and enforced server-side.
    """
    name: str = ""       # user-visible filename, reused in prompt framing
    media_type: str      # declared MIME type
    data: str            # base64 of raw file bytes


class ChatRequest(BaseModel):
    provider: str
    model: str
    system_prompt: str = ""
    user_content: str
    images: list[str] | None = None
    documents: list[DocumentAttachment] | None = None
    # GP-supplied tool definitions. Only the Anthropic adapter reads
    # these, which is sufficient because every lane that sets them is
    # already gated to provider == "anthropic". Used by the artifact
    # contract lane so the column schema is enforced at the API boundary
    # rather than hoped for in a prompt: measured 2026-08-15, a fixed
    # schema took the expected-result column from 1 of 3 runs to 3 of 3.
    tools: list[dict] | None = None
    tool_choice: dict | None = None
    # Server-set ONLY (phase 2a document generation): the chat router
    # overwrites this from its gate on every request — a client-sent value
    # never survives. When True the anthropic adapter arms the execution
    # sandbox + document skills and collects generated artifacts.
    generation: bool = False
    max_tokens: int | None = None
    temperature: float | None = None  # GP-controlled; None => provider default
    stream: bool = False
    # Client-minted id for one USER-AUTHORED turn, resent unchanged on every
    # retry of that turn and re-minted whenever the user edits the text or
    # changes the attachments (2026-08-30). Keyed with user_id, it makes a
    # retry a lookup instead of a second model call and a second 400 KB
    # upload. Absent means today's behaviour exactly, so every shipped build
    # is unaffected and this is additive on the wire.
    turn_id: str | None = None
    reasoning: ReasoningLevel | None = None
    # GP-controlled lane setting, NOT the user-facing picker above. Only
    # "disabled" is meaningful today: models that think by default (Sonnet 5,
    # Opus 5) share `max_tokens` between thinking and the reply, which starves
    # short-output lanes — tr_counterpart_turn caps at 300. Set from the prompt
    # config's `thinking` key; None => whatever the model does by default.
    thinking: str | None = None

    # Generic metadata dict — apps can pass any key-value pairs.
    # Known keys used by existing clients: call_type, prompt_mode,
    # session_duration_sec, meeting_id, project, project_id, image_count.
    metadata: dict[str, Any] | None = None

    # Context Quilt integration (generic feature gating)
    context_quilt: bool = False          # Enable CQ recall + capture for this request
    skip_teasers: list[str] | None = None  # Feature names to skip teaser checks for

    # --- Backwards-compatible top-level fields ---
    # These are copied into metadata by the validator below.
    # Existing clients can keep sending them at the top level.
    call_type: str | None = None
    prompt_mode: str | None = None
    image_count: int | None = None
    session_duration_sec: int | None = None
    meeting_id: str | None = None
    project: str | None = None
    project_id: str | None = None
    locale: str | None = None
    # Source of the raw transcript when call_type=="analysis": "ocr_captions",
    # "speech_to_text", or "mixed". Drives server-side cleanup routing; see
    # app.services.transcript_cleanup. Absent for non-analysis calls.
    transcript_source: str | None = None
    # Conversation-scoped text references (handoff Part 6): the assembled
    # injection blocks, byte-identical across turns while the chip set is
    # unchanged. Rendered as an own cached content part on the anthropic
    # path; folded into user_content elsewhere. Add-only optimization —
    # clients that concatenate into user_content lose nothing but the
    # cache discount.
    reference_text: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _copy_top_level_to_metadata(cls, data: Any) -> Any:
        """Copy top-level app-specific fields into metadata dict.

        This lets existing clients send fields at the top level while new
        clients can use metadata: {...} directly. Both paths end up in the
        same place.
        """
        if not isinstance(data, dict):
            return data

        meta = dict(data.get("metadata") or {})
        for field in _CHAT_META_FIELDS:
            val = data.get(field)
            if val is not None and field not in meta:
                meta[field] = val
        if meta:
            data["metadata"] = meta
        return data

    def get_meta(self, key: str, default: Any = None) -> Any:
        """Read a value from metadata, falling back to top-level field."""
        if self.metadata and key in self.metadata:
            return self.metadata[key]
        return getattr(self, key, default)


class ChatResponse(BaseModel):
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str
    provider: str
    # Abstract tier label ("standard" | "advanced" | "free") that clients
    # should render instead of `model`. Set by the route handler from the
    # user's subscription tier — NOT derived from the model identity, so we
    # can swap models per tier without breaking iOS attribution.
    ai_tier: str | None = None
    usage: dict | None = None
    # Why the model stopped, normalised across providers: "complete",
    # "max_tokens", "filtered", "tool_use", or an unrecognised provider value
    # passed through lowercased. ABSENT when the provider reported none,
    # which means "we do not know" rather than "it completed": treating a
    # missing value as success is how a truncation gets rendered as an
    # answer. See app/services/stop_reason.py.
    stop_reason: str | None = None
    cost: dict | None = None
    raw_request_json: str | None = None
    raw_response_json: str | None = None
    # Optional cleaned-up transcript when the server ran a captions/STT
    # cleanup pass before the main LLM call. Present only when the
    # transcript_source metadata field was set AND the cleanup feature
    # was enabled for this request. iOS falls back to its raw transcript
    # when this field is absent.
    cleaned_transcript: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
