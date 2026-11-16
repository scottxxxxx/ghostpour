"""N-400 gets 15 requests a minute on every tier (Scott, 2026-11-16). It is
a floor from apps.yml (`tier_overrides.min_requests_per_minute`), so free
goes 5 -> 15 on n400 only, ShoulderSurf free stays 5, and pro's 30 is never
lowered."""
from __future__ import annotations

import pytest

from tests.conftest import chat_request


def _rpm_seen(client, user, monkeypatch, app):
    seen = []
    def fake(uid, rpm):
        seen.append(rpm)
        return (False, 7)
    monkeypatch.setattr(client.app.state.rate_limiter, "check", fake)
    h = {**user["headers"], **({"X-App-ID": app} if app else {})}
    r = client.post("/v1/chat", json=chat_request(), headers=h)
    assert seen, r.text
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    return seen[-1]


@pytest.mark.parametrize("app,want", [("n400", 15), ("shouldersurf", 5), (None, 5)])
def test_free_rpm_by_app(client, free_user, monkeypatch, app, want):
    assert _rpm_seen(client, free_user, monkeypatch, app) == want


def test_floor_never_lowers_pro(client, pro_user, monkeypatch):
    assert _rpm_seen(client, pro_user, monkeypatch, "n400") == 30
