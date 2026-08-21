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

# Some payloads are structurally bigger than the default cap and are also
# the ones most worth seeing whole. A CQ person detail measured 67,386
# bytes on the wire, so at 10,000 the live log showed the first 15% of the
# surface most likely to carry a passthrough bug, and showed it as a
# truncated STRING (the JSON no longer parses), which is the worst of both.
# Raised for those routes only, not globally: the cap exists to stop a
# fleet of ordinary requests from flooding memory, and that reason still
# holds everywhere else.
_MAX_BODY_LOG_LARGE = 131_072
_LARGE_BODY_PREFIXES = ("/v1/people", "/v1/quilt")

# Total captured body bytes the buffer may hold. Raising the per-entry cap
# 13x means the old "1000 entries" bound is no longer a memory bound on its
# own, so make the real constraint explicit: oldest entries are evicted
# until the buffer fits. Sized so a full buffer of large payloads costs
# tens of MB, not hundreds.
_MAX_BUFFER_BYTES = 48 * 1024 * 1024
_BUFFER_ENTRIES = 1000

# In-memory ring buffer for recent requests (viewable in dashboard).
# maxlen is enforced in _buffer_append rather than by the deque so entry
# count and byte budget are evicted by the same rule.
_LOG_BUFFER: deque[dict] = deque()
_BUFFER_BYTES = 0

# Private, stripped before any entry is handed out. Carries the captured
# size so eviction does not have to re-measure a parsed body.
_SIZE_KEY = "_captured_bytes"


def _body_cap(path: str) -> int:
    """Per-path body capture cap."""
    if any(path.startswith(p) for p in _LARGE_BODY_PREFIXES):
        return _MAX_BODY_LOG_LARGE
    return _MAX_BODY_LOG


def _clip(raw: bytes | str | None, cap: int) -> str | None:
    """Decode and cap, marking the cut instead of hiding it.

    Silent truncation reads as a complete payload, which is exactly how a
    missing key gets blamed on the wrong side of a wire.
    """
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n... [truncated at {cap} chars, {len(text)} total]"


def _buffer_append(entry: dict, size: int = 0) -> None:
    """Append and evict to stay inside BOTH bounds."""
    global _BUFFER_BYTES
    entry[_SIZE_KEY] = size
    _LOG_BUFFER.append(entry)
    _BUFFER_BYTES += size
    while _LOG_BUFFER and (
        len(_LOG_BUFFER) > _BUFFER_ENTRIES or _BUFFER_BYTES > _MAX_BUFFER_BYTES
    ):
        _BUFFER_BYTES -= _LOG_BUFFER.popleft().get(_SIZE_KEY, 0)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != _SIZE_KEY}

# Redact any field whose name contains one of these substrings. Matching
# broadly (not an exact-name list) so new sensitive wire fields are redacted
# by default instead of silently logged. Only string values are redacted, so
# numeric fields like max_tokens/prompt_tokens pass through untouched.
_REDACT_SUBSTRINGS = ("token", "secret", "password", "key", "signed_transaction", "credential")


def get_recent_logs(limit: int = 50) -> list[dict]:
    """Return the most recent log entries, newest first."""
    entries = list(_LOG_BUFFER)
    entries.reverse()
    return [_public(e) for e in entries[:limit]]


def get_log_by_request_id(request_id: str) -> dict | None:
    """Find a single log entry by its request_id, or None if not in buffer."""
    for entry in _LOG_BUFFER:
        if entry.get("request_id") == request_id:
            return _public(entry)
    return None


class StreamingBypassMiddleware:
    """Pure ASGI middleware for request logging that supports SSE streaming.

    Replaces BaseHTTPMiddleware which materializes StreamingResponse bodies.
    Decides whether to capture the response body based on the response's
    actual content-type — not the request's `stream:true` flag — so handlers
    that override streaming (e.g. ProjectChat returning JSON to a request
    with `stream:true`) log their real body and aren't mislabeled "(streaming)".
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Meeting share tokens ARE the URL and carry no other credential, so
        # a path log line is a token leak. Mask them here, at the one place
        # every request's path is written (2026-08-21, share privacy line).
        if path.startswith("/s/"):
            rest = path[3:].split("/", 1)
            path = "/s/<token>" + ("/" + rest[1] if len(rest) > 1 else "")
        method = scope.get("method", "")

        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        request_id = uuid.uuid4().hex[:12]
        app_id = "unknown"
        for hdr_name, hdr_val in scope.get("headers", []):
            if hdr_name == b"x-app-id":
                app_id = hdr_val.decode()
                break

        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id
        scope["state"]["app_id"] = app_id
        # Mirror into the contextvar for service-layer CQ auth. This must
        # happen HERE, in pure ASGI: this middleware awaits the downstream
        # app in the same context, so the value reaches handlers and the
        # create_task background work they spawn. A set inside a
        # BaseHTTPMiddleware.dispatch does NOT reach handlers (the app runs
        # outside dispatch's context) — verified against starlette 0.52.
        from app.request_context import current_app_id
        current_app_id.set(app_id)

        # Peek at request body
        first_message = await receive()
        req_body = b""
        if first_message.get("type") == "http.request":
            req_body = first_message.get("body", b"")

        cap = _body_cap(path)
        req_body_str = _clip(req_body, cap)

        # Build request headers (redact auth)
        req_headers = {}
        for hdr_name, hdr_val in scope.get("headers", []):
            name = hdr_name.decode().lower()
            if name not in ("authorization", "x-admin-key", "cookie"):
                req_headers[name] = hdr_val.decode()
            elif name == "authorization":
                val = hdr_val.decode()
                req_headers["authorization"] = val.split()[0] + " <redacted>" if " " in val else "<redacted>"

        replayed = False
        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return first_message
            return await receive()

        response_status = 200
        response_headers: dict[str, str] = {}
        response_body = b""
        is_streaming = False

        async def capture_send(message):
            nonlocal response_status, response_headers, response_body, is_streaming
            if message["type"] == "http.response.start":
                response_status = message.get("status", 200)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
                response_headers = {h[0].decode(): h[1].decode() for h in headers}
                # Decide based on actual response content-type, not the
                # request's `stream:true` flag — handlers may override it.
                ct = response_headers.get("content-type", "")
                if ct.startswith("text/event-stream"):
                    is_streaming = True
            elif message["type"] == "http.response.body" and not is_streaming:
                response_body += message.get("body", b"")
            await send(message)

        await self.app(scope, replay_receive, capture_send)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if is_streaming:
            resp_section: dict = {"headers": response_headers, "body": "(streaming)"}
            log_suffix = " (streaming)"
            resp_body_str = None
        else:
            resp_body_str = _clip(response_body, cap)
            resp_section = {"headers": response_headers,
                            "body": _format_body_parsed(resp_body_str, cap)}
            log_suffix = ""

        _buffer_append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "app_id": app_id,
            "method": method,
            "path": path,
            "status": response_status,
            "latency_ms": elapsed_ms,
            "client_ip": req_headers.get("x-real-ip", ""),
            "user_agent": req_headers.get("user-agent", ""),
            "request": {"headers": req_headers,
                        "body": _format_body_parsed(req_body_str, cap)},
            "response": resp_section,
        }, len(req_body_str or "") + len(resp_body_str or ""))
        logger.info("%s %s %d %dms%s", method, path, response_status, elapsed_ms, log_suffix)


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
        # NOTE: this class is NOT registered on the app (see main.py — only
        # StreamingBypassMiddleware runs, which also owns the current_app_id
        # contextvar set; a set here wouldn't reach handlers anyway, since
        # BaseHTTPMiddleware runs the app outside dispatch's context).

        # Capture request body
        cap = _body_cap(request.url.path)
        req_body_str = None
        if request.url.path not in _SKIP_PATHS:
            try:
                raw = await request.body()
                if raw:
                    req_body_str = _clip(raw, cap)
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
                    "body": _format_body_parsed(req_body_str, cap),
                },
                "response": {
                    "headers": resp_headers,
                    "body": "(streaming — not captured)",
                },
            }
            _buffer_append(entry, len(req_body_str or ""))
            return response

        # Non-streaming: capture response body for logging
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk if isinstance(chunk, bytes) else chunk.encode()

        resp_body_str = _clip(resp_body, cap)
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
                "body": _format_body_parsed(req_body_str, cap),
            },
            "response": {
                "headers": resp_headers,
                "body": _format_body_parsed(resp_body_str, cap),
            },
        }
        _buffer_append(entry, len(req_body_str or "") + len(resp_body_str or ""))

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


def _format_body_parsed(body: str | None, cap: int = _MAX_BODY_LOG):
    """Parse body to dict/list for JSON storage. Redacts sensitive fields.

    A body that was clipped no longer parses, so it lands here as a raw
    string with the truncation marker still on it. That is the honest
    outcome: better a payload that says where it was cut than one that
    looks whole.
    """
    if not body:
        return None
    try:
        parsed = json.loads(body)
        _redact_sensitive(parsed)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return body[:cap]


def _format_body(body: str | None) -> str:
    """Verbose FILE logging. Deliberately still capped at the small
    default: the raised cap exists so the dashboard can show a payload
    whole, not so journald carries 128KB per request."""
    if not body:
        return "<empty>"
    try:
        parsed = json.loads(body)
        _redact_sensitive(parsed)
        return json.dumps(parsed, indent=2, ensure_ascii=False)[:_MAX_BODY_LOG]
    except (json.JSONDecodeError, TypeError):
        return body[:_MAX_BODY_LOG]


def _is_sensitive_key(key) -> bool:
    return isinstance(key, str) and any(s in key.lower() for s in _REDACT_SUBSTRINGS)


def _redact_sensitive(obj):
    """Recursively redact sensitive fields in a dict.

    Values are replaced entirely — no prefix is kept. A 20-char prefix of a
    password or client_secret can be the whole value, and token prefixes are
    stable identifiers; the request_id is the correlation handle instead.
    """
    if isinstance(obj, dict):
        for key in obj:
            if _is_sensitive_key(key) and isinstance(obj[key], str):
                obj[key] = "<redacted>"
            elif isinstance(obj[key], (dict, list)):
                _redact_sensitive(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _redact_sensitive(item)
