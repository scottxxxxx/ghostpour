import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("ghostpour")

# Paths to skip entirely (no buffer entry, no verbose log)
_SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/v1/model-pricing",
               "/webhooks/admin/live-log", "/webhooks/admin/dashboard",
               "/webhooks/admin/configs", "/admin"}

# Max body size to log (prevent huge payloads from flooding logs)
_MAX_BODY_LOG = 10_000

# In-memory ring buffer for recent requests (viewable in dashboard)
_LOG_BUFFER: deque[dict] = deque(maxlen=1000)

_REDACT_KEYS = {"identity_token", "access_token", "refresh_token", "signed_transaction", "client_secret", "password"}


def get_recent_logs(limit: int = 50) -> list[dict]:
    """Return the most recent log entries, newest first."""
    entries = list(_LOG_BUFFER)
    entries.reverse()
    return entries[:limit]


def get_log_by_request_id(request_id: str) -> dict | None:
    """Find a single log entry by its request_id, or None if not in buffer."""
    for entry in _LOG_BUFFER:
        if entry.get("request_id") == request_id:
            return entry
    return None


class StreamingBypassMiddleware:
    """Raw ASGI middleware that bypasses BaseHTTPMiddleware for streaming requests.

    BaseHTTPMiddleware materializes StreamingResponse bodies internally
    (known Starlette limitation), defeating SSE streaming. This wrapper
    intercepts streaming requests at the ASGI level and passes them through
    without body materialization, while still setting request_id and app_id.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Peek at the request body to detect stream: true
        body_chunks = []
        is_stream = False

        async def receive_wrapper():
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                body_chunks.append(body)
                if b'"stream":true' in body or b'"stream": true' in body:
                    nonlocal is_stream
                    is_stream = True
            return message

        # We need to peek at the first receive to check for streaming.
        # But we can only consume receive once, so we buffer and replay.
        first_message = await receive()
        if first_message.get("type") == "http.request":
            body = first_message.get("body", b"")
            if b'"stream":true' in body or b'"stream": true' in body:
                is_stream = True

        if not is_stream:
            # Non-streaming: replay the first message and delegate to the
            # full BaseHTTPMiddleware pipeline
            replayed = False
            async def replay_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return first_message
                return await receive()
            await self.app(scope, replay_receive, send)
            return

        # Streaming: bypass BaseHTTPMiddleware entirely.
        # Set request_id and app_id on scope state, inject X-Request-ID
        # into response headers, log minimal entry, and pass through.
        request_id = uuid.uuid4().hex[:12]
        app_id = "unknown"
        for hdr_name, hdr_val in scope.get("headers", []):
            if hdr_name == b"x-app-id":
                app_id = hdr_val.decode()
                break

        # Store on scope for downstream access via request.state
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id
        scope["state"]["app_id"] = app_id

        start = time.monotonic()
        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                # Inject X-Request-ID header
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        # Replay the first message and delegate directly to the app
        replayed = False
        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return first_message
            return await receive()

        await self.app(scope, replay_receive, send_wrapper)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        path = scope.get("path", "")
        method = scope.get("method", "")
        if path not in _SKIP_PATHS:
            _LOG_BUFFER.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id,
                "app_id": app_id,
                "method": method,
                "path": path,
                "status": 200,
                "latency_ms": elapsed_ms,
                "request": {"body": "(streaming request)"},
                "response": {"body": "(streaming)"},
            })
            logger.info("%s %s 200 %dms (streaming)", method, path, elapsed_ms)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from app.config import get_settings
        verbose = get_settings().verbose_logging

        start = time.monotonic()

        # Generate a request ID and stash it on request.state so handlers
        # can include it in error responses for client-side correlation.
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        request.state.app_id = request.headers.get("X-App-ID", "unknown")

        # Capture request body
        req_body_str = None
        if request.url.path not in _SKIP_PATHS:
            try:
                raw = await request.body()
                if raw:
                    req_body_str = raw.decode("utf-8", errors="replace")[:_MAX_BODY_LOG]
            except Exception:
                req_body_str = "<read error>"

        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Always set X-Request-ID so clients can correlate with GP logs
        response.headers["X-Request-ID"] = request_id

        if request.url.path in _SKIP_PATHS:
            return response

        # Log summary line for non-skipped paths
        logger.info(
            "%s %s %d %dms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )

        # Build request headers (redact auth)
        req_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("authorization", "x-admin-key", "cookie")
        }
        auth = request.headers.get("authorization", "")
        if auth:
            req_headers["authorization"] = auth.split()[0] + " <redacted>" if " " in auth else "<redacted>"

        # For streaming responses (SSE), don't consume the body — return
        # the response as-is so chunks flow to the client immediately.
        is_streaming = response.media_type == "text/event-stream"

        if is_streaming:
            resp_headers = dict(response.headers)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id,
                "app_id": request.state.app_id,
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
                    "body": "(streaming — not captured)",
                },
            }
            _LOG_BUFFER.append(entry)
            return response

        # Non-streaming: capture response body for logging
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk if isinstance(chunk, bytes) else chunk.encode()

        resp_body_str = resp_body.decode("utf-8", errors="replace")[:_MAX_BODY_LOG]
        resp_headers = dict(response.headers)

        # Store in ring buffer (always, for dashboard)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "app_id": request.state.app_id,
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
