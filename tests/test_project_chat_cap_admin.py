"""Tests for PUT /admin/tunable/project-chat-cap.

The endpoint dual-writes to:
- `client-config.{locale}.json` → `limits.project_chat.max_input_chars[tier]`
  (new source of truth for server enforcement)
- `tiers.{locale}.json` →
  `tiers.{tier}.feature_definitions.project_chat.max_input_tokens`
  (legacy back-compat for iOS builds reading the old field)

Each test snapshots and restores the affected persistent files so writes
don't leak into other tests in the suite. The persistent dir IS the real
project `data/remote-config/` (CONFIG_DIR is a module-level constant) —
the snapshot/restore pattern is necessary as long as that's true.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_KEY = {"X-Admin-Key": "test-admin-key"}

_FILES_THIS_ENDPOINT_TOUCHES = [
    "client-config.json",
    "client-config.es.json",
    "client-config.ja.json",
    "tiers.json",
    "tiers.es.json",
    "tiers.ja.json",
]


@pytest.fixture(autouse=True)
def _restore_persistent_files():
    """Snapshot files before the test, restore after — even on failure."""
    from app.routers.config import CONFIG_DIR
    snapshots: dict[str, str | None] = {}
    for fname in _FILES_THIS_ENDPOINT_TOUCHES:
        path = CONFIG_DIR / fname
        snapshots[fname] = path.read_text() if path.exists() else None
    yield
    for fname, content in snapshots.items():
        path = CONFIG_DIR / fname
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.write_text(content)


def _persistent_dir() -> Path:
    """Live persistent dir the server writes to. Tests run against this
    same path because the app's CONFIG_DIR is a module-level constant."""
    from app.routers.config import CONFIG_DIR
    return CONFIG_DIR


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_default_locale_writes_default_files(client: TestClient):
    pdir = _persistent_dir()
    cc_path = pdir / "client-config.json"
    tiers_path = pdir / "tiers.json"

    resp = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "plus", "locale": "", "max_input_chars": 555_000},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["locale"] == "default"
    slugs = {f["slug"] for f in body["files_updated"]}
    assert "client-config" in slugs
    assert "tiers" in slugs

    cc = _read_json(cc_path)
    assert cc["limits"]["project_chat"]["max_input_chars"]["plus"] == 555_000

    tiers = _read_json(tiers_path)
    plus = tiers["tiers"]["plus"]
    assert plus["feature_definitions"]["project_chat"]["max_input_tokens"] == 555_000 // 4


def test_japanese_locale_writes_only_ja_files(client: TestClient):
    pdir = _persistent_dir()
    cc_default = pdir / "client-config.json"
    cc_ja = pdir / "client-config.ja.json"
    tiers_ja = pdir / "tiers.ja.json"

    cc_default_before = _read_json(cc_default)
    cc_default_plus_before = (
        cc_default_before["limits"]["project_chat"]["max_input_chars"]["plus"]
    )

    resp = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "plus", "locale": "ja", "max_input_chars": 250_000},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["locale"] == "ja"
    slugs = {f["slug"] for f in body["files_updated"]}
    assert "client-config.ja" in slugs
    assert "tiers.ja" in slugs

    # Japanese files updated
    cc_ja_data = _read_json(cc_ja)
    assert cc_ja_data["limits"]["project_chat"]["max_input_chars"]["plus"] == 250_000
    tiers_ja_data = _read_json(tiers_ja)
    assert (
        tiers_ja_data["tiers"]["plus"]["feature_definitions"]["project_chat"][
            "max_input_tokens"
        ]
        == 250_000 // 4
    )

    # Default English file is NOT touched
    cc_default_after = _read_json(cc_default)
    assert (
        cc_default_after["limits"]["project_chat"]["max_input_chars"]["plus"]
        == cc_default_plus_before
    )


def test_unlimited_minus_one_round_trips(client: TestClient):
    resp = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "pro", "locale": "", "max_input_chars": -1},
    )
    assert resp.status_code == 200, resp.text

    cc = _read_json(_persistent_dir() / "client-config.json")
    assert cc["limits"]["project_chat"]["max_input_chars"]["pro"] == -1
    tiers = _read_json(_persistent_dir() / "tiers.json")
    assert (
        tiers["tiers"]["pro"]["feature_definitions"]["project_chat"][
            "max_input_tokens"
        ]
        == -1
    )


def test_no_change_returns_empty_files_updated(client: TestClient):
    # First write to set a known value
    client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "free", "locale": "", "max_input_chars": 200_000},
    )
    # Second write of the same value — both files match; no version bump
    resp = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "free", "locale": "", "max_input_chars": 200_000},
    )
    assert resp.status_code == 200
    assert resp.json()["files_updated"] == []


def test_invalid_chars_value_rejected(client: TestClient):
    resp = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "free", "locale": "", "max_input_chars": -42},
    )
    assert resp.status_code == 400


def test_admin_key_required(client: TestClient):
    resp = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        json={"tier": "plus", "locale": "", "max_input_chars": 600_000},
    )
    assert resp.status_code in (401, 422)
    resp2 = client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers={"X-Admin-Key": "wrong"},
        json={"tier": "plus", "locale": "", "max_input_chars": 600_000},
    )
    assert resp2.status_code == 403


def test_save_then_load_via_admin_config_endpoint(client: TestClient):
    """End-to-end round trip mirroring the dashboard load flow:
    save via project-chat-cap, then read back via /admin/config/{slug}
    (what loadProjectChatCap calls)."""
    client.put(
        "/webhooks/admin/tunable/project-chat-cap",
        headers=_KEY,
        json={"tier": "plus", "locale": "ja", "max_input_chars": 280_000},
    )
    resp = client.get("/webhooks/admin/config/client-config.ja", headers=_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["data"]["limits"]["project_chat"]["max_input_chars"]["plus"]
        == 280_000
    )
