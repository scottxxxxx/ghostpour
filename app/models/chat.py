from pydantic import BaseModel


class ChatRequest(BaseModel):
    provider: str
    model: str
    system_prompt: str
    user_content: str
    images: list[str] | None = None
    max_tokens: int | None = None


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
