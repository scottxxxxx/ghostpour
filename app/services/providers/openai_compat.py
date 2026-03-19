from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter


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
        usage = data.get("usage", {})

        return ChatResponse(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            model=request.model,
            provider=request.provider,
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
