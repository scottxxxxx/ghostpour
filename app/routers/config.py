"""Remote config endpoints for iOS app config sync.

The iOS app calls GET /v1/config/{name} with an X-Config-Version header.
If the local version matches, we return 200 with {"changed": false}.
Otherwise, we return the full JSON payload with {"changed": true}.

Note: We avoid HTTP 304 because Nginx Proxy Manager mangles bare 304
responses (no cached body to serve) into 404s for downstream clients.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Map URL slug → filename in config/remote/
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "remote"


def load_remote_configs() -> dict[str, dict]:
    """Load all JSON files from config/remote/ into a slug→data dict.

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


@router.get("/v1/config/{name}")
async def get_config(name: str, request: Request):
    """Return a remote config JSON, or a slim 'not changed' response."""
    configs: dict[str, dict] = request.app.state.remote_configs

    if name not in configs:
        return JSONResponse(status_code=404, content={"error": f"Unknown config: {name}"})

    data = configs[name]
    server_version = data["version"]

    # Check if client already has this version
    client_version = request.headers.get("X-Config-Version")
    if client_version is not None:
        try:
            if int(client_version) >= server_version:
                return JSONResponse(
                    content={"changed": False, "version": server_version},
                    headers={"X-Config-Version": str(server_version)},
                )
        except (ValueError, TypeError):
            pass  # Invalid header value — just return the full payload

    return JSONResponse(
        content=data,
        headers={
            "X-Config-Version": str(server_version),
            "Cache-Control": "public, max-age=300",
        },
    )
