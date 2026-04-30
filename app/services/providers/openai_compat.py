from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter
from .reasoning import openai_compat_fields


class OpenAICompatAdapter(ProviderAdapter):
    """Handles OpenAI, xAI, DeepSeek, Kimi, Qwen — all use OpenAI chat format."""

    async def send_request(self, request: ChatRequest) -> ChatResponse:
        user_content = self._build_user_content(request)

        body: dict = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        body.update(openai_compat_fields(request.provider, request.reasoning))

        headers = self._build_headers()
        status, data, raw_req, raw_resp = await self._post(
            self.base_url, body, headers
        )

        if status != 200:
            error_msg = "Unknown error"
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": f"{request.provider}: {error_msg}",
                    "details": {"status_code": status},
                },
            )

        text = data["choices"][0]["message"]["content"]

        # Capture the full usage block from the provider
        # OpenAI returns: prompt_tokens, completion_tokens, total_tokens,
        # prompt_tokens_details.cached_tokens, completion_tokens_details.reasoning_tokens, etc.
        raw_usage = data.get("usage", {})
        usage = self._flatten_usage(raw_usage)

        # Also capture model-level metadata
        if data.get("id"):
            usage["response_id"] = data["id"]
        if data.get("model"):
            usage["model_version"] = data["model"]
        finish_reason = self._extract_path(data, "choices.0.finish_reason")
        if finish_reason:
            usage["finish_reason"] = finish_reason

        return ChatResponse(
            text=text,
            input_tokens=raw_usage.get("prompt_tokens"),
            output_tokens=raw_usage.get("completion_tokens"),
            model=request.model,
            provider=request.provider,
            usage=usage,
            raw_request_json=raw_req,
            raw_response_json=raw_resp,
        )

    @staticmethod
    def _build_user_content(request: ChatRequest) -> str | list:
        if not request.images:
            return request.user_content

        parts: list[dict] = [{"type": "text", "text": request.user_content}]
        for img_b64 in request.images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                }
            )
        return parts
