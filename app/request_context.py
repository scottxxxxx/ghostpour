"""Request-scoped context available outside the request object.

`current_app_id` carries the caller's X-App-ID (as set on
`request.state.app_id` by the request-logging middleware) into code that
has no `request` in scope: service-layer helpers and `asyncio.create_task`
background work, which copies the contextvar context at creation time.

Consumers treat it as a fallback only — an explicitly passed app_id always
wins. Outside a request (startup, cron) it reads None, which downstream
resolves to the default identity.
"""

from contextvars import ContextVar

current_app_id: ContextVar[str | None] = ContextVar("current_app_id", default=None)
