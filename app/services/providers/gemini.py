from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter
from .reasoning import gemini_thinking_config


class GeminiAdapter(ProviderAdapter):
    """Google Gemini generateContent API — custom format."""

    async def send_request(self, request: ChatRequest) -> ChatResponse:
        # Gemini puts model ID in URL path
        url = f"{self.base_url}/{request.model}:generateContent"

        user_parts: list[dict] = [{"text": request.user_content}]
        if request.images:
            for img_b64 in request.images:
                user_parts.append(
                    {"inlineData": {"mimeType": "image/jpeg", "data": img_b64}}
                )

        body: dict = {
            "contents": [{"role": "user", "parts": user_parts}],
        }
        if request.system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}]
            }
        thinking_config = gemini_thinking_config(request.reasoning)
        if thinking_config is not None:
            body.setdefault("generationConfig", {})["thinkingConfig"] = thinking_config

        headers = self._build_headers()
        status, data, raw_req, raw_resp = await self._post(url, body, headers)

        if status != 200:
            error_msg = data.get("error", {}).get("message", "Unknown error")
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": f"google: {error_msg}",
                    "details": {"status_code": status},
                },
            )

        # Check for safety blocks
        prompt_feedback = data.get("promptFeedback", {})
        if prompt_feedback.get("blockReason"):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": (
                        f"google: Prompt blocked — {prompt_feedback['blockReason']}"
                    ),
                },
            )

        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": "google: No candidates returned",
                },
            )

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")
        if finish_reason in ("SAFETY", "RECITATION"):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": f"google: Response blocked — {finish_reason}",
                },
            )

        # Extract text from parts
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)

        # Capture the full usage block from the provider
        # Gemini returns: promptTokenCount, candidatesTokenCount, totalTokenCount,
        # cachedContentTokenCount, thoughtsTokenCount
        raw_usage = data.get("usageMetadata", {})
        usage = self._flatten_usage(raw_usage)

        # Also capture response-level metadata
        if finish_reason:
            usage["finish_reason"] = finish_reason

        return ChatResponse(
            text=text,
            input_tokens=raw_usage.get("promptTokenCount"),
            output_tokens=raw_usage.get("candidatesTokenCount"),
            model=request.model,
            provider=request.provider,
            usage=usage,
            raw_request_json=raw_req,
            raw_response_json=raw_resp,
        )
