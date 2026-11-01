"""gzip on the admin surface only (app/middleware/admin_gzip.py).

Measured on the live dashboard 2026-10-31: GET /webhooks/admin/users?days=7
was 431 KB with transfer size equal to decoded size and content-encoding null
on every admin response. These tests pin the fix AND its boundary: the iOS
clients hit /v1 and the chat routes stream SSE there, and none of that may
change.

httpx (the TestClient) sends `Accept-Encoding: gzip, deflate` by default and
decodes transparently, so `resp.content` is always the decoded body and
`content-length` is the wire size. A request that must NOT ask for gzip sends
`Accept-Encoding: identity` explicitly.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from app.middleware.admin_gzip import AdminGZipMiddleware, MINIMUM_SIZE, is_admin_path

ADMIN = {"X-Admin-Key": "test-admin-key"}
GZIP = {"Accept-Encoding": "gzip"}
IDENTITY = {"Accept-Encoding": "identity"}


# --- (a) the admin API compresses and round-trips ----------------------------

def test_admin_tiers_is_gzipped_and_decodes_to_the_identity_body(client):
    plain = client.get("/webhooks/admin/tiers", headers={**ADMIN, **IDENTITY})
    assert plain.status_code == 200
    assert plain.headers.get("content-encoding") is None
    # Precondition for the claim below: the payload is over the floor.
    assert len(plain.content) >= MINIMUM_SIZE

    resp = client.get("/webhooks/admin/tiers", headers={**ADMIN, **GZIP})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.content == plain.content
    # Transfer size is now smaller than decoded size, the observable that was
    # equal on the live page.
    assert int(resp.headers["content-length"]) < len(resp.content)
    # A cache in front must key on the request's encoding.
    assert "accept-encoding" in resp.headers.get("vary", "").lower()


def test_admin_dashboard_page_is_gzipped(client):
    """/admin is a FileResponse (357 KB of HTML); it goes out in chunks, so
    this is GZipMiddleware's streaming branch and Content-Length is dropped."""
    plain = client.get("/admin", headers=IDENTITY)
    resp = client.get("/admin", headers=GZIP)
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.content == plain.content
    assert resp.headers["cache-control"] == plain.headers["cache-control"]


# --- (b) nothing outside the admin surface is touched -------------------------

@pytest.mark.parametrize("path", ["/health", "/openapi.json", "/admin/us-states-map.js"])
def test_non_admin_paths_are_never_compressed(client, path):
    """/health is the spec'd probe but it is under the floor on its own.
    /openapi.json (about 200 KB) and /admin/us-states-map.js (120 KB, the
    sibling that an "/admin" prefix would have caught) are well over it, so a
    missing content-encoding there is the exclusion and not the size rule."""
    plain = client.get(path, headers=IDENTITY)
    resp = client.get(path, headers=GZIP)
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") is None
    assert "vary" not in resp.headers
    assert resp.content == plain.content
    if "content-length" in plain.headers:
        assert resp.headers["content-length"] == plain.headers["content-length"]
        assert int(resp.headers["content-length"]) == len(resp.content)


# --- (c) small admin responses stay plain -------------------------------------

def test_small_admin_response_is_not_compressed(client):
    """The CSV export on an empty database is one header line (61 bytes)."""
    resp = client.get("/webhooks/admin/subscriptions/export.csv", headers={**ADMIN, **GZIP})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert len(resp.content) < MINIMUM_SIZE
    assert resp.headers.get("content-encoding") is None
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert resp.text.startswith("month,")


# --- the CSV export over the floor ---------------------------------------------

def _seed_subscriber(db_path: str, effective_at: str):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (id, apple_sub, email, tier, created_at, updated_at, is_active,
            monthly_used_usd, overage_balance_usd)
           VALUES ('A', 'sub_A', 'a@t.co', 'plus', ?, ?, 1, 0, 0)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO subscription_events
           (id, user_id, event_type, from_tier, to_tier, original_transaction_id,
            source, environment, effective_at, recorded_at)
           VALUES (?, 'A', 'subscribed', NULL, 'plus', NULL, 'assn', 'Production', ?, ?)""",
        (str(uuid.uuid4()), effective_at, effective_at),
    )
    conn.commit()
    conn.close()


def test_csv_export_over_the_floor_compresses_and_round_trips(client, tmp_db_path):
    """One subscriber since 2020 means one CSV row per calendar month since,
    which puts the export over the floor."""
    _seed_subscriber(tmp_db_path, "2020-01-01T00:00:00+00:00")
    plain = client.get("/webhooks/admin/subscriptions/export.csv", headers={**ADMIN, **IDENTITY})
    assert plain.status_code == 200
    assert len(plain.content) >= MINIMUM_SIZE
    resp = client.get("/webhooks/admin/subscriptions/export.csv", headers={**ADMIN, **GZIP})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.content == plain.content
    assert resp.text.splitlines()[0].startswith("month,")
    assert int(resp.headers["content-length"]) < len(resp.content)


# --- (d) streaming on /v1 is untouched ----------------------------------------

def test_v1_chat_sse_is_untouched(client, pro_user):
    """The real route, the real mock provider. SSE bodies are below the floor
    here and GZipMiddleware never compresses text/event-stream anyway, so a
    missing content-encoding is necessary but not sufficient; the ASGI test
    below is the one that proves the wrapper is not even in the path."""
    body = {"provider": "anthropic", "model": "claude-haiku-4-5-20251001",
            "system_prompt": "x", "user_content": "hi", "stream": True}
    resp = client.post("/v1/chat", json=body, headers={**pro_user["headers"], **GZIP})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("content-encoding") is None
    assert "vary" not in resp.headers
    assert resp.text.startswith("data: ")


@pytest.mark.asyncio
async def test_wrapper_hands_a_non_admin_request_the_original_send():
    """Byte for byte means the wrapper never interposes: the inner app gets
    the very `send` the server gave us, and every message object the handler
    sends is the one the server receives, in order, unmodified."""
    seen_send = []
    chunks = [
        {"type": "http.response.start", "status": 200,
         "headers": [(b"content-type", b"text/event-stream")]},
        {"type": "http.response.body", "body": b"data: " + b"x" * 2048 + b"\n\n", "more_body": True},
        {"type": "http.response.body", "body": b"data: done\n\n", "more_body": True},
        {"type": "http.response.body", "body": b"", "more_body": False},
    ]

    async def handler(scope, receive, send):
        seen_send.append(send)
        for m in chunks:
            await send(m)

    received = []

    async def send(message):
        received.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/v1/chat",
             "headers": [(b"accept-encoding", b"gzip")]}
    await AdminGZipMiddleware(handler)(scope, receive, send)

    assert seen_send == [send]
    assert len(received) == len(chunks)
    for got, sent in zip(received, chunks):
        assert got is sent
    assert received[0]["headers"] == [(b"content-type", b"text/event-stream")]


@pytest.mark.asyncio
async def test_wrapper_does_interpose_on_an_admin_request():
    """The control for the test above: same handler, admin path, and the
    inner app now sees GZipMiddleware's send, not the server's."""
    seen_send = []

    async def handler(scope, receive, send):
        seen_send.append(send)
        body = b"{" + b"x" * 4096 + b"}"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    received = []

    async def send(message):
        received.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "GET", "path": "/webhooks/admin/users",
             "headers": [(b"accept-encoding", b"gzip")]}
    await AdminGZipMiddleware(handler)(scope, receive, send)

    assert seen_send and seen_send[0] is not send
    headers = dict(received[0]["headers"])
    assert headers[b"content-encoding"] == b"gzip"
    assert int(headers[b"content-length"]) < 4098


@pytest.mark.parametrize("path,expected", [
    ("/admin", True),
    ("/webhooks/admin", True),
    ("/webhooks/admin/users", True),
    ("/webhooks/admin/subscriptions/export.csv", True),
    ("/admin/us-states-map.js", False),
    ("/webhooks/adminx", False),
    ("/webhooks/apple", False),
    ("/v1/chat", False),
    ("/v1/admin", False),
    ("/health", False),
    ("/", False),
])
def test_is_admin_path(path, expected):
    assert is_admin_path(path) is expected
