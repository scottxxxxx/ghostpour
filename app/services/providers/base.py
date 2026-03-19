import json
import re
from abc import ABC, abstractmethod

import httpx

from app.models.chat import ChatRequest, ChatResponse


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

    async def _post(
        self, url: str, body: dict, headers: dict
    ) -> tuple[int, dict, str, str]:
        """POST to provider and return (status, parsed_json, raw_request, raw_response).

        raw_request has base64 redacted for readability.
        """
        raw_request = self._redact_base64(self._pretty_json(body))

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body, headers=headers)

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text}

        raw_response = self._pretty_json(resp_json)
        return resp.status_code, resp_json, raw_request, raw_response
