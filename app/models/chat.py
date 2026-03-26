from pydantic import BaseModel


class ChatRequest(BaseModel):
    provider: str
    model: str
    system_prompt: str
    user_content: str
    images: list[str] | None = None
    max_tokens: int | None = None
    call_type: str | None = None        # "query", "summary", "follow_up", "analysis"
    prompt_mode: str | None = None       # "Assist", "Summarize", "Action Items", "Coach", etc.
    image_count: int | None = None       # Explicit count (in case images not sent through gateway)
    session_duration_sec: int | None = None  # How long the meeting session has been running
    # Context Quilt integration
    context_quilt: bool = False          # Enable CQ recall + capture for this request
    meeting_id: str | None = None        # Meeting UUID for CQ queue grouping
    project: str | None = None           # Project display name for CQ metadata
    project_id: str | None = None        # Project UUID (iOS Project.id) for CQ patch grouping
    # Generic feature gating
    skip_teasers: list[str] | None = None  # Feature names to skip teaser checks for


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
