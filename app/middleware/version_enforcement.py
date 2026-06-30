"""Force-upgrade enforcement middleware (#force-version-gate).

Rejects below-floor / blocklisted builds with HTTP 426 across the LLM, Context
Quilt, and config paths when an app's `min_supported_blocking` flag is on. The
decision is made purely from request headers (X-App-ID / X-App-Version /
X-App-Build) before the handler runs, so it cuts a session off mid-flight.

Pure ASGI (NOT BaseHTTPMiddleware): when it doesn't block, it passes the request
straight through without wrapping the response, so SSE/streaming on /v1/chat is
untouched — the same reason request logging here is pure-ASGI.

Exempt by omission: only the prefixes below are enforced. /v1/app/version and
/auth are NOT, so a blocked client can always fetch the new floor + upgrade URL
and refresh tokens to recover. All decisioning (and its fail-open safety) lives
in app/services/version_gate.py.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from app.routers.config import load_apps
from app.services import version_gate

# LLM, Context Quilt (cq_proxy), and config. str.startswith accepts a tuple.
_ENFORCED_PREFIXES = ("/v1/chat", "/v1/config", "/v1/quilt", "/v1/capture-transcript")


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode()
    return None


class VersionEnforcementMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(_ENFORCED_PREFIXES):
            await self.app(scope, receive, send)
            return

        state = getattr(scope.get("app"), "state", None)
        registry = getattr(state, "app_versions", {}) or {}
        block = version_gate.evaluate(
            registry,
            load_apps(),
            _header(scope, b"x-app-id"),
            _header(scope, b"x-app-version"),
            _header(scope, b"x-app-build"),
        )
        if block is None:
            await self.app(scope, receive, send)
            return

        body = json.dumps(block).encode()
        await send({
            "type": "http.response.start",
            "status": 426,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
