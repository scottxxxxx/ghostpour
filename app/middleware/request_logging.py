import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ghostpour")

# Paths to never log bodies for (sensitive or noisy)
_SKIP_BODY_PATHS = {"/health", "/docs", "/openapi.json", "/v1/model-pricing"}

# Max body size to log (prevent huge payloads from flooding logs)
_MAX_BODY_LOG = 10_000

# In-memory ring buffer for recent requests (viewable in dashboard)
_LOG_BUFFER: deque[dict] = deque(maxlen=200)

_REDACT_KEYS = {"identity_token", "access_token", "refresh_token", "signed_transaction", "client_secret", "password"}


def get_recent_logs(limit: int = 50) -> list[dict]:
    """Return the most recent log entries, newest first."""
    entries = list(_LOG_BUFFER)
    entries.reverse()
    return entries[:limit]


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from app.config import get_settings
        verbose = get_settings().verbose_logging

        start = time.monotonic()

        # Capture request body
        req_body_str = None
        if request.url.path not in _SKIP_BODY_PATHS:
            try:
                raw = await request.body()
                if raw:
                    req_body_str = raw.decode("utf-8", errors="replace")[:_MAX_BODY_LOG]
            except Exception:
                req_body_str = "<read error>"

        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Always log the summary line
        logger.info(
            "%s %s %d %dms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )

        if request.url.path in _SKIP_BODY_PATHS:
            return response

        # Build request headers (redact auth)
        req_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("authorization", "x-admin-key", "cookie")
        }
        auth = request.headers.get("authorization", "")
        if auth:
            req_headers["authorization"] = auth.split()[0] + " <redacted>" if " " in auth else "<redacted>"

        # Capture response body by reading the stream
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk if isinstance(chunk, bytes) else chunk.encode()

        resp_body_str = resp_body.decode("utf-8", errors="replace")[:_MAX_BODY_LOG]
        resp_headers = dict(response.headers)

        # Store in ring buffer (always, for dashboard)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query) if request.url.query else None,
            "status": response.status_code,
            "latency_ms": elapsed_ms,
            "client_ip": request.headers.get("x-real-ip", request.client.host if request.client else "unknown"),
            "user_agent": request.headers.get("user-agent", ""),
            "request": {
                "headers": req_headers,
                "body": _format_body_parsed(req_body_str),
            },
            "response": {
                "headers": resp_headers,
                "body": _format_body_parsed(resp_body_str),
            },
        }
        _LOG_BUFFER.append(entry)

        # Verbose file logging
        if verbose:
            logger.info(
                ">>> %s %s\n    Headers: %s\n    Body: %s",
                request.method,
                str(request.url),
                json.dumps(req_headers, indent=2),
                _format_body(req_body_str),
            )
            logger.info(
                "<<< %d %dms\n    Headers: %s\n    Body: %s",
                response.status_code,
                elapsed_ms,
                json.dumps(resp_headers, indent=2),
                _format_body(resp_body_str),
            )

        # Return a new response with the consumed body
        return Response(
            content=resp_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


def _format_body_parsed(body: str | None):
    """Parse body to dict/list for JSON storage. Redacts sensitive fields."""
    if not body:
        return None
    try:
        parsed = json.loads(body)
        _redact_sensitive(parsed)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return body[:_MAX_BODY_LOG]


def _format_body(body: str | None) -> str:
    if not body:
        return "<empty>"
    try:
        parsed = json.loads(body)
        _redact_sensitive(parsed)
        return json.dumps(parsed, indent=2, ensure_ascii=False)[:_MAX_BODY_LOG]
    except (json.JSONDecodeError, TypeError):
        return body[:_MAX_BODY_LOG]


def _redact_sensitive(obj):
    """Recursively redact sensitive fields in a dict."""
    if isinstance(obj, dict):
        for key in obj:
            if key in _REDACT_KEYS and isinstance(obj[key], str):
                obj[key] = obj[key][:20] + "...<redacted>"
            elif isinstance(obj[key], (dict, list)):
                _redact_sensitive(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _redact_sensitive(item)
