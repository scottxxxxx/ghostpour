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


def _escape_pointer_token(tok: str) -> str:
    """Encode an RFC 6901 reference token: ~ → ~0, / → ~1.
    Order matters — escape ~ first."""
    return tok.replace("~", "~0").replace("/", "~1")


def _hydrate_walk(bundle_node, overlay_node, ptr: str, added: list[str]) -> None:
    """Recursive worker for hydrate_overlay_additions.

    Both nodes are at the same JSON pointer path. Three descent rules:

    - **Both dicts** → walk each key in bundle. If missing from overlay,
      deep-copy the subtree and record the pointer. If present in both,
      recurse (apply the same rules to the child pair).
    - **Both same-length lists of dicts** → recurse element-wise. This
      is the common case for `providers[]` and per-provider `models[]`:
      bundle adds a field to an existing element, overlay needs that
      field added at the same index. We refuse to descend when lengths
      differ or when any pair isn't dict-dict, because then the safe
      action is unclear (shifting indices changes semantics).
    - **Anything else** (scalar-scalar, list-list with mismatched
      lengths, dict-vs-scalar, etc.) → atomic. Overlay wins.

    Lists are NEVER extended or shortened by this hook. Element
    additions/removals require the explicit sync-from-bundle endpoint.
    """
    if isinstance(bundle_node, dict) and isinstance(overlay_node, dict):
        for key, bundle_val in bundle_node.items():
            sub_ptr = f"{ptr}/{_escape_pointer_token(key)}"
            if key not in overlay_node:
                overlay_node[key] = json.loads(json.dumps(bundle_val))
                added.append(sub_ptr)
                continue
            _hydrate_walk(bundle_val, overlay_node[key], sub_ptr, added)
        return

    if (
        isinstance(bundle_node, list)
        and isinstance(overlay_node, list)
        and len(bundle_node) == len(overlay_node)
        and all(isinstance(b, dict) and isinstance(o, dict)
                for b, o in zip(bundle_node, overlay_node))
    ):
        for i, (b, o) in enumerate(zip(bundle_node, overlay_node)):
            _hydrate_walk(b, o, f"{ptr}/{i}", added)
        return

    # Atomic: overlay wins.


def _drift_walk(bundle_node, overlay_node, ptr: str, drifted: list[str]) -> None:
    """Recursive worker for detect_overlay_drift.

    Mirrors _hydrate_walk's descent rules, but reports pointers where a
    value exists in BOTH bundle and overlay and differs. Keys missing
    from the overlay are hydration's job; keys only in the overlay are
    hot-edits and intentionally ignored.
    """
    if isinstance(bundle_node, dict) and isinstance(overlay_node, dict):
        for key, bundle_val in bundle_node.items():
            if key not in overlay_node:
                continue
            _drift_walk(bundle_val, overlay_node[key], f"{ptr}/{_escape_pointer_token(key)}", drifted)
        return

    if (
        isinstance(bundle_node, list)
        and isinstance(overlay_node, list)
        and len(bundle_node) == len(overlay_node)
        and all(isinstance(b, dict) and isinstance(o, dict)
                for b, o in zip(bundle_node, overlay_node))
    ):
        for i, (b, o) in enumerate(zip(bundle_node, overlay_node)):
            _drift_walk(b, o, f"{ptr}/{i}", drifted)
        return

    # Atomic leaf (scalar, mixed types, or non-element-wise lists):
    # report when the values differ.
    if bundle_node != overlay_node:
        drifted.append(ptr or "/")


def detect_overlay_drift() -> dict[str, list[str]]:
    """Compare every bundled config against its runtime overlay and return
    {slug: [drifted JSON pointers]} for slugs where a value present in BOTH
    differs. Top-level /version is excluded (it always diverges).

    This is the read-only complement to hydrate_overlay_additions: hydration
    auto-applies *additions* at startup, but value CHANGES stay manual by
    design (ops hot-edits must win). Without detection those changes drift
    silently — bit us on protected-prompts (2026-06-10) where prod served a
    stale defaultPromptModes for weeks. Remediation stays the existing
    POST /webhooks/admin/config/{slug}/sync-from-bundle.

    Malformed files are skipped silently — hydrate already logs them.
    """
    drift: dict[str, list[str]] = {}
    if not _BUNDLED_DIR.is_dir() or not CONFIG_DIR.is_dir():
        return drift

    for bundle_path in _BUNDLED_DIR.glob("*.json"):
        slug = bundle_path.stem
        overlay_path = CONFIG_DIR / bundle_path.name
        if not overlay_path.exists():
            continue
        try:
            bundle = json.loads(bundle_path.read_text())
            overlay = json.loads(overlay_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(bundle, dict) or not isinstance(overlay, dict):
            continue

        bundle = {k: v for k, v in bundle.items() if k != "version"}
        drifted: list[str] = []
        _drift_walk(bundle, overlay, "", drifted)
        if drifted:
            drift[slug] = drifted

    return drift


def hydrate_overlay_additions() -> int:
    """For each bundled config, copy JSON pointers present in the bundle
    but missing from the overlay into the overlay. Never overwrites
    values the overlay already has — dashboard / hot-edits win. Lists
    are atomic: if the overlay has a list at a given path, the bundle's
    list at the same path is not merged.

    When additions land on a slug, the overlay's `version` counter is
    bumped by 1 (signals cache invalidation). When nothing changed,
    `version` is untouched.

    Closes the recurring "PR adds a field, runtime overlay still serves
    the old shape until manual sync-from-bundle" foot-gun that bit us
    on PRs #184, #187, #188, and #191. The manual sync endpoint is
    preserved for value-changes and explicit ops syncs; this only
    handles the additive case at startup.

    Returns the number of slugs that were modified. Logs a structured
    line per slug with addition count and a sample of pointers.

    Malformed bundles or overlay files are logged and skipped; never
    raises (so we never fail-start the container on a config issue).
    """
    if not _BUNDLED_DIR.is_dir():
        return 0
    if not CONFIG_DIR.is_dir():
        return 0

    slugs_modified = 0
    for bundle_path in _BUNDLED_DIR.glob("*.json"):
        slug = bundle_path.stem
        overlay_path = CONFIG_DIR / bundle_path.name
        if not overlay_path.exists():
            # seed_remote_configs handles the no-overlay case by copying
            # the bundle wholesale. Nothing to do here.
            continue

        try:
            bundle = json.loads(bundle_path.read_text())
            overlay = json.loads(overlay_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "hydrate_overlay skipped slug=%s reason=%s", slug, str(exc)[:200],
            )
            continue

        if not isinstance(bundle, dict) or not isinstance(overlay, dict):
            # Non-object root — out of scope; we only hydrate dict roots.
            continue

        added: list[str] = []
        _hydrate_walk(bundle, overlay, "", added)
        if not added:
            continue

        overlay["version"] = int(overlay.get("version", 0)) + 1
        try:
            overlay_path.write_text(
                json.dumps(overlay, indent=2, ensure_ascii=False) + "\n"
            )
        except OSError as exc:
            logger.error(
                "hydrate_overlay write failed slug=%s reason=%s", slug, exc,
            )
            continue

        slugs_modified += 1
        logger.info(
            "hydrate_overlay slug=%s additions=%d sample_pointers=%s version_after=%d",
            slug, len(added), added[:5], overlay["version"],
        )

    return slugs_modified


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
