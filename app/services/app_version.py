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


def get_version_info(registry: dict[str, Any], bundle_id: str) -> dict | None:
    """Look up a single app's version block. Returns the wire-shape
    response dict on hit, None on miss. None lets the router decide
    the HTTP status."""
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
        "platforms": _emit_with_flat_aliases(platforms),
    }


def _emit_with_flat_aliases(platforms: dict) -> dict:
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
        latest = p.get("latest")
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
