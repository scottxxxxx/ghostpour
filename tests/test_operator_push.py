"""Critical incidents reach the operator's iPhone (Scott, 2026-09-29).

The spend cap took production down while the only alert was an email that
never fired. These drive the real report_incident with APNs faked at the
network edge, so the path from an incident to a push is what is tested."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest

from app.services import alerting, operator_push


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def world(tmp_path, monkeypatch):
    cfg = tmp_path / "remote-config"
    cfg.mkdir()
    (cfg / "operator-alerts.json").write_text(json.dumps({
        "version": 1, "server_only": True, "push_user_ids": ["op-1"],
        "push_categories": ["provider_budget_exhausted"]}))
    monkeypatch.setattr("app.routers.config.CONFIG_DIR", cfg)
    monkeypatch.setattr(operator_push.apns, "configured", lambda s: True)
    sent = []

    async def fake_send(client, db, *, row, settings, payload, expiration, collapse_id):
        sent.append((row["device_token"], payload))
        return "sent"
    monkeypatch.setattr(operator_push.apns, "send_to_token", fake_send)

    async def for_user(db, uid):
        return [{"device_token": "tok-" + uid, "bundle_id": "com.ss", "environment": "production"}] if uid == "op-1" else []
    monkeypatch.setattr(operator_push.device_tokens, "for_user", for_user)
    return sent


async def _db(path):
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    return db


def test_a_new_budget_incident_pushes_to_the_operators_phone(world, client, tmp_db_path):
    async def go():
        db = await _db(tmp_db_path)
        try:
            await alerting.report_incident(db, category="provider_budget_exhausted",
                                           subject="anthropic_usage_limit", details={}, from_addr="x@y")
        finally:
            await db.close()
    _run(go())
    assert len(world) == 1
    token, payload = world[0]
    assert token == "tok-op-1"
    assert payload["aps"]["alert"]["title"] == "GhostPour: Managed LLM budget exhausted"
    assert payload["aps"]["alert"]["body"] == "anthropic_usage_limit"
    assert payload["operator_alert"] == "provider_budget_exhausted"


def test_a_category_not_listed_is_not_pushed(world, tmp_path):
    out = _run(operator_push.push_incident(None, category="user_cost_whale", label="x",
                                           subject="y", settings=SimpleNamespace()))
    assert out["skipped"] == "category_not_pushed" and world == []


def test_a_repeat_of_an_open_incident_does_not_push_again(world, client, tmp_db_path):
    async def go():
        db = await _db(tmp_db_path)
        try:
            for _ in range(3):
                await alerting.report_incident(db, category="provider_budget_exhausted",
                                               subject="anthropic_usage_limit", details={}, from_addr="x@y")
        finally:
            await db.close()
    _run(go())
    assert len(world) == 1
