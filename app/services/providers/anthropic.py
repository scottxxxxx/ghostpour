from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    """Anthropic Messages API — custom format, not OpenAI-compatible."""

    async def send_request(self, request: ChatRequest) -> ChatResponse:
        content_parts: list[dict] = []

        # Images before text (Anthropic convention, max 5)
        if request.images:
            for img_b64 in request.images[:5]:
                content_parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    }
                )

        content_parts.append({"type": "text", "text": request.user_content})

        body = {
            "model": request.model,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": content_parts}],
            "max_tokens": request.max_tokens or 4096,
        }

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
                    "message": f"anthropic: {error_msg}",
                    "details": {"status_code": status},
                },
            )

        # Anthropic returns content as array of blocks
        text_parts = [
            block["text"] for block in data.get("content", []) if block.get("type") == "text"
        ]
        text = "\n".join(text_parts) if text_parts else ""

        usage = data.get("usage", {})

        return ChatResponse(
            text=text,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            model=request.model,
            provider=request.provider,
            raw_request_json=raw_req,
            raw_response_json=raw_resp,
        )
