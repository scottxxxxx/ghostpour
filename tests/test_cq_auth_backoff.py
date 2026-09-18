"""Stop asking CQ for a token once they have said no (2026-08-07, CQ).

Success was cached and failure was not, so with a wrong secret every
proxied request minted a fresh token. CQ's /v1/auth/token verifies with
pbkdf2_sha256, deliberately CPU costly, and has no rate limiting. A bad
credential did not merely fail: it generated sustained load on the one
endpoint designed to be slow, at one hash per request.

CQ framed it as our 401-to-502 translation inviting client retries, since
a 401 is permanent and clients do not retry it while a 502 is transient and
they do. It is worse than that. No retrying client is required. Ordinary
traffic was the load.

The distinction the fix rests on: a rejected credential is PERMANENT until
a human changes it, so asking again sooner has no upside. A timeout might
succeed next call, so it gets no cooldown.
"""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import context_quilt as cq

APP = "shouldersurf"


@pytest.fixture(autouse=True)
def _clean():
    cq._tokens.clear()
    cq._auth_failures.clear()
    cq._auth_strikes.clear()
    yield
    cq._tokens.clear()
    cq._auth_failures.clear()
    cq._auth_strikes.clear()


def _identity(monkeypatch, app_id="cq-app", secret="s3cret"):
    monkeypatch.setattr(cq, "_cq_identity", lambda _a=None: (app_id, secret))
    return app_id


def _client(post):
    c = AsyncMock()
    c.post = post
    return c


def _rejection(status: int):
    req = httpx.Request("POST", "https://cq.example/v1/auth/token")
    resp = httpx.Response(status_code=status, request=req)
    err = httpx.HTTPStatusError("rejected", request=req, response=resp)
    r = AsyncMock()
    r.raise_for_status = lambda: (_ for _ in ()).throw(err)
    return AsyncMock(return_value=r)


def _success(token="tok", expires_in=3600):
    r = AsyncMock()
    r.raise_for_status = lambda: None
    r.json = lambda: {"access_token": token, "expires_in": expires_in}
    return AsyncMock(return_value=r)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_a_rejected_credential_is_asked_for_once(monkeypatch, status):
    """The whole point. Ten proxied requests behind a wrong secret must not
    become ten pbkdf2 hashes on CQ."""
    _identity(monkeypatch)
    post = _rejection(status)
    with patch.object(cq, "_get_client", lambda: _client(post)):
        for _ in range(10):
            headers = await cq._get_auth_headers(APP)
            assert "Authorization" not in headers
    # TWO, not one: the first rejection is forgiven because CQ's token
    # endpoint reports backend outages as credential errors, so a single
    # 401 is not yet evidence of a bad credential.
    assert post.await_count == 2, "the cooldown must engage after the second strike"


@pytest.mark.asyncio
async def test_a_timeout_is_retried_because_it_might_work(monkeypatch):
    """Cooling down a transient failure would extend a network blip into a
    minute of degraded auth for no reason."""
    _identity(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))
    with patch.object(cq, "_get_client", lambda: _client(post)):
        for _ in range(4):
            await cq._get_auth_headers(APP)
    assert post.await_count == 4


@pytest.mark.asyncio
async def test_the_cooldown_expires(monkeypatch):
    _identity(monkeypatch)
    post = _rejection(401)
    with patch.object(cq, "_get_client", lambda: _client(post)):
        await cq._get_auth_headers(APP)
        await cq._get_auth_headers(APP)          # second strike arms it
        assert post.await_count == 2
        cq._auth_failures["cq-app"] = time.time() - 1     # expired
        await cq._get_auth_headers(APP)
    assert post.await_count == 3


@pytest.mark.asyncio
async def test_a_fixed_secret_recovers_without_serving_out_the_cooldown(monkeypatch):
    """A rejection followed by a success must clear the memo. Otherwise
    fixing the secret appears not to work for up to a minute, and somebody
    changes it again."""
    _identity(monkeypatch)
    with patch.object(cq, "_get_client", lambda: _client(_rejection(401))):
        await cq._get_auth_headers(APP)
        await cq._get_auth_headers(APP)
    assert "cq-app" in cq._auth_failures
    cq._auth_failures["cq-app"] = time.time() - 1
    with patch.object(cq, "_get_client", lambda: _client(_success())):
        headers = await cq._get_auth_headers(APP)
    assert headers["Authorization"] == "Bearer tok"
    assert "cq-app" not in cq._auth_failures, "the rejection must be forgotten"


@pytest.mark.asyncio
async def test_the_cooldown_is_per_identity(monkeypatch):
    """SS and TR authenticate as different CQ apps. One rejected secret must
    not stop the other app from authenticating."""
    _identity(monkeypatch, app_id="app-a")
    with patch.object(cq, "_get_client", lambda: _client(_rejection(401))):
        await cq._get_auth_headers("a")
        await cq._get_auth_headers("a")
    _identity(monkeypatch, app_id="app-b")
    post = _success(token="btok")
    with patch.object(cq, "_get_client", lambda: _client(post)):
        headers = await cq._get_auth_headers("b")
    assert headers["Authorization"] == "Bearer btok"
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_a_healthy_token_is_still_cached(monkeypatch):
    """The pre-existing behaviour this must not disturb."""
    _identity(monkeypatch)
    post = _success()
    with patch.object(cq, "_get_client", lambda: _client(post)):
        for _ in range(5):
            headers = await cq._get_auth_headers(APP)
            assert headers["Authorization"] == "Bearer tok"
    assert post.await_count == 1


# --- the two-strike rule (2026-08-08) ---------------------------------
#
# CQ's token endpoint has an outer arm that turns ANY failure into a
# credential error, so a database outage on their side presents as
# "Incorrect client_id or client_secret". They deferred fixing that status
# code during cutover week, which was the right trade in isolation.
#
# Then our cooldown shipped, and the two compose badly: a brief blip on
# their side would read as a permanently wrong credential and stop us
# talking to them for a minute, where before we would have retried and
# recovered instantly. Neither side could see that from its own code.


@pytest.mark.asyncio
async def test_one_rejection_is_forgiven(monkeypatch):
    """A credential does not spontaneously become wrong. A single 401 from
    a backend that reports outages as credential errors is more likely
    their blip than our secret changing under us."""
    _identity(monkeypatch)
    post = _rejection(401)
    with patch.object(cq, "_get_client", lambda: _client(post)):
        await cq._get_auth_headers(APP)
    assert "cq-app" not in cq._auth_failures
    assert cq._auth_strikes["cq-app"] == 1


@pytest.mark.asyncio
async def test_a_blip_between_successes_never_engages_the_cooldown(monkeypatch):
    """The exact scenario: working, one failed mint during an outage,
    working again. Their status code is wrong throughout and it costs us
    nothing."""
    _identity(monkeypatch)
    with patch.object(cq, "_get_client", lambda: _client(_success())):
        await cq._get_auth_headers(APP)
    cq._tokens.clear()                                  # force a re-mint
    with patch.object(cq, "_get_client", lambda: _client(_rejection(401))):
        await cq._get_auth_headers(APP)
    cq._tokens.clear()
    with patch.object(cq, "_get_client", lambda: _client(_success())):
        headers = await cq._get_auth_headers(APP)
    assert headers["Authorization"] == "Bearer tok"
    assert "cq-app" not in cq._auth_failures


@pytest.mark.asyncio
async def test_a_success_clears_the_strike_count(monkeypatch):
    """Otherwise strikes accumulate across unrelated blips hours apart and
    the second one engages a cooldown that the first had already earned
    forgiveness for."""
    _identity(monkeypatch)
    with patch.object(cq, "_get_client", lambda: _client(_rejection(401))):
        await cq._get_auth_headers(APP)
    assert cq._auth_strikes["cq-app"] == 1
    cq._tokens.clear()
    with patch.object(cq, "_get_client", lambda: _client(_success())):
        await cq._get_auth_headers(APP)
    assert "cq-app" not in cq._auth_strikes


@pytest.mark.asyncio
async def test_a_genuinely_wrong_secret_still_stops_fast(monkeypatch):
    """The property we must not lose. Two calls, then silence: not one
    pbkdf2 hash per request for as long as the secret stays wrong."""
    _identity(monkeypatch)
    post = _rejection(401)
    with patch.object(cq, "_get_client", lambda: _client(post)):
        for _ in range(50):
            await cq._get_auth_headers(APP)
    assert post.await_count == 2


# --- the 429 arm (2026-09-18) -----------------------------------------
#
# CQ's token endpoint rate-limits FAILURES: 10 per 900s keyed on client_id,
# then 429 with Retry-After (confirmed on their prod 2026-09-18). Before this,
# a 429 fell through to the transient arm and got retried with NO back-off, the
# one response a rate limit forbids. A 429 is a third thing: not a wrong
# credential (so no strike, no permanent cooldown) and not an instant-retry
# transient (so honour Retry-After).
#
# ⚠ In normal operation this arm is unreachable: the limiter counts only
# failures and a success clears the bucket, and a wrong secret trips our own
# two-strike cooldown at strike 2, long before CQ's tenth failure. Belt-and-
# braces, built because an UNHANDLED 429 is the dangerous default, not because
# the path is live today.


def _rate_limited(retry_after: str | None = "30"):
    req = httpx.Request("POST", "https://cq.example/v1/auth/token")
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    resp = httpx.Response(status_code=429, headers=headers, request=req)
    err = httpx.HTTPStatusError("rate limited", request=req, response=resp)
    r = AsyncMock()
    r.raise_for_status = lambda: (_ for _ in ()).throw(err)
    return AsyncMock(return_value=r)


@pytest.mark.asyncio
async def test_a_429_backs_off_and_does_not_spin(monkeypatch):
    """The whole point. A rate limit must stop us asking again inside the
    window, not become a retry the way a plain transient error would."""
    _identity(monkeypatch)
    post = _rate_limited("30")
    with patch.object(cq, "_get_client", lambda: _client(post)):
        for _ in range(10):
            headers = await cq._get_auth_headers(APP)
            assert "Authorization" not in headers
    # ONE call: the first 429 armed the back-off, and the gate at the top of
    # _get_auth_headers held every call after it. Contrast the transient arm,
    # which POSTs all ten times.
    assert post.await_count == 1
    assert "cq-app" in cq._auth_failures


@pytest.mark.asyncio
async def test_a_429_is_not_a_strike(monkeypatch):
    """A rate limit is not evidence the secret is wrong, so it must not count
    toward the permanent-rejection cooldown. If it did, two 429s would look
    like a wrong credential and cool us down for the wrong reason."""
    _identity(monkeypatch)
    with patch.object(cq, "_get_client", lambda: _client(_rate_limited("5"))):
        await cq._get_auth_headers(APP)
    assert "cq-app" not in cq._auth_strikes


@pytest.mark.asyncio
async def test_the_backoff_honours_retry_after_seconds(monkeypatch):
    _identity(monkeypatch)
    t0 = time.time()
    with patch.object(cq, "_get_client", lambda: _client(_rate_limited("120"))):
        await cq._get_auth_headers(APP)
    until = cq._auth_failures["cq-app"]
    assert 118 <= (until - t0) <= 123, f"expected ~120s back-off, got {until - t0:.1f}"


@pytest.mark.asyncio
async def test_a_missing_retry_after_falls_back_not_to_zero(monkeypatch):
    """Zero would turn a 429 into an instant retry, the exact thing the header
    exists to prevent. A header-less 429 backs off the ordinary cooldown."""
    _identity(monkeypatch)
    t0 = time.time()
    with patch.object(cq, "_get_client", lambda: _client(_rate_limited(None))):
        await cq._get_auth_headers(APP)
    until = cq._auth_failures["cq-app"]
    assert (until - t0) >= cq.AUTH_FAILURE_COOLDOWN_SECONDS - 1


@pytest.mark.asyncio
async def test_a_429_backoff_clears_on_a_later_success(monkeypatch):
    """Unlike a wrong secret, a rate limit passes. Once the window has rolled
    and a mint succeeds, the memo must clear so we are back to bearer auth."""
    _identity(monkeypatch)
    with patch.object(cq, "_get_client", lambda: _client(_rate_limited("30"))):
        await cq._get_auth_headers(APP)
    assert "cq-app" in cq._auth_failures
    cq._auth_failures["cq-app"] = time.time() - 1        # window rolled
    with patch.object(cq, "_get_client", lambda: _client(_success())):
        headers = await cq._get_auth_headers(APP)
    assert headers["Authorization"] == "Bearer tok"
    assert "cq-app" not in cq._auth_failures


def test_parse_retry_after_seconds_form():
    now = 1000.0
    assert cq._parse_retry_after("45", now=now) == 45.0


def test_parse_retry_after_http_date_form():
    from email.utils import format_datetime
    from datetime import datetime, timezone, timedelta
    now = time.time()
    when = datetime.now(timezone.utc) + timedelta(seconds=90)
    secs = cq._parse_retry_after(format_datetime(when), now=now)
    assert 85 <= secs <= 95, f"expected ~90s from the HTTP-date, got {secs:.1f}"


def test_parse_retry_after_clamps_and_defaults():
    now = 1000.0
    # Below the floor: never a busy-spin.
    assert cq._parse_retry_after("0", now=now) == 1.0
    # Above the window: never wait longer than the bucket that set it.
    assert cq._parse_retry_after("99999", now=now) == cq.RATE_LIMIT_MAX_BACKOFF_SECONDS
    # Garbage and empty both fall back, not to zero.
    assert cq._parse_retry_after("not-a-date", now=now) == cq.AUTH_FAILURE_COOLDOWN_SECONDS
    assert cq._parse_retry_after("", now=now) == cq.AUTH_FAILURE_COOLDOWN_SECONDS
    assert cq._parse_retry_after(None, now=now) == cq.AUTH_FAILURE_COOLDOWN_SECONDS
