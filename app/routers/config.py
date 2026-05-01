"""Remote config endpoints for iOS app config sync.

The iOS app calls GET /v1/config/{name} with an X-Config-Version header.
If the local version matches, we return 200 with {"changed": false}.
Otherwise, we return the full JSON payload with {"changed": true}.

Note: We avoid HTTP 304 because Nginx Proxy Manager mangles bare 304
responses (no cached body to serve) into 404s for downstream clients.
"""

import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Baked-in configs shipped with the image (read-only baseline)
_BUNDLED_DIR = Path(__file__).parent.parent.parent / "config" / "remote"

# Persistent directory for live configs (inside the mounted data volume).
# Dashboard edits write here and survive container restarts.
CONFIG_DIR = Path(__file__).parent.parent.parent / "data" / "remote-config"


def seed_remote_configs() -> None:
    """Copy bundled configs into the persistent directory IF MISSING.

    Called once at startup. For each bundled file:
    - If it doesn't exist in the persistent dir, copy it.
    - If it exists, **leave it alone unconditionally**. Dashboard edits
      always win over bundled-from-repo. Pulling repo changes into prod
      requires a manual sync (admin overwrites the dashboard config, or
      we add a "force-sync from bundle" admin action later).

    History: previously this helper ran "if bundled.version > persistent.version,
    overwrite" — which silently wiped dashboard edits any time someone
    bumped a JSON file in the repo. That stomp pattern hit us on
    2026-05-01 when PR #109 bumped tiers.json 13→14 and erased a
    dashboard-added 'share' icon. Failure mode is invisible until iOS
    starts rendering the wrong glyph; silent data loss for admin work.
    Repo bundle now seeds only fresh containers; subsequent edits live
    only in the persistent dir and the admin dashboard.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not _BUNDLED_DIR.is_dir():
        logger.warning("Bundled config directory not found: %s", _BUNDLED_DIR)
        return

    for src in _BUNDLED_DIR.glob("*.json"):
        dest = CONFIG_DIR / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
            logger.info("Seeded remote config from bundle: %s", src.name)
        # else: persistent file wins — never overwrite dashboard edits.


def load_remote_configs() -> dict[str, dict]:
    """Load all JSON files from the persistent config directory into a slug→data dict.

    Each JSON file must have a top-level "version" integer.
    The slug is the filename without .json (e.g., idle-tips.json → idle-tips).
    """
    configs: dict[str, dict] = {}
    if not CONFIG_DIR.is_dir():
        logger.warning("Remote config directory not found: %s", CONFIG_DIR)
        return configs

    for path in CONFIG_DIR.glob("*.json"):
        slug = path.stem
        try:
            data = json.loads(path.read_text())
            if "version" not in data:
                logger.warning("Config %s missing 'version' field, skipping", slug)
                continue
            configs[slug] = data
            logger.info("Loaded remote config: %s (version %s)", slug, data["version"])
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load remote config %s: %s", slug, exc)

    return configs


def _parse_accept_language(header: str | None) -> str | None:
    """Extract the primary language code from an Accept-Language header.

    Examples:
        "es" → "es"
        "es-MX,es;q=0.9,en;q=0.8" → "es"
        "en-US" → "en"
        None → None
    """
    if not header:
        return None
    # Take the first (highest priority) language tag
    first = header.split(",")[0].strip().split(";")[0].strip()
    # Extract just the language code (before any region subtag)
    lang = first.split("-")[0].lower()
    return lang if lang and lang != "en" else None


@router.get("/v1/config/{name}")
async def get_config(name: str, request: Request):
    """Return a remote config JSON, or a slim 'not changed' response.

    Supports localization via Accept-Language header. If the client sends
    Accept-Language: es, the server looks for a "{name}.es" config first,
    falling back to the base "{name}" config. English is the default.
    """
    configs: dict[str, dict] = request.app.state.remote_configs

    # Resolve locale-specific config with fallback
    accept_lang = request.headers.get("Accept-Language")
    locale = _parse_accept_language(accept_lang)
    localized_name = f"{name}.{locale}" if locale else None

    logger.info(
        "Config request: name=%s, Accept-Language=%r, parsed_locale=%s, "
        "trying=%s, available=[%s]",
        name, accept_lang, locale or "en",
        localized_name or name,
        ", ".join(sorted(configs.keys())),
    )

    if localized_name and localized_name in configs:
        data = configs[localized_name]
        resolved_name = localized_name
        logger.info("Resolved to localized config: %s", resolved_name)
    elif name in configs:
        data = configs[name]
        resolved_name = name
        logger.info("Resolved to base config: %s (no localized version found)", resolved_name)
    else:
        logger.warning("Config not found: %s (tried %s)", name, localized_name or name)
        return JSONResponse(status_code=404, content={"error": f"Unknown config: {name}"})

    server_version = data["version"]

    # Check if client already has this version
    client_version = request.headers.get("X-Config-Version")
    if client_version is not None:
        try:
            if int(client_version) >= server_version:
                return JSONResponse(
                    content={"changed": False, "version": server_version},
                    headers={
                        "X-Config-Version": str(server_version),
                        "X-Config-Locale": locale or "en",
                        "X-Config-Resolved": resolved_name,
                    },
                )
        except (ValueError, TypeError):
            pass  # Invalid header value — just return the full payload

    return JSONResponse(
        content=data,
        headers={
            "X-Config-Version": str(server_version),
            "X-Config-Locale": locale or "en",
            "X-Config-Resolved": resolved_name,
            "Cache-Control": "public, max-age=300",
        },
    )
