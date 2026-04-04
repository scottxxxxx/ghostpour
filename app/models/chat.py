from typing import Any

from pydantic import BaseModel, model_validator


_CHAT_META_FIELDS = (
    "call_type", "prompt_mode", "image_count", "session_duration_sec",
    "meeting_id", "project", "project_id",
)


class ChatRequest(BaseModel):
    provider: str
    model: str
    system_prompt: str
    user_content: str
    images: list[str] | None = None
    max_tokens: int | None = None
    stream: bool = False

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
    usage: dict | None = None
    cost: dict | None = None
    raw_request_json: str | None = None
    raw_response_json: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
