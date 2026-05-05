import json
from collections.abc import AsyncIterator

from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter
from .reasoning import anthropic_min_max_tokens, anthropic_thinking_block


class AnthropicAdapter(ProviderAdapter):
    """Anthropic Messages API — custom format, not OpenAI-compatible."""

    async def send_request(self, request: ChatRequest) -> ChatResponse:
        body, headers = self._build_body(request)
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

        # Capture the full usage block from the provider
        # Anthropic returns: input_tokens, output_tokens,
        # cache_creation_input_tokens, cache_read_input_tokens.
        # When web_search is enabled, also: server_tool_use.web_search_requests.
        raw_usage = data.get("usage", {})
        usage = self._flatten_usage(raw_usage)

        # Count web_search invocations from the content blocks. Anthropic
        # emits one `server_tool_use` block per search with name="web_search".
        # We mirror this into a top-level usage key so the chat router's
        # gate can increment the per-user counter without re-parsing the
        # raw content array.
        search_count = sum(
            1 for block in data.get("content", [])
            if block.get("type") == "server_tool_use"
            and block.get("name") == "web_search"
        )
        if search_count:
            usage["web_search_requests"] = search_count

        # Also capture response-level metadata
        if data.get("id"):
            usage["response_id"] = data["id"]
        if data.get("model"):
            usage["model_version"] = data["model"]
        if data.get("stop_reason"):
            usage["finish_reason"] = data["stop_reason"]

        return ChatResponse(
            text=text,
            input_tokens=raw_usage.get("input_tokens"),
            output_tokens=raw_usage.get("output_tokens"),
            model=request.model,
            provider=request.provider,
            usage=usage,
            raw_request_json=raw_req,
            raw_response_json=raw_resp,
        )

    def _build_body(self, request: ChatRequest) -> tuple[dict, dict]:
        """Build Anthropic request body and headers. Shared by stream and non-stream."""
        content_parts: list[dict] = []
        if request.images:
            for img_b64 in request.images[:5]:
                content_parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
                })
        content_parts.append({"type": "text", "text": request.user_content})

        system_block = [{
            "type": "text",
            "text": request.system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]

        max_tokens = request.max_tokens or 4096
        thinking = anthropic_thinking_block(request.reasoning)
        if thinking:
            # Anthropic requires budget_tokens < max_tokens; lift if needed.
            max_tokens = max(max_tokens, anthropic_min_max_tokens(request.reasoning))

        body = {
            "model": request.model,
            "system": system_block,
            "messages": [{"role": "user", "content": content_parts}],
            "max_tokens": max_tokens,
        }
        if thinking:
            body["thinking"] = thinking

        # Web search tool — gated upstream by the chat router. The router
        # only sets search_enabled=True after passing tier + cap checks,
        # so the adapter trusts the flag and just attaches Anthropic's
        # native web_search_20250305 tool. max_uses bounds how many
        # searches a single turn can make (otherwise one query could
        # burn through the user's monthly cap).
        if request.get_meta("search_enabled"):
            body["tools"] = [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }
            ]

        return body, self._build_headers()

    async def send_request_stream(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Stream an Anthropic Messages API request, yielding event dicts.

        Yields:
          {"type": "text", "text": "chunk", "done": False}  — for each text delta
          {"type": "text", "text": "", "done": True, "response": ChatResponse}  — at end
        """
        body, headers = self._build_body(request)
        body["stream"] = True

        raw_request = self._redact_base64(self._pretty_json(body))

        # Accumulate response data
        full_text = ""
        input_tokens = 0
        output_tokens = 0
        response_id = ""
        model_version = ""
        stop_reason = ""
        cache_creation = 0
        cache_read = 0

        try:
            async for line in self._post_stream(self.base_url, body, headers):
                # Anthropic SSE: "event: <type>" followed by "data: <json>"
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type")

                    if event_type == "message_start":
                        msg = event.get("message", {})
                        response_id = msg.get("id", "")
                        model_version = msg.get("model", "")
                        usage = msg.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        cache_creation = usage.get("cache_creation_input_tokens", 0)
                        cache_read = usage.get("cache_read_input_tokens", 0)

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            chunk = delta.get("text", "")
                            full_text += chunk
                            yield {"type": "text", "text": chunk, "done": False}

                    elif event_type == "message_delta":
                        delta = event.get("delta", {})
                        stop_reason = delta.get("stop_reason", "")
                        usage = event.get("usage", {})
                        output_tokens = usage.get("output_tokens", output_tokens)

        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail={"code": "provider_error", "message": f"anthropic stream: {e}"},
            )

        # Build final ChatResponse for logging/cost calculation
        usage_dict = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "response_id": response_id,
            "model_version": model_version,
            "finish_reason": stop_reason,
        }

        final_response = ChatResponse(
            text=full_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=request.model,
            provider=request.provider,
            usage=usage_dict,
            raw_request_json=raw_request,
            raw_response_json=None,  # Not available in streaming
        )

        yield {"type": "text", "text": "", "done": True, "response": final_response}
