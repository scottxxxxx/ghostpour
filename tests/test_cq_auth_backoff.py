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
