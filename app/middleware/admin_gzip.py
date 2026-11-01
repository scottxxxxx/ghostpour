"""Response compression for the admin dashboard and its API, nothing else.

WHY: measured on the live dashboard 2026-10-31, GET /webhooks/admin/users?days=7
was 431 KB on the wire with transfer size equal to decoded size, and
content-encoding was null on every admin response. The app had no compression
middleware at all.

Scoped on purpose. The iOS clients hit /v1 and the chat routes serve SSE and
streaming bodies there. Starlette's GZipMiddleware wraps `send` for every
request it sees, buffers headers until the first body chunk, and rewrites
Content-Length, so even where it declines to compress it still interposes on
the response. None of that may change for /v1 in this PR. So this wrapper
decides on the REQUEST PATH alone, before anything is sent: an admin path is
handed to GZipMiddleware, every other path is handed straight to the inner app
with the original `send`, byte for byte.

Admin paths are /webhooks/admin/* (the dashboard's API) and exactly /admin
(the dashboard HTML, 357 KB served as a FileResponse). /admin/us-states-map.js
is not matched; it carries a week-long cache header and was not part of the
measurement.

No admin endpoint streams (grep app/routers/webhooks.py for StreamingResponse
or EventSourceResponse: none, 2026-11-01). If one is added later it is still
safe: GZipMiddleware never compresses text/event-stream.

Order: registered LAST in app/main.py so it sits OUTERMOST, outside
StreamingBypassMiddleware. That middleware captures response bodies for the
live log and parses them as JSON; it has to see plaintext, so compression must
happen after it has looked.
"""

from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# str.startswith accepts a tuple. The exact-match set is separate because
# "/admin" as a prefix would also catch /admin/us-states-map.js.
_ADMIN_PREFIXES = ("/webhooks/admin/",)
_ADMIN_EXACT = frozenset({"/admin", "/webhooks/admin"})

# Below this many bytes a gzip header costs more than it saves, and the
# dashboard's small responses (set-tier echoes, health pings) are not the
# problem being fixed.
MINIMUM_SIZE = 1024


def is_admin_path(path: str) -> bool:
    return path in _ADMIN_EXACT or path.startswith(_ADMIN_PREFIXES)


class AdminGZipMiddleware:
    """GZipMiddleware for admin paths; a transparent pass-through everywhere else."""

    def __init__(self, app: ASGIApp, minimum_size: int = MINIMUM_SIZE):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and is_admin_path(scope.get("path", "")):
            await self._gzip(scope, receive, send)
            return
        await self.app(scope, receive, send)
