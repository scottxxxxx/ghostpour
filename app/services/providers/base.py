import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from app.models.chat import ChatRequest, ChatResponse

# Sentinel marker iOS bakes into the protected-prompts systemPromptTemplate
# between the stable system instructions and the per-turn Context Quilt
# enrichment. Only AnthropicAdapter consumes it (to emit two cache_control
# blocks). Every other adapter strips it via _strip_cache_marker so the
# literal sentinel never leaks into the prompt of a non-Anthropic model.
_CACHE_BREAK = "__CQ_BREAK__"


class ProviderAdapter(ABC):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        auth_header: str,
        auth_prefix: str,
        extra_headers: dict[str, str] | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.auth_header = auth_header
        self.auth_prefix = auth_prefix
        self.extra_headers = extra_headers or {}
        self._client: httpx.AsyncClient | None = None

    @abstractmethod
    async def send_request(self, request: ChatRequest) -> ChatResponse:
        ...

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_header and self.api_key:
            headers[self.auth_header] = f"{self.auth_prefix}{self.api_key}"
        headers.update(self.extra_headers)
        return headers

    @staticmethod
    def _pretty_json(obj: dict | list) -> str:
        return json.dumps(obj, indent=2, ensure_ascii=False)

    @staticmethod
    def _strip_cache_marker(system_prompt: str) -> str:
        """Remove the _CACHE_BREAK sentinel from a system prompt.

        The marker is meaningful only to AnthropicAdapter; for any other
        provider it would leak as literal text in the system prompt. Strip
        the marker plus immediately surrounding whitespace, leaving a
        single blank-line separator (matches the v5 template spacing
        between prompt_system_instructions and context_quilt).
        """
        if _CACHE_BREAK not in system_prompt:
            return system_prompt
        return re.sub(
            r"\s*" + re.escape(_CACHE_BREAK) + r"\s*",
            "\n\n",
            system_prompt,
        )

    @staticmethod
    def _redact_base64(json_str: str) -> str:
        """Replace long base64 data strings with [BASE64_REDACTED]."""
        return re.sub(
            r'"data:image/[^;]+;base64,[A-Za-z0-9+/=]{100,}"',
            '"[BASE64_REDACTED]"',
            re.sub(
                r'"data"\s*:\s*"[A-Za-z0-9+/=]{100,}"',
                '"data": "[BASE64_REDACTED]"',
                json_str,
            ),
        )

    @staticmethod
    def _extract_path(data: dict, path: str):
        """Extract a value from a nested dict using dot notation.

        Examples:
            _extract_path(data, "usage.prompt_tokens") -> data["usage"]["prompt_tokens"]
            _extract_path(data, "choices.0.message.content") -> data["choices"][0]["message"]["content"]

        Returns None if any key in the path is missing.
        """
        current = data
        for key in path.split("."):
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list):
                try:
                    current = current[int(key)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    @staticmethod
    def _flatten_usage(usage_obj: dict, prefix: str = "") -> dict:
        """Recursively flatten a nested usage dict into dot-notation keys.

        Example: {"prompt_tokens_details": {"cached_tokens": 5}}
             ->  {"prompt_tokens_details.cached_tokens": 5}
        """
        flat: dict = {}
        for k, v in usage_obj.items():
            full_key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
            if isinstance(v, dict):
                flat.update(ProviderAdapter._flatten_usage(v, full_key))
            elif v is not None:
                flat[full_key] = v
        return flat

    def _get_client(self) -> httpx.AsyncClient:
        """Return a shared HTTP client, creating it on first use.

        Reusing the client keeps TCP connections alive across requests,
        eliminating ~200-400ms of DNS + TLS overhead per call.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client. Called on app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _post(
        self, url: str, body: dict, headers: dict
    ) -> tuple[int, dict, str, str]:
        """POST to provider and return (status, parsed_json, raw_request, raw_response).

        raw_request has base64 redacted for readability.
        """
        raw_request = self._redact_base64(self._pretty_json(body))

        client = self._get_client()
        resp = await client.post(url, json=body, headers=headers)

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text}

        raw_response = self._pretty_json(resp_json)
        return resp.status_code, resp_json, raw_request, raw_response

    async def _post_stream(
        self, url: str, body: dict, headers: dict
    ) -> AsyncIterator[str]:
        """POST to provider with streaming and yield SSE lines.

        Yields raw SSE lines (e.g., 'event: content_block_delta\\ndata: {...}').
        The caller is responsible for parsing provider-specific event formats.
        """
        client = self._get_client()
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                await resp.aread()
                raise httpx.HTTPStatusError(
                    f"Provider returned {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            async for line in resp.aiter_lines():
                if line:
                    yield line

    async def send_request_stream(
        self, request: "ChatRequest"
    ) -> AsyncIterator[dict]:
        """Stream a chat request. Yields event dicts.

        Default implementation falls back to non-streaming (yields one event).
        Providers override this with real streaming support.
        """
        response = await self.send_request(request)
        yield {
            "type": "text",
            "text": response.text,
            "done": True,
            "response": response,
        }
