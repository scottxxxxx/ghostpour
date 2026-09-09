"""GhostPour's ContextQuilt clients retry CONNECTION failures.

Why (2026-09-09, Bifrost's edge logs): on the public hostname nginx
re-proxied a failed upstream connect for ~49s, absorbing every CQ container
swap. GhostPour had no retry of its own. On the internal container network
the edge is not on the hop, so without this a CQ deploy surfaces instantly as
connection refused, and Docker's embedded DNS can NXDOMAIN for a moment while
the new container attaches, which httpcore also reports as ConnectError.

The retry is httpx transport-level: connection establishment only, never a
request that was sent, so it is safe on POST.
"""
from __future__ import annotations

import httpcore
import httpx
import pytest

from app.services import context_quilt as cq


class _RefusingBackend(httpcore.AsyncNetworkBackend):
    """Every connect fails the way a swapped container or a stale DNS entry
    fails. Sleep is a no-op so the backoff (0.5 + 1 + 2s) costs nothing."""

    def __init__(self):
        self.attempts = 0

    async def connect_tcp(self, *a, **k):
        self.attempts += 1
        raise httpcore.ConnectError("connection refused / NXDOMAIN")

    async def sleep(self, seconds):
        return None


@pytest.mark.asyncio
async def test_a_refused_connect_is_retried_the_configured_number_of_times():
    backend = _RefusingBackend()
    transport = cq.connect_retry_transport()
    transport._pool._network_backend = backend
    async with httpx.AsyncClient(base_url="http://contextquilt:8000",
                                 transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post("/v1/memory", json={"x": 1})
    assert backend.attempts == 1 + cq.CQ_CONNECT_RETRIES, (
        f"expected one attempt plus {cq.CQ_CONNECT_RETRIES} retries")
    assert cq.CQ_CONNECT_RETRIES >= 3, "fewer than 3 does not cover a container swap"


def test_the_shared_client_uses_the_retry_transport(monkeypatch):
    monkeypatch.setattr(cq, "_client", None)
    monkeypatch.setattr(cq.get_settings(), "cq_base_url", "http://contextquilt:8000")
    client = cq._get_client()
    assert client._transport._pool._retries == cq.CQ_CONNECT_RETRIES


def test_every_ad_hoc_client_in_the_proxy_router_passes_the_transport():
    """⚠ A source-reading assertion, kept deliberately narrow: the other two
    construction sites are inside request handlers and the property is that
    each passes the shared transport. Removing the kwarg at either site is
    exactly the drift this catches."""
    import inspect

    from app.routers import cq_proxy

    src = inspect.getsource(cq_proxy)
    sites = src.count("httpx.AsyncClient(base_url=settings.cq_base_url")
    wired = src.count("transport=cq.connect_retry_transport()")
    assert sites == 2, f"expected exactly two ad hoc CQ clients, found {sites}"
    assert wired == sites, f"{sites - wired} CQ client(s) built without the retry transport"
