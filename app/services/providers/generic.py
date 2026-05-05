"""Config-driven generic adapter for providers not covered by built-in adapters.

Users define response_mappings in providers.yml to tell GhostPour how to extract
text, tokens, and usage from any provider's response JSON. No code changes needed.

Example providers.yml entry:

  my_new_provider:
    display_name: "My Provider"
    api_format: "generic"
    base_url: "https://api.myprovider.com/v1/chat"
    auth_header: "Authorization"
    auth_prefix: "Bearer "
    env_key: "my_provider_api_key"
    request_format:
      model_field: "model"
      system_prompt_field: "system"
      messages_field: "messages"
      system_in_messages: false
      max_tokens_field: "max_tokens"
      image_format: "openai"  # "openai", "anthropic", "gemini", or "none"
    response_mappings:
      text: "choices.0.message.content"
      input_tokens: "usage.prompt_tokens"
      output_tokens: "usage.completion_tokens"
      finish_reason: "choices.0.finish_reason"
      response_id: "id"
      model_version: "model"
    usage_paths:
      - "usage"
"""

from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatResponse

from .base import ProviderAdapter


class GenericAdapter(ProviderAdapter):
    """Config-driven adapter for arbitrary LLM providers."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        auth_header: str,
        auth_prefix: str,
        extra_headers: dict[str, str] | None = None,
        request_format: dict | None = None,
        response_mappings: dict[str, str] | None = None,
        usage_paths: list[str] | None = None,
    ):
        super().__init__(api_key, base_url, auth_header, auth_prefix, extra_headers)
        self.request_format = request_format or {}
        self.response_mappings = response_mappings or {
            "text": "choices.0.message.content",
            "input_tokens": "usage.prompt_tokens",
            "output_tokens": "usage.completion_tokens",
        }
        self.usage_paths = usage_paths or ["usage"]

    async def send_request(self, request: ChatRequest) -> ChatResponse:
        body = self._build_request_body(request)
        url = self._build_url(request)
        headers = self._build_headers()

        status, data, raw_req, raw_resp = await self._post(url, body, headers)

        if status != 200:
            # Try common error formats
            error_msg = (
                self._extract_path(data, "error.message")
                or self._extract_path(data, "error.type")
                or self._extract_path(data, "message")
                or f"HTTP {status}"
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": f"{request.provider}: {error_msg}",
                    "details": {"status_code": status},
                },
            )

        # Extract text using configured path
        text = self._extract_path(data, self.response_mappings.get("text", ""))
        if text is None:
            text = ""
        if isinstance(text, list):
            # Handle array responses (e.g., Anthropic content blocks)
            text = " ".join(str(t) for t in text)

        # Extract token counts
        input_tokens = self._extract_path(
            data, self.response_mappings.get("input_tokens", "")
        )
        output_tokens = self._extract_path(
            data, self.response_mappings.get("output_tokens", "")
        )

        # Build usage dict from all configured usage paths
        usage: dict = {}
        for usage_path in self.usage_paths:
            raw_usage = self._extract_path(data, usage_path)
            if isinstance(raw_usage, dict):
                usage.update(self._flatten_usage(raw_usage))

        # Extract any additional mapped fields into usage
        for key, path in self.response_mappings.items():
            if key not in ("text", "input_tokens", "output_tokens"):
                val = self._extract_path(data, path)
                if val is not None:
                    usage[key] = val

        return ChatResponse(
            text=str(text),
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            model=request.model,
            provider=request.provider,
            usage=usage,
            raw_request_json=raw_req,
            raw_response_json=raw_resp,
        )

    def _build_url(self, request: ChatRequest) -> str:
        """Build the URL, substituting {model} if present."""
        return self.base_url.replace("{model}", request.model)

    def _build_request_body(self, request: ChatRequest) -> dict:
        """Build the request body from the configured format."""
        fmt = self.request_format
        model_field = fmt.get("model_field", "model")
        messages_field = fmt.get("messages_field", "messages")
        max_tokens_field = fmt.get("max_tokens_field", "max_tokens")
        system_in_messages = fmt.get("system_in_messages", True)
        system_prompt_field = fmt.get("system_prompt_field")
        image_format = fmt.get("image_format", "openai")

        user_content = self._build_user_content(request, image_format)
        system_prompt = (
            self._strip_cache_marker(request.system_prompt)
            if request.system_prompt
            else ""
        )

        messages = []
        if system_in_messages and system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        body: dict = {
            model_field: request.model,
            messages_field: messages,
        }

        # System prompt as top-level field (Anthropic style)
        if system_prompt_field and not system_in_messages:
            body[system_prompt_field] = system_prompt

        if request.max_tokens:
            body[max_tokens_field] = request.max_tokens

        return body

    @staticmethod
    def _build_user_content(request: ChatRequest, image_format: str) -> str | list:
        if not request.images or image_format == "none":
            return request.user_content

        parts: list[dict] = []

        if image_format == "anthropic":
            # Images before text
            for img_b64 in request.images[:5]:
                parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_b64,
                    },
                })
            parts.append({"type": "text", "text": request.user_content})
        elif image_format == "gemini":
            parts.append({"text": request.user_content})
            for img_b64 in request.images:
                parts.append(
                    {"inlineData": {"mimeType": "image/jpeg", "data": img_b64}}
                )
        else:
            # Default: OpenAI format
            parts.append({"type": "text", "text": request.user_content})
            for img_b64 in request.images:
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                })

        return parts
