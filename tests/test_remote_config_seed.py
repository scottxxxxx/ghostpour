"""Pin seed_remote_configs's NO-OVERWRITE contract.

Regression: on 2026-05-01 a dashboard-added "share" icon on tiers.json
was silently wiped when PR #109 bumped the bundled JSON's `version`
field. The old seed logic was "if bundled.version > persistent.version,
overwrite" — which made any repo-side version bump stomp dashboard edits
without warning. Failure surface was invisible (an admin notices days
later when the wrong glyph renders).

Repo bundle now seeds only fresh containers; once a persistent file
exists, dashboard edits are sacred and the bundle never overwrites.

If you intentionally want to force-sync a bundled config back into prod,
that's an explicit admin action — DO NOT make this seed helper do it.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import patch


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_seeds_when_persistent_missing(tmp_path):
    """Fresh container: persistent dir empty → bundled files seeded."""
    bundle_dir = tmp_path / "bundle"
    config_dir = tmp_path / "persistent"
    _write(bundle_dir / "tiers.json", {"version": 1, "tiers": {}})

    with patch("app.routers.config._BUNDLED_DIR", bundle_dir), \
         patch("app.routers.config.CONFIG_DIR", config_dir):
        from app.routers.config import seed_remote_configs
        seed_remote_configs()

    seeded = json.loads((config_dir / "tiers.json").read_text())
    assert seeded["version"] == 1


def test_does_not_overwrite_when_bundled_version_is_higher(tmp_path):
    """Regression test for the 2026-05-01 incident.

    Bundled v15, persistent v13 with a dashboard-added 'share' icon →
    persistent MUST stay v13 with the share icon intact. Old behavior
    was bundled wins, which silently wiped the dashboard work.
    """
    bundle_dir = tmp_path / "bundle"
    config_dir = tmp_path / "persistent"
    _write(bundle_dir / "tiers.json", {"version": 15, "tiers": {"plus": {"icon": "checkmark"}}})
    _write(config_dir / "tiers.json", {"version": 13, "tiers": {"plus": {"icon": "share"}}})

    with patch("app.routers.config._BUNDLED_DIR", bundle_dir), \
         patch("app.routers.config.CONFIG_DIR", config_dir):
        from app.routers.config import seed_remote_configs
        seed_remote_configs()

    persistent = json.loads((config_dir / "tiers.json").read_text())
    # Persistent file must still be the dashboard-edited v13 with share icon,
    # NOT the bundled v15 with checkmark.
    assert persistent["version"] == 13
    assert persistent["tiers"]["plus"]["icon"] == "share"


def test_does_not_overwrite_when_versions_equal(tmp_path):
    """Same version on both sides — still no overwrite."""
    bundle_dir = tmp_path / "bundle"
    config_dir = tmp_path / "persistent"
    _write(bundle_dir / "tiers.json", {"version": 15, "tiers": {"plus": {"icon": "checkmark"}}})
    _write(config_dir / "tiers.json", {"version": 15, "tiers": {"plus": {"icon": "share"}}})

    with patch("app.routers.config._BUNDLED_DIR", bundle_dir), \
         patch("app.routers.config.CONFIG_DIR", config_dir):
        from app.routers.config import seed_remote_configs
        seed_remote_configs()

    persistent = json.loads((config_dir / "tiers.json").read_text())
    assert persistent["tiers"]["plus"]["icon"] == "share"


def test_seeds_only_missing_files_when_some_exist(tmp_path):
    """Mixed state: A.json exists in persistent, B.json doesn't.
    A stays untouched; B gets seeded from bundle."""
    bundle_dir = tmp_path / "bundle"
    config_dir = tmp_path / "persistent"
    _write(bundle_dir / "a.json", {"version": 5, "x": "from-bundle"})
    _write(bundle_dir / "b.json", {"version": 1, "y": "from-bundle"})
    _write(config_dir / "a.json", {"version": 1, "x": "from-dashboard"})

    with patch("app.routers.config._BUNDLED_DIR", bundle_dir), \
         patch("app.routers.config.CONFIG_DIR", config_dir):
        from app.routers.config import seed_remote_configs
        seed_remote_configs()

    a = json.loads((config_dir / "a.json").read_text())
    b = json.loads((config_dir / "b.json").read_text())
    # A: dashboard edit preserved.
    assert a["x"] == "from-dashboard"
    assert a["version"] == 1
    # B: seeded from bundle.
    assert b["y"] == "from-bundle"
