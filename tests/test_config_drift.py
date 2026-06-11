"""Tests for detect_overlay_drift() — the read-only complement to
hydrate_overlay_additions(). Hydration auto-applies bundle *additions* at
startup; value CHANGES stay manual by design. detect_overlay_drift reports
pointers where a value exists in BOTH bundle and overlay and differs, so
the drift is loud (startup warning + /admin/configs) instead of silent.

Motivating incident (2026-06-10): prod's protected-prompts overlay served a
stale defaultPromptModes for weeks after the bundle changed it — hydration
correctly skipped it (the key existed), and nothing warned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.routers import config as config_module


@pytest.fixture
def isolated_config_dirs(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    overlay = tmp_path / "overlay"
    bundled.mkdir()
    overlay.mkdir()
    monkeypatch.setattr(config_module, "_BUNDLED_DIR", bundled)
    monkeypatch.setattr(config_module, "CONFIG_DIR", overlay)
    return bundled, overlay


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


class TestDriftDetection:
    def test_changed_list_value_reported(self, isolated_config_dirs):
        """The 2026-06-10 scenario: bundle changed defaultPromptModes (a
        list whose length differs), overlay kept the old one."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "protected-prompts.json", {
            "version": 10,
            "defaultPromptModes": [{"name": "Catch Me Up v2"}, {"name": "New Mode"}],
        })
        _write(overlay / "protected-prompts.json", {
            "version": 9,
            "defaultPromptModes": [{"name": "Catch Me Up"}],
        })
        drift = config_module.detect_overlay_drift()
        assert drift == {"protected-prompts": ["/defaultPromptModes"]}

    def test_changed_scalar_reported_at_nested_pointer(self, isolated_config_dirs):
        bundled, overlay = isolated_config_dirs
        _write(bundled / "tiers.json", {
            "version": 2,
            "limits": {"project_chat": {"free": 2000}},
        })
        _write(overlay / "tiers.json", {
            "version": 2,
            "limits": {"project_chat": {"free": 1000}},
        })
        drift = config_module.detect_overlay_drift()
        assert drift == {"tiers": ["/limits/project_chat/free"]}

    def test_same_length_dict_lists_recurse_elementwise(self, isolated_config_dirs):
        """Mirrors hydrate's descent: a changed field inside providers[1]
        is reported at its element pointer, not the whole list."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 14,
            "providers": [{"id": "a", "timeout": 30}, {"id": "b", "timeout": 60}],
        })
        _write(overlay / "llm-providers.json", {
            "version": 14,
            "providers": [{"id": "a", "timeout": 30}, {"id": "b", "timeout": 10}],
        })
        drift = config_module.detect_overlay_drift()
        assert drift == {"llm-providers": ["/providers/1/timeout"]}

    def test_identical_configs_no_drift(self, isolated_config_dirs):
        bundled, overlay = isolated_config_dirs
        data = {"version": 3, "tips": ["a", "b"], "nested": {"x": 1}}
        _write(bundled / "idle-tips.json", data)
        _write(overlay / "idle-tips.json", {**data, "version": 7})
        assert config_module.detect_overlay_drift() == {}

    def test_version_difference_ignored(self, isolated_config_dirs):
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 1, "a": 1})
        _write(overlay / "x.json", {"version": 99, "a": 1})
        assert config_module.detect_overlay_drift() == {}

    def test_overlay_only_keys_ignored(self, isolated_config_dirs):
        """Hot-edited keys that only exist in the overlay are not drift."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 1, "a": 1})
        _write(overlay / "x.json", {"version": 1, "a": 1, "ops_override": True})
        assert config_module.detect_overlay_drift() == {}

    def test_bundle_only_keys_ignored(self, isolated_config_dirs):
        """Additions are hydration's job, not drift."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 2, "a": 1, "new_field": "v"})
        _write(overlay / "x.json", {"version": 1, "a": 1})
        assert config_module.detect_overlay_drift() == {}

    def test_missing_overlay_skipped(self, isolated_config_dirs):
        bundled, _overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 1, "a": 1})
        assert config_module.detect_overlay_drift() == {}

    def test_malformed_overlay_skipped(self, isolated_config_dirs):
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 1, "a": 1})
        (overlay / "x.json").write_text("{not json")
        assert config_module.detect_overlay_drift() == {}

    def test_multiple_drifts_in_one_slug(self, isolated_config_dirs):
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 1, "a": 1, "b": {"c": "new"}})
        _write(overlay / "x.json", {"version": 1, "a": 2, "b": {"c": "old"}})
        drift = config_module.detect_overlay_drift()
        assert sorted(drift["x"]) == ["/a", "/b/c"]
