"""Pin both health endpoints — `/health` (canonical) and `/v1/health`
(alias added for the bifrost NPM check). Same payload from both."""

from __future__ import annotations


def test_health_root_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "uptime_seconds" in body


def test_health_v1_alias_returns_same_payload(client):
    a = client.get("/health").json()
    b = client.get("/v1/health").json()
    # uptime_seconds may differ by a few ms between calls — assert the
    # rest is identical and uptime is close.
    a_no_uptime = {k: v for k, v in a.items() if k != "uptime_seconds"}
    b_no_uptime = {k: v for k, v in b.items() if k != "uptime_seconds"}
    assert a_no_uptime == b_no_uptime
    assert abs(a["uptime_seconds"] - b["uptime_seconds"]) < 5


def test_health_v1_no_auth_required(client):
    """Healthchecks shouldn't need an admin key — pin that."""
    resp = client.get("/v1/health")
    assert resp.status_code == 200
