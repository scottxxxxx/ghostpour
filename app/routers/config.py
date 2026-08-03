"""Remote config endpoints for iOS app config sync.

The iOS app calls GET /v1/config/{name} with an X-Config-Version header.
If the local version matches, we return 200 with {"changed": false}.
Otherwise, we return the full JSON payload with {"changed": true}.

Note: We avoid HTTP 304 because Nginx Proxy Manager mangles bare 304
responses (no cached body to serve) into 404s for downstream clients.
"""

import json
import logging
import re
import shutil
from pathlib import Path

import aiosqlite
import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Baked-in configs shipped with the image (read-only baseline)
_BUNDLED_DIR = Path(__file__).parent.parent.parent / "config" / "remote"

# Persistent directory for live configs (inside the mounted data volume).
# Dashboard edits write here and survive container restarts.
CONFIG_DIR = Path(__file__).parent.parent.parent / "data" / "remote-config"

# App registry (config/apps.yml): X-App-ID → {dir, label}. Drives per-app
# config resolution and dashboard grouping (Phase B / #249).
_APPS_PATH = Path(__file__).parent.parent.parent / "config" / "apps.yml"
_DEFAULT_APP = "shouldersurf"
_apps_cache: dict | None = None


def load_apps(force: bool = False) -> dict:
    """Return {"default_app": str, "apps": {id: {"dir","label"}}} from apps.yml.

    Cached after first read. Falls back to a shouldersurf-only registry if the
    file is missing or malformed, so a bad apps.yml can never fail-start the
    container or 404 ShoulderSurf (the safe default app).
    """
    global _apps_cache
    if _apps_cache is not None and not force:
        return _apps_cache
    fallback = {"default_app": _DEFAULT_APP,
                "apps": {"shouldersurf": {"dir": "shouldersurf", "label": "ShoulderSurf"}}}
    try:
        loaded = yaml.safe_load(_APPS_PATH.read_text()) or {}
        apps = loaded.get("apps") or {}
        if not isinstance(apps, dict) or not apps:
            raise ValueError("apps.yml has no 'apps' map")
        _apps_cache = {
            "default_app": loaded.get("default_app", _DEFAULT_APP),
            "apps": apps,
        }
    except (OSError, yaml.YAMLError, ValueError) as exc:
        logger.error("apps.yml load failed (%s); using shouldersurf-only fallback", exc)
        _apps_cache = fallback
    return _apps_cache


def resolve_app_dir(app_id: str | None) -> str:
    """Map an X-App-ID to its config subdirectory. Always fails open.

    Missing / blank / "unknown" → the default app's dir (shouldersurf).
    Known id (case-insensitive) → its dir. UNRECOGNIZED id → the default dir
    too, with a logged warning.

    Why fail open instead of 404: ShoulderSurf has shipped to TestFlight for
    months and older builds in the field may send no X-App-ID, an odd casing,
    or a legacy value. We must never break their config fetch over app
    identity — an unrecognized id resolves like a header-less client (flat /
    ShoulderSurf config), exactly today's behavior, and the warning still
    surfaces genuine misconfig in logs. New apps get registered in apps.yml
    before launch so they land in their own dir rather than this fallback.
    """
    reg = load_apps()
    apps = reg["apps"]
    default_dir = apps.get(reg["default_app"], {}).get("dir", _DEFAULT_APP)
    norm = (app_id or "").strip().lower()
    if not norm or norm == "unknown":
        return default_dir
    entry = apps.get(norm)
    if entry:
        return entry.get("dir", default_dir)
    logger.warning(
        "Unrecognized X-App-ID=%r; serving default app %s (flat config)",
        app_id, default_dir,
    )
    return default_dir


def tier_overrides_for_app(app_id: str | None) -> dict:
    """Per-app tier-value overrides from apps.yml (`apps.<id>.tier_overrides`).

    Returns the override dict for a recognized app id, or {} for missing /
    blank / "unknown" / unrecognized ids — so the default + ShoulderSurf path
    always gets {} and is never altered. Callers apply overrides as
    `overrides.get(field, tier.<field>)`, so an absent key leaves the tier
    value untouched.
    """
    norm = (app_id or "").strip().lower()
    if not norm or norm == "unknown":
        return {}
    entry = load_apps()["apps"].get(norm) or {}
    return entry.get("tier_overrides") or {}


TESTER_PREFIX = "tester"

# Directories named `build-lte-<N>` hold the shape served to builds at or
# below N. The frozen App Store build is the case this exists for: it
# decodes whole-file with no per-entry tolerance, so a restructure that is
# safe for current builds discards the entire file there, and it has no
# way to tell us. See docs/decisions/prompt-composition-doctrine.md.
_BUILD_PIN_RE = re.compile(r"^build-lte-(\d+)$")


def build_pin_dirs() -> list[int]:
    """Build ceilings that have a pinned config tree, ascending."""
    if not CONFIG_DIR.is_dir():
        return []
    out = []
    for child in CONFIG_DIR.iterdir():
        if child.is_dir():
            m = _BUILD_PIN_RE.match(child.name)
            if m:
                out.append(int(m.group(1)))
    return sorted(out)


def resolve_build_pin(app_build: str | None) -> str | None:
    """The tightest pin directory that applies to this caller, or None.

    An unreadable or absent build resolves to the LOWEST pin rather than
    none, because guessing new on an unknown client is the one guess that
    breaks somebody. SS also reports that their build stamp can silently
    fall back to a hardcoded baseline when the git-count script does not
    run, so a low or odd number is not evidence of an old install and a
    high one is not proof of a new one. Safe-but-degraded is the trade.

    Among applicable pins the SMALLEST ceiling wins, so `build-lte-803`
    beats `build-lte-900` for a build of 803: the tighter pin is the more
    specific statement about that build.
    """
    pins = build_pin_dirs()
    if not pins:
        return None
    try:
        build = int(app_build) if app_build is not None else -1
    except (TypeError, ValueError):
        build = -1
    if build < 0:
        return f"build-lte-{pins[0]}"
    applicable = [p for p in pins if build <= p]
    return f"build-lte-{applicable[0]}" if applicable else None


def candidate_slugs(app_dir: str, name: str,
                    audience: str = "production",
                    build_pin: str | None = None) -> list[str]:
    """Slug lookup order for (app, requested name), highest priority first.

    1. `{app_dir}/{name}` — the app's own file.
    2. `{app_dir}/{name[3:]}` — Option C (#249): when Tech Rehearsal asks for a
       legacy `tr-`prefixed name, also match the clean unprefixed file, so old
       TR builds keep resolving after B2 drops the prefix in storage.
    3. `{name}` — flat fallback = today's behavior; nothing breaks pre-B2 when
       no per-app files exist yet.

    For a tester, every candidate above is tried under `tester/` FIRST, then
    the production chain unchanged. So a tester variant overrides only the
    configs it actually defines, and everything else resolves exactly as
    production does. That fallback is the point: the audience is for proving
    a change is safe before the rest of the base sees it, not for running a
    permanently divergent build.
    """
    base = [f"{app_dir}/{name}"]
    if app_dir == "techrehearsal" and name.startswith("tr-"):
        base.append(f"{app_dir}/{name[3:]}")
    base.append(name)

    out: list[str] = []
    # Audience first: a tester asked to see a specific change, and that
    # intent outranks a build ceiling. A tester on a pinned build still
    # falls through to the pin for anything the tester tree omits.
    if audience == TESTER_PREFIX:
        out += [f"{TESTER_PREFIX}/{c}" for c in base]
    if build_pin:
        out += [f"{build_pin}/{c}" for c in base]
    return out + base


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

    for src in _BUNDLED_DIR.rglob("*.json"):
        rel = src.relative_to(_BUNDLED_DIR)
        dest = CONFIG_DIR / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            logger.info("Seeded remote config from bundle: %s", rel.as_posix())
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

    for bundle_path in _BUNDLED_DIR.rglob("*.json"):
        rel = bundle_path.relative_to(_BUNDLED_DIR)
        slug = rel.with_suffix("").as_posix()
        overlay_path = CONFIG_DIR / rel
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
    for bundle_path in _BUNDLED_DIR.rglob("*.json"):
        rel = bundle_path.relative_to(_BUNDLED_DIR)
        slug = rel.with_suffix("").as_posix()
        overlay_path = CONFIG_DIR / rel
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
    """Load all JSON files from the persistent config dir (recursively) into a
    slug→data dict.

    Each JSON file must have a top-level "version" integer. The slug is the
    path relative to CONFIG_DIR without .json, posix-style: a flat file
    `idle-tips.json` → `idle-tips`; a per-app file `techrehearsal/jd-analysis.json`
    → `techrehearsal/jd-analysis`. Pre-B2 everything is flat, so slugs are bare
    stems exactly as before.
    """
    configs: dict[str, dict] = {}
    if not CONFIG_DIR.is_dir():
        logger.warning("Remote config directory not found: %s", CONFIG_DIR)
        return configs

    for path in CONFIG_DIR.rglob("*.json"):
        slug = path.relative_to(CONFIG_DIR).with_suffix("").as_posix()
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
async def get_config(
    name: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return a remote config JSON, or a slim 'not changed' response.

    Per-app resolution (Phase B / #249): the X-App-ID header (via
    request.state.app_id) selects the app dir; we try `{app_dir}/{name}`, the
    Option-C `tr-`stripped alias for Tech Rehearsal, then the flat name —
    each in the requested locale first (Accept-Language), then base. Missing
    header → ShoulderSurf; present-but-unknown app → 404. Pre-B2 (no per-app
    files yet) every lookup lands on the flat fallback, so behavior is
    unchanged for existing clients.
    """
    configs: dict[str, dict] = request.app.state.remote_configs

    # App identity → config dir. Fails open: missing/unknown/unrecognized
    # X-App-ID all resolve to the default app (ShoulderSurf / flat config), so
    # an older client never loses its config over app identity.
    app_id = getattr(request.state, "app_id", None)
    app_dir = resolve_app_dir(app_id)

    # Test audience (2026-08-03). A config change is a live change to an app
    # we cannot roll back per user, so a named set of accounts sees it first.
    # Resolution falls through to production for anything the tester tree
    # does not define, so a tester is never running a wholly separate build.
    audience = await resolve_audience(request, db)

    # Build ceiling: a frozen build can need an older shape than the one we
    # serve everyone else. Unknown or unreadable build resolves to the
    # tightest pin, never to none.
    build_pin = resolve_build_pin(request.headers.get("X-App-Build"))

    # Resolve locale-specific config with fallback
    accept_lang = request.headers.get("Accept-Language")
    locale = _parse_accept_language(accept_lang)

    # Walk candidates in priority order; for each, try the localized variant
    # before the base, so a per-app localized file wins over a flat base.
    resolved_name = None
    for cand in candidate_slugs(app_dir, name, audience, build_pin):
        if locale and f"{cand}.{locale}" in configs:
            resolved_name = f"{cand}.{locale}"
            break
        if cand in configs:
            resolved_name = cand
            break

    logger.info(
        "Config request: name=%s app_id=%r app_dir=%s locale=%s audience=%s "
        "build_pin=%s resolved=%s", name, app_id, app_dir, locale or "en",
        audience, build_pin or "-", resolved_name,
    )

    if resolved_name is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown config: {name}"})

    data = configs[resolved_name]

    # Server-only configs (2026-07-24): prompt-bearing managed configs and
    # model-routing are GP-internal material — no client fetches them (both
    # app fetch lists confirmed empirically + by the TR team), so serving
    # them publicly was pure exposure. Marked configs answer exactly like
    # an unknown slug so the namespace stays unconfirmable from outside.
    # Admin routes read app.state directly and are unaffected.
    if data.get("server_only"):
        logger.info("Config request blocked (server_only): %s", resolved_name)
        return JSONResponse(status_code=404, content={"error": f"Unknown config: {name}"})
    server_version = data["version"]

    # Check if client already has this version
    client_version = request.headers.get("X-Config-Version")
    parsed_client_version: int | None = None
    if client_version is not None:
        try:
            parsed_client_version = int(client_version)
            if parsed_client_version >= server_version:
                # Holding current: it decoded and cached fine, so any
                # recorded stall for this config is resolved.
                await _note_config_health(
                    request, resolved_name, parsed_client_version,
                    server_version, stalled=False, db=db)
                return JSONResponse(
                    content={"changed": False, "version": server_version},
                    headers={
                        "X-Config-Version": str(server_version),
                        "X-Config-Locale": locale or "en",
                        "X-Config-Resolved": resolved_name,
                        "X-Config-Audience": audience,
                        "X-Config-Build-Pin": build_pin or "none",
                    },
                )
        except (ValueError, TypeError):
            pass  # Invalid header value — just return the full payload

    # Serving the body. Repeatedly serving it to a client whose version
    # never advances is how the poisoned-cache loop looks from here; see
    # app/services/config_stall.py.
    await _note_config_health(
        request, resolved_name, parsed_client_version, server_version,
        stalled=True, db=db)

    return JSONResponse(
        content=data,
        headers={
            "X-Config-Version": str(server_version),
            "X-Config-Locale": locale or "en",
            "X-Config-Resolved": resolved_name,
            "X-Config-Audience": audience,
            "X-Config-Build-Pin": build_pin or "none",
            # Testers must not be served a proxy-cached production payload,
            # and vice versa. Vary is not enough here because the audience is
            # derived from the bearer token rather than a cacheable header.
            "Cache-Control": ("no-store" if audience == TESTER_PREFIX
                              else "public, max-age=300"),
        },
    )


async def _note_config_health(
    request: Request, config_name: str, client_version: int | None,
    server_version: int, *, stalled: bool,
    db: "aiosqlite.Connection | None" = None,
) -> None:
    """Book-keep one config fetch for poisoned-cache detection.

    Best-effort and never raises: config delivery must not fail because
    the bookkeeping did. Unauthenticated fetches are skipped, since the
    signature needs a stable identity across launches.
    """
    if client_version is None or db is None:
        return
    try:
        from app.services import config_stall

        user_id = await _user_id_from_request(request)
        if not user_id:
            return
        app_id = getattr(request.state, "app_id", None) or "unknown"
        if stalled:
            await config_stall.record_full_payload(
                db, user_id=user_id, app_id=app_id,
                config_name=config_name, client_version=client_version,
                server_version=server_version,
                app_build=request.headers.get("X-App-Build"),
                app_version=request.headers.get("X-App-Version"),
            )
        else:
            await config_stall.clear(
                db, user_id=user_id, app_id=app_id,
                config_name=config_name, client_version=client_version,
            )
    except Exception:
        logger.debug("config health bookkeeping skipped", exc_info=True)


async def resolve_audience(request: Request, db) -> str:
    """"tester" when the caller is a registered test account, else "production".

    Best-effort and fails to production: an unauthenticated fetch, an
    unreadable token, or a database hiccup must never accidentally serve
    test config to the base. The cost of guessing wrong in that direction
    is one tester seeing production config; the cost of the other direction
    is everyone seeing untested config.
    """
    if db is None:
        return "production"
    try:
        user_id = await _user_id_from_request(request)
        if not user_id:
            return "production"
        row = await (await db.execute(
            "SELECT 1 FROM config_testers WHERE user_id = ? AND active = 1",
            (user_id,))).fetchone()
        return TESTER_PREFIX if row else "production"
    except Exception:
        logger.debug("audience resolution failed; serving production",
                     exc_info=True)
        return "production"


async def _user_id_from_request(request: Request) -> str | None:
    """Best-effort user id from the bearer token, without 401ing."""
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = request.app.state.jwt_service.verify_access_token(
            auth.split(None, 1)[1])
        return payload.get("sub") if payload.get("type") == "access" else None
    except Exception:
        return None
