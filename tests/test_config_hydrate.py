"""Tests for hydrate_overlay_additions() — the additive bundle→overlay
sync that runs at startup. See issue #186.

Invariants:
  - Pointers present in bundle but missing from overlay are copied verbatim.
  - Pointers present in overlay are left alone (no overwrite, no merge).
  - Lists are atomic: if overlay has a list, bundle's list at the same
    pointer is not merged. If overlay lacks the list entirely, the
    bundle's list is copied as a single value.
  - `version` is bumped only when at least one addition landed on that slug.
  - Malformed bundles / overlays log and skip; never raise.
  - Slugs without an existing overlay are left to seed_remote_configs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.routers import config as config_module


@pytest.fixture
def isolated_config_dirs(tmp_path, monkeypatch):
    """Point _BUNDLED_DIR and CONFIG_DIR at a tmp scratch space so tests
    don't touch the real config/remote or data/remote-config."""
    bundled = tmp_path / "bundled"
    overlay = tmp_path / "overlay"
    bundled.mkdir()
    overlay.mkdir()
    monkeypatch.setattr(config_module, "_BUNDLED_DIR", bundled)
    monkeypatch.setattr(config_module, "CONFIG_DIR", overlay)
    return bundled, overlay


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


class TestAdditiveSync:
    def test_field_missing_in_overlay_is_added(self, isolated_config_dirs):
        """Mirrors the PR #184 scenario: bundle has reasoningLevels on a
        model, overlay's same model entry lacks it. After hydrate, the
        field is present with the bundle value."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 14,
            "providers": [{
                "id": "openai",
                "models": [{"id": "gpt-5.5", "reasoningLevels": ["default", "high"]}],
            }],
        })
        _write(overlay / "llm-providers.json", {
            "version": 11,
            "providers": [{
                "id": "openai",
                "models": [{"id": "gpt-5.5"}],
            }],
        })

        slugs_modified = config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        assert slugs_modified == 1
        assert result["providers"][0]["models"][0]["reasoningLevels"] == ["default", "high"]
        # Overlay's version counter bumped by 1 (independent of bundle's version).
        assert result["version"] == 12

    def test_overlay_value_not_overwritten(self, isolated_config_dirs):
        """Hot-edited overlay values survive. Mirrors a dashboard edit:
        overlay has cost=0.10 (operator override), bundle has cost=0.05.
        Hydrate must leave the overlay at 0.10."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 14,
            "providers": [{
                "id": "openai",
                "models": [{"id": "gpt-5.5", "inputCostPerMillion": 0.05}],
            }],
        })
        _write(overlay / "llm-providers.json", {
            "version": 11,
            "providers": [{
                "id": "openai",
                "models": [{"id": "gpt-5.5", "inputCostPerMillion": 0.10}],
            }],
        })

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        # The hot-edited value wins.
        assert result["providers"][0]["models"][0]["inputCostPerMillion"] == 0.10
        # No additions made → version not bumped.
        assert result["version"] == 11

    def test_nested_addition_lands_at_correct_path(self, isolated_config_dirs):
        """Mirrors PR #187: bundle adds a top-level field on each provider
        object. Overlay's provider objects must receive the new field
        without other fields being touched."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 13,
            "providers": [
                {"id": "openai", "baseURL": "https://api.openai.com",
                 "tokenLimitField": "max_completion_tokens"},
                {"id": "anthropic", "baseURL": "https://api.anthropic.com"},
            ],
        })
        _write(overlay / "llm-providers.json", {
            "version": 11,
            "providers": [
                {"id": "openai", "baseURL": "https://api.openai.com"},
                {"id": "anthropic", "baseURL": "https://api.anthropic.com"},
            ],
        })

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        assert result["providers"][0]["tokenLimitField"] == "max_completion_tokens"
        assert "tokenLimitField" not in result["providers"][1]


class TestListAtomicity:
    def test_overlay_list_is_not_merged_with_bundle_list(self, isolated_config_dirs):
        """If overlay has a list at a path, hydrate does NOT extend it
        with the bundle's elements. Lists are atomic to avoid shifting
        positional semantics."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 14,
            "providers": [
                {"id": "openai"},
                {"id": "anthropic"},
                {"id": "google"},
            ],
        })
        _write(overlay / "llm-providers.json", {
            "version": 11,
            "providers": [
                {"id": "openai"},
                {"id": "anthropic"},
            ],
        })

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        # Overlay's providers list is left at length 2 — bundle's third
        # element is NOT appended.
        assert len(result["providers"]) == 2
        assert {p["id"] for p in result["providers"]} == {"openai", "anthropic"}
        # No additions touched the overlay → version not bumped.
        assert result["version"] == 11

    def test_missing_list_is_copied_whole(self, isolated_config_dirs):
        """If overlay lacks a list entirely, the bundle's list is copied
        verbatim (atomic add of the whole subtree)."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 14,
            "providers": [{"id": "openai"}],
            "newCategoryList": ["a", "b", "c"],
        })
        _write(overlay / "llm-providers.json", {
            "version": 11,
            "providers": [{"id": "openai"}],
        })

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        assert result["newCategoryList"] == ["a", "b", "c"]
        assert result["version"] == 12


class TestNoOpBehavior:
    def test_identical_bundle_and_overlay_is_noop(self, isolated_config_dirs):
        """When bundle and overlay have the same shape, version is NOT
        bumped and no slug is reported as modified."""
        bundled, overlay = isolated_config_dirs
        data = {"version": 11, "providers": [{"id": "openai", "field": 1}]}
        _write(bundled / "llm-providers.json", data)
        _write(overlay / "llm-providers.json", dict(data))  # copy

        slugs_modified = config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        assert slugs_modified == 0
        assert result["version"] == 11

    def test_no_overlay_file_is_skipped(self, isolated_config_dirs):
        """If overlay file doesn't exist, seed_remote_configs is the
        right path; hydrate skips and returns 0."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {"version": 1, "foo": "bar"})
        # No overlay file written.

        slugs_modified = config_module.hydrate_overlay_additions()

        assert slugs_modified == 0
        # Overlay file was not created by hydrate; seed_remote_configs
        # would create it via shutil.copy2 — not in scope here.
        assert not (overlay / "llm-providers.json").exists()

    def test_no_bundle_dir_is_skipped(self, tmp_path, monkeypatch):
        """If the bundle directory is missing, hydrate returns 0
        without raising."""
        bundled = tmp_path / "does-not-exist"
        overlay = tmp_path / "overlay"
        overlay.mkdir()
        monkeypatch.setattr(config_module, "_BUNDLED_DIR", bundled)
        monkeypatch.setattr(config_module, "CONFIG_DIR", overlay)

        assert config_module.hydrate_overlay_additions() == 0


class TestErrorResilience:
    def test_malformed_bundle_logs_and_skips(self, isolated_config_dirs):
        """A malformed bundle should not crash startup. The slug is
        skipped; other slugs continue to be processed."""
        bundled, overlay = isolated_config_dirs

        (bundled / "broken.json").write_text("{not valid json")
        _write(bundled / "good.json", {"version": 5, "newField": "added"})
        _write(overlay / "broken.json", {"version": 1})
        _write(overlay / "good.json", {"version": 1})

        slugs_modified = config_module.hydrate_overlay_additions()

        # broken.json skipped; good.json processed.
        assert slugs_modified == 1
        assert _read(overlay / "good.json")["newField"] == "added"
        # broken.json overlay untouched (no version bump).
        assert _read(overlay / "broken.json")["version"] == 1

    def test_malformed_overlay_logs_and_skips(self, isolated_config_dirs):
        """A malformed overlay file should not crash startup. The slug
        is skipped — fixing requires manual ops, not a startup write."""
        bundled, overlay = isolated_config_dirs

        _write(bundled / "good.json", {"version": 1, "newField": "added"})
        (overlay / "good.json").write_text("{corrupt")

        slugs_modified = config_module.hydrate_overlay_additions()

        assert slugs_modified == 0
        # Overlay still corrupt — we don't try to repair it.
        with pytest.raises(json.JSONDecodeError):
            _read(overlay / "good.json")


class TestVersionBumpSemantics:
    def test_version_bumps_exactly_once_per_run(self, isolated_config_dirs):
        """One run that adds N pointers bumps version by exactly 1, not N."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "llm-providers.json", {
            "version": 14,
            "a": 1, "b": 2, "c": 3, "d": 4,
        })
        _write(overlay / "llm-providers.json", {"version": 11})

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "llm-providers.json")

        assert result["a"] == 1 and result["b"] == 2
        # All 4 pointers added in one pass, version bumped by 1.
        assert result["version"] == 12

    def test_version_field_itself_is_not_overwritten_from_bundle(self, isolated_config_dirs):
        """Even when other fields are missing, the overlay's version is
        controlled by hydrate (bumped by 1), not by the bundle's version
        value. Overlay version counter is independent of bundle's."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 99, "newField": "x"})
        _write(overlay / "x.json", {"version": 11})

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "x.json")

        assert result["newField"] == "x"
        # NOT 99 — overlay version is its own counter.
        assert result["version"] == 12

    def test_no_overlap_changes_means_no_bump(self, isolated_config_dirs):
        """Even if bundle has different scalar values, hydrate doesn't
        touch them (atomic). No additions → no version bump."""
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {"version": 99, "knob": 5})
        _write(overlay / "x.json", {"version": 11, "knob": 100})

        config_module.hydrate_overlay_additions()
        result = _read(overlay / "x.json")

        assert result["knob"] == 100  # untouched
        assert result["version"] == 11  # untouched


class TestPointerEscaping:
    def test_keys_with_slashes_are_escaped_in_log_pointers(self, isolated_config_dirs, caplog):
        """If a key contains '/' or '~', the logged pointer encodes per
        RFC 6901. Belt-and-suspenders — log readability under odd keys."""
        import logging
        bundled, overlay = isolated_config_dirs
        _write(bundled / "x.json", {
            "version": 1,
            "weird/key": "added",
            "tilde~key": "added",
        })
        _write(overlay / "x.json", {"version": 1})

        caplog.set_level(logging.INFO, logger="app.routers.config")
        config_module.hydrate_overlay_additions()

        # Look for the structured log line containing the escaped tokens.
        log_text = "\n".join(r.message for r in caplog.records)
        assert "weird~1key" in log_text or "/weird~1key" in log_text
        assert "tilde~0key" in log_text or "/tilde~0key" in log_text
