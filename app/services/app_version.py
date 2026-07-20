"""Per-app version registry loader + lookup.

Backs the GET /v1/app/version endpoint. The registry is a YAML file
keyed by bundle id with a `platforms` block per app, so the same
gateway can serve version metadata for SS, future apps, future
platforms without a wire shape rev.

Hot reload deliberately omitted. Version bumps coincide with app
releases and an operator update of the YAML + redeploy is the right
moment to refresh. If we ever need live updates we can flip to the
same overlay pattern as remote configs, but it's not worth the
complexity today.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ghostpour.app_version")


def load_registry(path: str | Path) -> dict[str, Any]:
    """Read the YAML and return a dict keyed by bundle id. Missing file
    or malformed YAML returns an empty registry and logs a warning;
    that produces a 404 on every /v1/app/version call rather than
    killing startup, which is the right failure mode for an operational
    metadata endpoint."""
    p = Path(path)
    if not p.exists():
        logger.warning("app_versions registry not found at %s; serving empty", p)
        return {}
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        logger.warning("app_versions registry %s is malformed: %s", p, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("app_versions registry %s root is not a mapping", p)
        return {}
    return data


# --- Runtime override overlay (#force-version-gate, break-glass cutoff) -------
#
# The bundle YAML is load-at-startup. For a SECURITY cutoff a config PR + deploy
# is too slow, so an admin override is persisted to a small overlay file beside
# the SQLite DB (so it rides the same volume and survives restart — critical, a
# restart mid-incident must NOT un-block a flagged build) and deep-merged onto
# the bundle registry. The admin endpoint writes the overlay and reloads the live
# app.state in one shot, so a flip takes effect on the very next request.


def overlay_path() -> Path:
    """Persistent overlay file, beside the SQLite DB (per-test tmp under tests)."""
    from app import database
    base = Path(database._db_path).parent if getattr(database, "_db_path", None) else Path("data")
    return base / "app-versions-overlay.yml"


def load_overlay() -> dict[str, Any]:
    """The admin override overlay, or {} when none/malformed (fail safe — a bad
    overlay must not erase the bundle floors)."""
    p = overlay_path()
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        logger.warning("app-versions overlay %s malformed: %s", p, e)
        return {}
    return data if isinstance(data, dict) else {}


def save_overlay(overlay: dict[str, Any]) -> None:
    p = overlay_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(overlay, sort_keys=True))


def merge_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge the overlay onto the base registry at the bundle ->
    platforms -> platform key level (overlay keys win; everything else kept)."""
    out = copy.deepcopy(base)
    for bundle, b_over in (overlay or {}).items():
        if not isinstance(b_over, dict):
            continue
        plats_over = b_over.get("platforms") or {}
        entry = out.setdefault(bundle, {})
        plats = entry.setdefault("platforms", {})
        if not isinstance(plats, dict):
            plats = entry["platforms"] = {}
        for plat, keys in plats_over.items():
            if isinstance(keys, dict):
                plats.setdefault(plat, {}).update(keys)
    return out


def load_effective(path: str | Path) -> dict[str, Any]:
    """The registry the gateway actually serves and enforces: bundle YAML with
    any admin overlay merged on top."""
    base = load_registry(path)
    overlay = load_overlay()
    return merge_overlay(base, overlay) if overlay else base


def get_version_info(
    registry: dict[str, Any], bundle_id: str, channel: str | None = None
) -> dict | None:
    """Look up a single app's version block. Returns the wire-shape
    response dict on hit, None on miss. None lets the router decide
    the HTTP status.

    `channel` (normalized: "appstore" | "testflight" | None) selects the
    per-channel `latest` when a `latest_by_channel` block is present, so a
    TestFlight user is nudged to the latest beta and an App Store user to
    the latest App Store release, each with the correct upgrade_url. When
    absent or unmatched, the top-level `latest` is served as the fallback
    (today's behavior), so header-less clients are unaffected."""
    entry = registry.get(bundle_id)
    if not entry or not isinstance(entry, dict):
        return None
    platforms = entry.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        # Entry exists but has no platforms block. Treat as a miss so a
        # misconfiguration surfaces as 404 immediately instead of a 200
        # with empty data that the client would silently ignore.
        return None
    return {
        "bundle_id": bundle_id,
        "platforms": _emit_with_flat_aliases(platforms, channel),
    }


def _emit_with_flat_aliases(platforms: dict, channel: str | None = None) -> dict:
    """Return platforms with the `latest` block mirrored as flat
    `latest_version` + `upgrade_url` siblings.

    Background: PR #210 shipped a flat shape, PR #213 restructured into
    a nested `latest` block. The 1.13 iOS build (build 377, shipped
    2026-06-03) decodes the FLAT shape and silently treats the nested
    response as "no update available." 1.14 will accept both shapes,
    but for the entire 1.13-in-the-field window we need to serve both
    on the wire so the soft banner actually fires.

    This is purely additive — the nested `latest` block stays, the
    flat aliases sit next to it. Future clients keep reading the
    nested form (semantically cleaner because the URL and version are
    coupled to the release they describe); 1.13 reads the flat form.

    Operators continue editing the YAML in the nested shape only — the
    aliases are synthesized here on the way out.
    """
    out: dict = {}
    for platform_key, p in platforms.items():
        if not isinstance(p, dict):
            out[platform_key] = p
            continue
        merged = dict(p)
        # Resolve the per-channel latest, then drop the internal map so the
        # client only ever sees one resolved `latest` (never all channels).
        # No channel, or no matching block, leaves the top-level `latest`
        # in place as the fallback.
        by_channel = merged.pop("latest_by_channel", None)
        if channel and isinstance(by_channel, dict):
            chosen = by_channel.get(channel)
            if isinstance(chosen, dict):
                merged["latest"] = chosen
        latest = merged.get("latest")
        if isinstance(latest, dict):
            if "version" in latest and "latest_version" not in merged:
                merged["latest_version"] = latest["version"]
            if "upgrade_url" in latest and "upgrade_url" not in merged:
                merged["upgrade_url"] = latest["upgrade_url"]
            # `latest_build` flat alias — same flat-and-nested pattern
            # as version/upgrade_url. Build number is a numeric string
            # (CFBundleVersion); clients only consult it when their
            # marketing version equals latest_version. See the wire
            # contract doc for semantics.
            if "build" in latest and "latest_build" not in merged:
                merged["latest_build"] = latest["build"]
        out[platform_key] = merged
    return out
