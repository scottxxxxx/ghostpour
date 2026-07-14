import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import httpx
import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.database import get_db
from app.services.allocation_reset import compute_next_reset
from app.services.display_labels import display_call_type

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_admin(request: Request, x_admin_key: str) -> None:
    settings = request.app.state.settings
    if not settings.admin_key or not secrets.compare_digest(x_admin_key, settings.admin_key):
        raise HTTPException(status_code=403, detail="Invalid admin key")


class SetTierRequest(BaseModel):
    user_id: str
    tier: str


class SimulateTierRequest(BaseModel):
    user_id: str
    tier: str | None = None  # null to clear simulation
    exhausted: bool = True


class AdminCaptureTranscriptRequest(BaseModel):
    user_id: str
    transcript: str
    meeting_id: str | None = None
    project: str | None = None
    project_id: str | None = None


class UpdateFeatureStateRequest(BaseModel):
    tier: str
    feature: str
    state: str  # "enabled", "teaser", "disabled"


@router.post("/admin/set-tier")
async def set_tier(
    body: SetTierRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Set a user's subscription tier with dollar-value carryover on upgrade.

    On upgrade: unused allocation from the old tier is converted to dollar
    value and added to the new tier's allocation. monthly_used_usd resets to 0.

    On downgrade: allocation resets to the new tier's limit. No carryover
    (downgrades take effect at period end in production via StoreKit).
    """
    _verify_admin(request, x_admin_key)

    tier_config = request.app.state.tier_config
    if body.tier not in tier_config.tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {body.tier}. Available: {list(tier_config.tiers.keys())}",
        )

    new_tier = tier_config.tiers[body.tier]

    # Read current user state
    cursor = await db.execute(
        "SELECT tier, monthly_used_usd, monthly_cost_limit_usd, overage_balance_usd FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    old_tier_name = row["tier"]

    # Apply tier change — reset allocation to the new tier's limit, no carryover
    now = datetime.now(timezone.utc)
    new_limit = new_tier.monthly_cost_limit_usd
    # Admin tier-change has no Apple expiresDate to anchor on; use a
    # locally-computed 1-month rolling window.
    resets_at = compute_next_reset(now).isoformat()

    await db.execute(
        """UPDATE users SET
            tier = ?,
            monthly_cost_limit_usd = ?,
            monthly_used_usd = 0,
            overage_balance_usd = 0,
            searches_used = 0,
            allocation_resets_at = ?,
            simulated_tier = NULL,
            simulated_exhausted = 0,
            updated_at = ?
           WHERE id = ?""",
        (body.tier, new_limit, resets_at, now.isoformat(), body.user_id),
    )
    await db.commit()

    return {
        "status": "ok",
        "user_id": body.user_id,
        "old_tier": old_tier_name,
        "new_tier": body.tier,
        "monthly_limit_usd": new_limit,
        "allocation_resets_at": resets_at,
    }


@router.post("/admin/simulate-tier")
async def simulate_tier(
    body: SimulateTierRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Toggle tier simulation for testing upgrade flows.

    Sets a temporary tier override on a user without changing their real tier.
    When active, the user sees the simulated tier's constraints, and if
    exhausted=true, all chat requests return 429 allocation_exhausted.

    Send tier=null to clear the simulation and restore the real tier.
    """
    _verify_admin(request, x_admin_key)

    tier_config = request.app.state.tier_config

    # Validate tier if setting simulation
    if body.tier is not None and body.tier not in tier_config.tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {body.tier}. Available: {list(tier_config.tiers.keys())}",
        )

    # Verify user exists
    cursor = await db.execute(
        "SELECT id, tier, simulated_tier FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    real_tier = row["tier"]

    if body.tier is None:
        # Clear simulation
        await db.execute(
            "UPDATE users SET simulated_tier = NULL, simulated_exhausted = 0 WHERE id = ?",
            (body.user_id,),
        )
        await db.commit()
        return {
            "status": "ok",
            "simulation": "cleared",
            "user_id": body.user_id,
            "real_tier": real_tier,
        }

    # Activate simulation
    await db.execute(
        "UPDATE users SET simulated_tier = ?, simulated_exhausted = ? WHERE id = ?",
        (body.tier, 1 if body.exhausted else 0, body.user_id),
    )
    await db.commit()

    return {
        "status": "ok",
        "simulation": "active",
        "user_id": body.user_id,
        "real_tier": real_tier,
        "simulated_tier": body.tier,
        "exhausted": body.exhausted,
    }


@router.post("/admin/update-feature-state")
async def update_feature_state(
    body: UpdateFeatureStateRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Toggle a feature's state for a specific tier. Writes to tiers.yml and reloads."""
    _verify_admin(request, x_admin_key)

    if body.state not in ("enabled", "teaser", "disabled"):
        raise HTTPException(status_code=400, detail=f"Invalid state: {body.state}. Must be enabled, teaser, or disabled")

    tier_config = request.app.state.tier_config
    if body.tier not in tier_config.tiers:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {body.tier}")

    # Load current YAML
    tiers_path = Path(__file__).parent.parent.parent / "config" / "tiers.yml"

    with open(tiers_path) as f:
        raw = yaml.safe_load(f)

    # Update the feature state
    tier_data = raw["tiers"].get(body.tier)
    if not tier_data:
        raise HTTPException(status_code=400, detail=f"Tier {body.tier} not found in tiers.yml")

    if "features" not in tier_data:
        tier_data["features"] = {}

    old_state = tier_data["features"].get(body.feature, "disabled")
    tier_data["features"][body.feature] = body.state

    # Write back
    with open(tiers_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Reload tier config in app state
    from app.models.tier import load_tier_config
    request.app.state.tier_config = load_tier_config(str(tiers_path))

    return {
        "status": "ok",
        "tier": body.tier,
        "feature": body.feature,
        "old_state": old_state,
        "new_state": body.state,
    }


# --- Admin Transcript Capture ---


@router.post("/admin/capture-transcript")
async def admin_capture_transcript(
    body: AdminCaptureTranscriptRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Send a transcript to Context Quilt on behalf of a user. Admin-only."""
    _verify_admin(request, x_admin_key)

    import asyncio
    from app.services import context_quilt as cq

    # Look up user for display_name, email, and effective tier (so the
    # admin path forwards subscription_tier to CQ like the user-driven
    # /v1/capture-transcript and chat after_llm hook do — closes the
    # last gap on extraction_metrics tier coverage).
    cursor = await db.execute(
        "SELECT id, email, display_name, tier, simulated_tier FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    effective_tier = row["simulated_tier"] or row["tier"]

    # Admin captures have no originating device request, so source the
    # language from the user's most recent telemetry ping. app_locale is
    # Locale.current.identifier ("es_US") — underscore → hyphen for BCP-47.
    cursor = await db.execute(
        """SELECT app_locale FROM telemetry_events
           WHERE user_id = ? AND app_locale IS NOT NULL
           ORDER BY received_at DESC LIMIT 1""",
        (body.user_id,),
    )
    locale_row = await cursor.fetchone()
    language = locale_row["app_locale"].replace("_", "-") if locale_row else None

    asyncio.create_task(cq.capture(
        user_id=row["id"],
        interaction_type="meeting_transcript",
        content=body.transcript,
        meeting_id=body.meeting_id,
        project=body.project,
        project_id=body.project_id,
        display_name=row["display_name"],
        email=row["email"],
        subscription_tier=effective_tier,
        language=language,
    ))

    return {
        "status": "queued",
        "user_id": body.user_id,
        "project": body.project,
        "transcript_length": len(body.transcript),
    }


# --- Live Request Log ---


@router.get("/admin/live-log")
async def get_live_log(
    request: Request,
    x_admin_key: str = Header(...),
    limit: int = 50,
):
    """Return recent API request/response log entries from the in-memory buffer."""
    _verify_admin(request, x_admin_key)
    from app.middleware.request_logging import get_recent_logs
    return {"entries": get_recent_logs(limit)}


@router.get("/admin/live-log/{request_id}")
async def get_live_log_entry(
    request_id: str,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Look up a single log entry by request_id (from X-Request-ID header)."""
    _verify_admin(request, x_admin_key)
    from app.middleware.request_logging import get_log_by_request_id
    entry = get_log_by_request_id(request_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No log entry for request_id: {request_id}")
    return entry


# --- Remote Config Management ---


def _config_app(slug: str) -> str:
    """Bucket a config slug to an app for dashboard grouping.

    Composite slugs (`techrehearsal/jd-analysis`) carry the app in their dir
    prefix — authoritative post-B2. Flat slugs (pre-B2) are bucketed by the
    legacy `tr-` convention so the dashboard segregates immediately; the
    internally-keyed `model-routing` is shared. Display only — no bearing on
    /v1/config resolution.
    """
    if "/" in slug:
        return slug.split("/", 1)[0]
    base = slug.split(".", 1)[0]
    if base.startswith("tr-"):
        return "techrehearsal"
    if base == "model-routing":
        return "shared"
    return "shouldersurf"


@router.get("/admin/configs")
async def list_configs(
    request: Request,
    x_admin_key: str = Header(...),
):
    """List all remote config files with their versions and sizes.

    `drift` lists JSON pointers where the bundled config's value differs
    from the overlay's (recomputed live, not the boot-time snapshot).
    Non-empty drift means a PR changed a value that hasn't been synced —
    remediate via POST /admin/config/{slug}/sync-from-bundle.
    """
    _verify_admin(request, x_admin_key)
    configs: dict[str, dict] = request.app.state.remote_configs

    from app.routers.config import detect_overlay_drift
    drift = detect_overlay_drift()

    result = []
    for slug, data in sorted(configs.items()):
        result.append({
            "slug": slug,
            "app": _config_app(slug),
            "version": data.get("version"),
            "keys": list(data.keys()),
            "size": len(json.dumps(data)),
            "drift": drift.get(slug, []),
        })
    return {"configs": result}


@router.get("/admin/config/{slug:path}/bundle")
async def get_config_bundle(
    slug: str,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Return the bundled (repo-shipped) version of a remote config.

    The active value at /admin/config/{slug} comes from the persistent
    file on the data volume (dashboard-edited). This endpoint exposes
    the BUNDLED value from `config/remote/` for diff/sync UIs.

    Declared BEFORE the catch-all `{slug:path}` detail route on purpose: the
    greedy path converter would otherwise swallow `…/bundle` as a slug.
    """
    _verify_admin(request, x_admin_key)
    from app.routers.config import _BUNDLED_DIR

    bundle_path = _BUNDLED_DIR / f"{slug}.json"
    if not bundle_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No bundled file for slug '{slug}' at {bundle_path.name}",
        )
    try:
        data = json.loads(bundle_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read bundled {slug}.json: {exc}",
        )
    return {"slug": slug, "data": data}


@router.get("/admin/config/{slug:path}")
async def get_config_detail(
    slug: str,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Get the full JSON content of a remote config.

    Re-reads from disk on every call so direct file edits to the
    persistent config dir are reflected immediately in the dashboard.
    The persistent JSON file is the source of truth — `app.state.remote_configs`
    is just an in-memory cache for hot-path reads on /v1/* endpoints,
    and we refresh it here so dashboard reads stay consistent.
    """
    _verify_admin(request, x_admin_key)

    from app.routers.config import load_remote_configs
    request.app.state.remote_configs = load_remote_configs()
    configs: dict[str, dict] = request.app.state.remote_configs

    if slug not in configs:
        raise HTTPException(status_code=404, detail=f"Config '{slug}' not found")
    return {"slug": slug, "data": configs[slug]}


class UpdateConfigRequest(BaseModel):
    data: dict


@router.put("/admin/config/{slug:path}")
async def update_config(
    slug: str,
    body: UpdateConfigRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Update a remote config. Writes to disk and hot-reloads into memory."""
    _verify_admin(request, x_admin_key)

    if "version" not in body.data:
        raise HTTPException(status_code=400, detail="Config must have a 'version' field")

    # Entitlements matrix writes are closed-enum validated (Phase 2,
    # feature-entitlements.md): a malformed matrix never loads — reject
    # the write and the last good config stays live. Locale/per-app
    # variants of the slug get the same check.
    if slug == "entitlements" or slug.endswith("/entitlements"):
        from app.services.entitlements import validate_matrix
        problems = validate_matrix(
            body.data,
            known_features=set(request.app.state.feature_config.features),
            known_tiers=set(request.app.state.tier_config.tiers),
        )
        if problems:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_entitlements_matrix",
                        "problems": problems})

    from app.routers.config import CONFIG_DIR, load_remote_configs

    config_path = CONFIG_DIR / f"{slug}.json"
    is_new = not config_path.exists() and slug not in request.app.state.remote_configs

    # Allow creating new locale variants (e.g., protected-prompts.es)
    # but block creating entirely new base configs via PUT
    if is_new:
        parts = slug.rsplit(".", 1)
        is_locale_variant = len(parts) == 2 and len(parts[1]) == 2 and parts[0] in request.app.state.remote_configs
        if not is_locale_variant:
            raise HTTPException(status_code=404, detail=f"Config '{slug}' not found")

    # Auto-increment version if content changed
    old_data = request.app.state.remote_configs.get(slug, {})
    old_version = old_data.get("version", 0)
    if body.data["version"] <= old_version:
        body.data["version"] = old_version + 1

    # Write to disk (create the app subdir on first per-app write)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(body.data, indent=2, ensure_ascii=False) + "\n")

    # Hot-reload all configs
    request.app.state.remote_configs = load_remote_configs()

    return {
        "status": "updated",
        "slug": slug,
        "version": body.data["version"],
    }


class SyncFromBundleRequest(BaseModel):
    """Force-sync entries from the bundled config into the persistent
    (live) config. Closes the silent-deploy gap left by
    `seed_remote_configs()`'s no-overwrite policy: bundle changes for
    these entries propagate to prod immediately, dashboard edits to
    OTHER entries are preserved.

    Each entry in `keys` is either:
      - a bare top-level key (e.g. `"alpha"`) — legacy semantics; the
        whole value is replaced verbatim.
      - a JSON pointer starting with `/` (RFC 6901, e.g.
        `"/limits/project_chat/defaultPromptReserveTokens"`) — only the
        leaf is replaced. Intermediate objects are created if missing
        on the persistent side. Use this to land deeply-nested bundle
        adds without clobbering sibling dashboard edits.
    """
    keys: list[str]


def _unescape_pointer_token(tok: str) -> str:
    """Decode an RFC 6901 reference token: ~1 → /, ~0 → ~."""
    return tok.replace("~1", "/").replace("~0", "~")


def _as_list_index(token: str) -> int | None:
    """Parse an RFC 6901 array-index token to a non-negative int, or None if
    it isn't one. Per RFC 6901, indices are digits with no leading zero (bare
    "0" is allowed). The end-of-array token "-" is not a resolvable index."""
    if token == "0":
        return 0
    if token.isdigit() and token[0] != "0":
        return int(token)
    return None


def _resolve_pointer(obj: dict, pointer: str) -> tuple[bool, object]:
    """Resolve a JSON pointer against `obj`. Returns (found, value).

    Walks dicts by key and lists by numeric array index, so a pointer like
    `/providers/3/models` — the shape detect_overlay_drift emits for a
    nested-array change — resolves instead of failing at the list.
    """
    if pointer == "":
        return True, obj
    if not pointer.startswith("/"):
        raise ValueError(f"Pointer must start with '/' or be empty: {pointer!r}")
    cur: object = obj
    for raw in pointer.split("/")[1:]:
        tok = _unescape_pointer_token(raw)
        if isinstance(cur, dict):
            if tok not in cur:
                return False, None
            cur = cur[tok]
        elif isinstance(cur, list):
            idx = _as_list_index(tok)
            if idx is None or idx >= len(cur):
                return False, None
            cur = cur[idx]
        else:
            return False, None
    return True, cur


def _set_pointer(obj: dict, pointer: str, value: object) -> None:
    """Set a value at a JSON pointer, creating intermediate dicts as needed.

    Walks lists by numeric array index; an existing dict/list intermediate is
    traversed, never clobbered. A leaf list index replaces that element, and
    the RFC 6901 end token "-" (or an index equal to the list length) appends.
    """
    if pointer == "" or not pointer.startswith("/"):
        raise ValueError(f"Pointer must start with '/': {pointer!r}")
    tokens = [_unescape_pointer_token(t) for t in pointer.split("/")[1:]]
    cur: object = obj
    for tok in tokens[:-1]:
        if isinstance(cur, list):
            idx = _as_list_index(tok)
            if idx is None or idx >= len(cur):
                raise ValueError(f"pointer traverses missing list index {tok!r}: {pointer!r}")
            cur = cur[idx]
        elif isinstance(cur, dict):
            nxt = cur.get(tok)
            if not isinstance(nxt, (dict, list)):
                nxt = {}
                cur[tok] = nxt
            cur = nxt
        else:
            raise ValueError(f"pointer traverses a scalar: {pointer!r}")
    last = tokens[-1]
    if isinstance(cur, list):
        if last == "-":
            cur.append(value)
            return
        idx = _as_list_index(last)
        if idx is None or idx > len(cur):
            raise ValueError(f"pointer sets invalid list index {last!r}: {pointer!r}")
        if idx == len(cur):
            cur.append(value)
        else:
            cur[idx] = value
    else:
        cur[last] = value


@router.post("/admin/geoip/reload")
async def reload_geoip(
    request: Request,
    x_admin_key: str = Header(...),
):
    """Drop the cached GeoIP readers so the next lookup re-opens the .mmdb files.
    The refresh cron downloads fresh dbip-city files into the volume, then calls
    this to hot-swap them without a container restart. Returns whether each
    family's DB is present + a sample lookup so the cron can verify the swap."""
    _verify_admin(request, x_admin_key)
    from app.services import geoip
    geoip.reset_cache()
    sample = {"v4": geoip.lookup("8.8.8.8"), "v6": geoip.lookup("2001:4860:4860::8888")}
    return {"reloaded": True, "sample": sample}


@router.post("/admin/config/{slug:path}/sync-from-bundle")
async def sync_config_from_bundle(
    slug: str,
    body: SyncFromBundleRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Copy listed entries from the bundled config into the persistent
    config. Each entry is either a top-level key (legacy) or a JSON
    pointer (`/limits/project_chat/...`). Bumps version; hot-reloads
    `remote_configs`.

    Returns a per-entry change report (old → new value, or "unchanged"
    when bundle and persistent already matched). If the persistent
    file doesn't exist yet, it's created with the requested entries
    plus a starting `version: 1`.
    """
    _verify_admin(request, x_admin_key)
    if not body.keys:
        raise HTTPException(status_code=400, detail="keys list must not be empty")

    from app.routers.config import _BUNDLED_DIR, CONFIG_DIR, load_remote_configs

    bundle_path = _BUNDLED_DIR / f"{slug}.json"
    if not bundle_path.exists():
        raise HTTPException(
            status_code=404, detail=f"No bundled file for slug '{slug}'"
        )
    try:
        bundle = json.loads(bundle_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read bundle: {exc}")

    persistent_path = CONFIG_DIR / f"{slug}.json"
    if persistent_path.exists():
        try:
            persistent = json.loads(persistent_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not read persistent: {exc}",
            )
    else:
        persistent = {"version": 0}

    changes: list[dict] = []
    any_change = False
    missing_entries: list[str] = []
    for entry in body.keys:
        pointer = entry if entry.startswith("/") else f"/{entry}"
        try:
            found, new_val = _resolve_pointer(bundle, pointer)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not found:
            missing_entries.append(entry)
            continue
        _, old_val = _resolve_pointer(persistent, pointer)
        if old_val == new_val:
            changes.append({"key": entry, "status": "unchanged"})
            continue
        _set_pointer(persistent, pointer, new_val)
        changes.append({
            "key": entry,
            "status": "synced",
            "old": old_val,
            "new": new_val,
        })
        any_change = True

    if missing_entries:
        raise HTTPException(
            status_code=400,
            detail=f"keys not in bundled file: {missing_entries}",
        )

    if any_change:
        persistent["version"] = (persistent.get("version") or 0) + 1
        persistent_path.write_text(
            json.dumps(persistent, indent=2, ensure_ascii=False) + "\n"
        )
        request.app.state.remote_configs = load_remote_configs()

    return {
        "status": "synced" if any_change else "no_changes",
        "slug": slug,
        "version": persistent["version"],
        "changes": changes,
    }


# --- Tunable parameters (per-tier dials editable from the dashboard) ---


class TunableTierFieldRequest(BaseModel):
    """Update a single per-tier numeric field across all locale variants
    of tiers.json (en + .es + .ja). Locale-independent values like
    max_input_tokens stay in lockstep so iOS sees the same number
    regardless of Accept-Language.

    `value: None` is allowed — used to *clear* an optional field (e.g.,
    `searches_soft_threshold` for tiers that don't have a soft cap).
    For required numeric fields the caller should pass a concrete int
    (e.g., 0 to disable rather than null)."""
    tier: str          # "free" | "plus" | "pro" | "admin"
    feature: str       # "project_chat" | "meeting_reports" | "context_quilt" | "search"
    field: str         # "max_input_tokens" | "searches_per_month" | "searches_soft_threshold" | ...
    value: int | None  # new value (None clears the field)


class EntitlementsMatrixCellRequest(BaseModel):
    """Single-cell edit of the entitlements matrix from the dashboard.
    Same closed enums as the full-config PUT; writes the persistent
    `entitlements` overlay and hot-reloads, so the flip IS the
    enforcement change (Phase 2 single source)."""

    feature: str
    tier: str
    state: str


class EntitlementsDocumentsRequest(BaseModel):
    """Targeted edit of the documents knobs from the Entitlements tab
    (Phase 1.5). `scope` picks the block — "passthrough" is the top-level
    documents gate, "generation" the nested file-generation gate. Partial
    update: only provided fields change. Writes ALL locale variants in
    lockstep (enabled/min_tier are locale-independent feature numbers;
    every historical flip kept the three files aligned)."""

    scope: str
    enabled: bool | None = None
    min_tier: str | None = None


class ProjectChatCapRequest(BaseModel):
    """Update the per-tier Project Chat context cap for one locale.

    Source of truth is `client-config.{locale}.json`'s
    `limits.project_chat.max_input_chars`. Server enforcement reads from
    there. We ALSO dual-write the legacy
    `tiers.{locale}.feature_definitions.project_chat.max_input_tokens`
    field (= max_input_chars / 4) so iOS builds that haven't migrated to
    `client-config` still see the right gauge denominator. Removing the
    dual-write is a follow-up once iOS picks up `client-config`.
    """
    tier: str           # "free" | "plus" | "pro" | "admin"
    locale: str = ""    # "" or "default" → English; "ja", "es", … for variants
    max_input_chars: int   # -1 for uncapped


@router.put("/admin/tunable/tier-field")
async def update_tier_tunable_field(
    body: TunableTierFieldRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Update tiers.{tier}.feature_definitions.{feature}.{field} across
    all locale variants of tiers.json. Auto-bumps version on every
    locale that changed. Hot-reloads remote_configs.

    Source of truth is the persistent JSON file. Server-side enforcement
    (e.g., the budget gate's context cap check) reads from this file
    via app.services.tunable_config — so a save here changes both the
    iOS fuel gauge AND the server's 413 threshold.
    """
    _verify_admin(request, x_admin_key)

    from app.routers.config import CONFIG_DIR, load_remote_configs

    locale_slugs = ["tiers", "tiers.es", "tiers.ja"]
    updated: list[dict] = []
    for slug in locale_slugs:
        path = CONFIG_DIR / f"{slug}.json"
        if not path.exists():
            continue  # locale not shipped — skip, not an error
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not read {slug}.json: {exc}",
            )

        tier_block = (data.get("tiers") or {}).get(body.tier)
        if tier_block is None:
            raise HTTPException(
                status_code=400,
                detail=f"Tier '{body.tier}' not found in {slug}.json",
            )

        feature_defs = tier_block.setdefault("feature_definitions", {})
        feature_block = feature_defs.setdefault(body.feature, {})
        old_value = feature_block.get(body.field)
        if old_value == body.value:
            continue  # no-op for this locale

        feature_block[body.field] = body.value
        data["version"] = (data.get("version") or 0) + 1

        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        updated.append({"slug": slug, "version": data["version"], "old": old_value, "new": body.value})

    # Hot-reload so the next /v1/chat call sees the new cap immediately.
    request.app.state.remote_configs = load_remote_configs()

    return {
        "status": "updated",
        "tier": body.tier,
        "feature": body.feature,
        "field": body.field,
        "value": body.value,
        "files_updated": updated,
    }


@router.put("/admin/tunable/project-chat-cap")
async def update_project_chat_cap(
    body: ProjectChatCapRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Set the per-tier Project Chat character cap for one locale.

    Writes `client-config.{locale}.json` (the new source of truth for
    server enforcement) AND `tiers.{locale}.json` (legacy, for iOS
    builds that haven't migrated to `client-config`). Both files'
    versions are bumped on change. Hot-reloads remote_configs.

    `locale=""` or `"default"` targets the unsuffixed default file.
    """
    _verify_admin(request, x_admin_key)

    from app.routers.config import CONFIG_DIR, load_remote_configs

    # Empty / "default" → the unsuffixed default files. Anything else
    # becomes a `.{locale}` suffix.
    suffix = ""
    locale_label = "default"
    if body.locale and body.locale.lower() not in ("default", "en"):
        suffix = f".{body.locale.lower()}"
        locale_label = body.locale.lower()

    if body.max_input_chars < -1:
        raise HTTPException(
            status_code=400,
            detail="max_input_chars must be -1 (uncapped) or a non-negative integer",
        )

    files_updated: list[dict] = []

    # 1. client-config.{locale}.json — write max_input_chars at
    #    limits.project_chat.max_input_chars[tier]
    cc_slug = f"client-config{suffix}"
    cc_path = CONFIG_DIR / f"{cc_slug}.json"
    if cc_path.exists():
        try:
            cc_data = json.loads(cc_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not read {cc_slug}.json: {exc}",
            )
    else:
        # First write for a new locale — bootstrap with the v1 skeleton.
        cc_data = {"version": 0, "limits": {"project_chat": {"max_input_chars": {}}}, "flags": {}, "intervals": {}}

    limits = cc_data.setdefault("limits", {})
    pc = limits.setdefault("project_chat", {})
    chars_block = pc.setdefault("max_input_chars", {})
    old_chars = chars_block.get(body.tier)
    if old_chars != body.max_input_chars:
        chars_block[body.tier] = body.max_input_chars
        cc_data["version"] = (cc_data.get("version") or 0) + 1
        cc_path.write_text(json.dumps(cc_data, indent=2, ensure_ascii=False) + "\n")
        files_updated.append({
            "slug": cc_slug,
            "version": cc_data["version"],
            "old": old_chars,
            "new": body.max_input_chars,
        })

    # 2. tiers.{locale}.json — back-compat dual-write of
    #    feature_definitions.project_chat.max_input_tokens (= chars / 4).
    tiers_slug = f"tiers{suffix}"
    tiers_path = CONFIG_DIR / f"{tiers_slug}.json"
    if tiers_path.exists():
        try:
            tiers_data = json.loads(tiers_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not read {tiers_slug}.json: {exc}",
            )
        tier_block = (tiers_data.get("tiers") or {}).get(body.tier)
        if tier_block is not None:
            feature_defs = tier_block.setdefault("feature_definitions", {})
            pc_legacy = feature_defs.setdefault("project_chat", {})
            new_tokens = (
                -1 if body.max_input_chars == -1 else body.max_input_chars // 4
            )
            old_tokens = pc_legacy.get("max_input_tokens")
            if old_tokens != new_tokens:
                pc_legacy["max_input_tokens"] = new_tokens
                tiers_data["version"] = (tiers_data.get("version") or 0) + 1
                tiers_path.write_text(
                    json.dumps(tiers_data, indent=2, ensure_ascii=False) + "\n"
                )
                files_updated.append({
                    "slug": tiers_slug,
                    "version": tiers_data["version"],
                    "old": old_tokens,
                    "new": new_tokens,
                })

    request.app.state.remote_configs = load_remote_configs()

    return {
        "status": "updated",
        "tier": body.tier,
        "locale": locale_label,
        "max_input_chars": body.max_input_chars,
        "files_updated": files_updated,
    }


# --- Provider Status & Key Management ---

# Providers we can check balance/status for
_PROVIDER_CHECKS = {
    "anthropic": {
        "display_name": "Anthropic",
        "env_key": "anthropic_api_key",
        "check_url": "https://api.anthropic.com/v1/messages",
        "has_balance_api": False,
        "console_url": "https://console.anthropic.com/settings/billing",
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "env_key": "openrouter_api_key",
        "check_url": "https://openrouter.ai/api/v1/auth/key",
        "has_balance_api": True,
        "console_url": "https://openrouter.ai/credits",
    },
    "openai": {
        "display_name": "OpenAI",
        "env_key": "openai_api_key",
        "check_url": None,
        "has_balance_api": False,
        "console_url": "https://platform.openai.com/settings/organization/billing/overview",
    },
}


@router.get("/admin/provider-status")
async def provider_status(
    request: Request,
    x_admin_key: str = Header(...),
):
    """Check API key status and balance for configured providers."""
    _verify_admin(request, x_admin_key)
    settings = request.app.state.settings

    results = {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, info in _PROVIDER_CHECKS.items():
            key = getattr(settings, info["env_key"], "")
            masked = f"...{key[-4:]}" if key and len(key) > 4 else "(not set)"

            entry = {
                "display_name": info["display_name"],
                "key_set": bool(key),
                "key_masked": masked,
                "console_url": info["console_url"],
                "status": "unknown",
            }

            if not key:
                entry["status"] = "no_key"
                results[name] = entry
                continue

            try:
                if name == "openrouter":
                    # OpenRouter has a balance API
                    resp = await client.get(
                        "https://openrouter.ai/api/v1/auth/key",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        from app.services.provider_health import next_limit_reset
                        data = resp.json().get("data", {})
                        entry["status"] = "ok"
                        # Prefer OpenRouter's limit_remaining (accounts for the
                        # reset window). Fall back to limit - all-time usage only
                        # for older API shapes that omit it. See provider_health.
                        remaining = data.get("limit_remaining")
                        if remaining is None and data.get("limit") is not None:
                            remaining = round(data["limit"] - data.get("usage", 0), 4)
                        nxt = next_limit_reset(data.get("limit_reset"))
                        entry["balance"] = {
                            "label": data.get("label", ""),
                            "usage_usd": data.get("usage", 0),          # all-time
                            "limit_usd": data.get("limit", None),
                            "remaining_usd": remaining,                 # current period
                            "limit_reset": data.get("limit_reset"),     # daily|weekly|monthly|null
                            "usage_weekly_usd": data.get("usage_weekly"),
                            "usage_monthly_usd": data.get("usage_monthly"),
                            "next_reset_at": nxt.isoformat() if nxt else None,
                            "is_free_tier": data.get("is_free_tier", False),
                        }
                    else:
                        entry["status"] = "invalid_key"

                elif name == "anthropic":
                    # Anthropic has no balance API — verify key with a minimal call
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": "claude-haiku-4-5-20251001",
                            "max_tokens": 1,
                            "messages": [{"role": "user", "content": "hi"}],
                        },
                    )
                    if resp.status_code == 200:
                        entry["status"] = "ok"
                    elif resp.status_code == 401:
                        entry["status"] = "invalid_key"
                    elif resp.status_code == 429:
                        entry["status"] = "rate_limited"
                    else:
                        entry["status"] = "ok"  # 400 etc still means key works

                else:
                    # Generic: just mark as configured
                    entry["status"] = "configured"

            except httpx.TimeoutException:
                entry["status"] = "timeout"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)

            results[name] = entry

    return {"providers": results}


class UpdateKeyRequest(BaseModel):
    provider: str   # e.g., "anthropic", "openrouter"
    api_key: str    # new key value


@router.post("/admin/update-key")
async def update_key(
    body: UpdateKeyRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Update a provider API key — takes effect immediately and persists to
    Secret Manager so it survives container restarts.

    Prior implementation tried to write a .env file at /app inside the
    container. The container can't reach /opt/ghostpour/.env.prod on
    the host (docker compose reads env_file once at start), so every
    update went memory-only and silently reverted on the next deploy.
    This path writes to the same Secret Manager secret that
    _ensure_secrets_in_env reads at startup, closing that loop.
    """
    _verify_admin(request, x_admin_key)
    settings = request.app.state.settings

    if body.provider not in _PROVIDER_CHECKS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {body.provider}. Available: {list(_PROVIDER_CHECKS.keys())}",
        )

    env_key = _PROVIDER_CHECKS[body.provider]["env_key"]

    if not hasattr(settings, env_key):
        raise HTTPException(status_code=400, detail=f"No setting for {env_key}")

    # Update in-memory first so the running process picks up the new key
    # immediately, regardless of whether SM persistence succeeds.
    # (pydantic-settings model is frozen, so use object.__setattr__)
    object.__setattr__(settings, env_key, body.api_key)
    # Also update the env var so any subprocess Python (e.g. tests,
    # debug shells) and any fresh `get_settings()` call sees it.
    import os
    env_var_name = f"CZ_{env_key.upper()}"
    os.environ[env_var_name] = body.api_key

    # Persist to Secret Manager. The mapping from env var to secret name
    # lives in app/config.py:_SECRET_MANAGER_MAPPINGS.
    from app.config import _SECRET_MANAGER_MAPPINGS
    sm_secret_name = _SECRET_MANAGER_MAPPINGS.get(env_var_name)
    if not sm_secret_name:
        # No mapping configured — the operator added a new provider
        # without wiring it through SM. Memory-only is the best we can
        # do, surface it.
        return {
            "status": "ok",
            "provider": body.provider,
            "key_masked": _mask_key(body.api_key),
            "persisted": False,
            "location": "memory_only",
            "detail": (
                f"No Secret Manager mapping for {env_var_name}. Add an "
                "entry to _SECRET_MANAGER_MAPPINGS in app/config.py to "
                "enable durable persistence."
            ),
        }

    success, detail = _persist_to_secret_manager(sm_secret_name, body.api_key)
    # Clear the secrets cache so a future startup or get_secret() call
    # reads the just-written value rather than the stale cached one.
    try:
        from app.secrets import _cache, _cache_lock
        with _cache_lock:
            _cache.clear()
    except Exception:
        pass

    return {
        "status": "ok",
        "provider": body.provider,
        "key_masked": _mask_key(body.api_key),
        "persisted": success,
        "location": "secret_manager" if success else "memory_only",
        "secret_name": sm_secret_name,
        "detail": detail,
    }


def _mask_key(value: str) -> str:
    return f"...{value[-4:]}" if len(value) > 4 else "***"


def _persist_to_secret_manager(secret_name: str, value: str) -> tuple[bool, str]:
    """Add a new version to the named Secret Manager secret. Auto-creates
    the secret if it doesn't exist yet. Returns (success, detail_message).

    PermissionDenied is reported separately so the dashboard can tell
    the operator exactly which IAM binding is missing instead of a
    generic "write failed."
    """
    try:
        from google.api_core.exceptions import (  # type: ignore[import-not-found]
            NotFound, PermissionDenied,
        )
        from google.cloud import secretmanager  # type: ignore[import-not-found]
        from google.auth import default as auth_default  # type: ignore[import-not-found]
    except ImportError as e:
        return False, f"google-cloud-secret-manager not installed: {e}"

    from app.secrets import _resolve_project
    project = _resolve_project()
    if not project:
        return False, (
            "No GCP project resolved. Set CZ_GCP_PROJECT or run on a "
            "GCE instance with Application Default Credentials."
        )

    try:
        # Same scope dance as app/secrets.py: GCE metadata-service
        # credentials need cloud-platform passed explicitly or every
        # SM call comes back 403.
        creds, _ = auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        client = secretmanager.SecretManagerServiceClient(credentials=creds)
        secret_path = f"projects/{project}/secrets/{secret_name}"
        try:
            client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": value.encode("utf-8")},
                }
            )
            return True, f"Added new version to projects/{project}/secrets/{secret_name}"
        except NotFound:
            # First write for this secret — create it then add v1.
            client.create_secret(
                request={
                    "parent": f"projects/{project}",
                    "secret_id": secret_name,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
            client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": value.encode("utf-8")},
                }
            )
            return True, (
                f"Created secret projects/{project}/secrets/{secret_name} "
                "and added v1."
            )
    except PermissionDenied as e:
        return False, (
            f"Runtime SA lacks Secret Manager write permission: {e}. "
            f"Grant roles/secretmanager.admin on projects/{project} (or "
            f"roles/secretmanager.secretVersionAdder on the specific "
            f"secret if you pre-create it via gcloud)."
        )
    except Exception as e:  # noqa: BLE001 — surface any other failure with detail
        return False, f"Secret Manager write failed: {e}"


@router.get("/admin/dashboard")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
    app: str | None = Query(default=None),
):
    """Admin dashboard: users, usage, costs, latency. Protected by admin key."""
    _verify_admin(request, x_admin_key)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Optional per-app scoping. Empty/absent `app` means all apps. When set,
    # every usage_log-derived metric below is restricted to that app_id. The
    # users/trial/allocation counts stay global (users are shared across apps).
    app_clause = " AND app_id = ?" if app else ""
    app_clause_l = " AND l.app_id = ?" if app else ""
    app_params = (app,) if app else ()

    # --- Users ---
    cursor = await db.execute("SELECT COUNT(*) FROM users")
    total_users = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
    active_users = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT tier, COUNT(*) FROM users WHERE is_active = 1 GROUP BY tier"
    )
    tier_breakdown = {row[0]: row[1] for row in await cursor.fetchall()}

    # --- Usage (last N days) ---
    since = f"{days}d"
    cursor = await db.execute(
        """SELECT
            COUNT(*) as total_requests,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
            SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END) as rate_limited,
            COALESCE(SUM(input_tokens), 0) as total_input_tokens,
            COALESCE(SUM(output_tokens), 0) as total_output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as total_cost_usd,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms,
            MAX(response_time_ms) as max_latency_ms,
            MIN(response_time_ms) as min_latency_ms
           FROM usage_log
           WHERE request_timestamp >= date('now', ?)""" + app_clause,
        (f"-{days} days", *app_params),
    )
    row = await cursor.fetchone()
    usage_summary = {
        "period_days": days,
        "total_requests": row[0],
        "successful": row[1],
        "errors": row[2],
        "rate_limited": row[3],
        "total_input_tokens": row[4],
        "total_output_tokens": row[5],
        "total_tokens": row[4] + row[5],
        "total_cost_usd": round(row[6], 4),
        "avg_latency_ms": int(row[7]) if row[7] else 0,
        "max_latency_ms": row[8],
        "min_latency_ms": row[9],
    }

    # --- Usage by provider ---
    cursor = await db.execute(
        """SELECT provider, model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'""" + app_clause + """
           GROUP BY provider, model
           ORDER BY requests DESC""",
        (f"-{days} days", *app_params),
    )
    by_model = [
        {
            "provider": r[0],
            "model": r[1],
            "requests": r[2],
            "input_tokens": r[3],
            "output_tokens": r[4],
            "cost_usd": round(r[5], 4),
            "avg_latency_ms": int(r[6]) if r[6] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # --- Usage by scenario (Tech Rehearsal scenario sub-dimension) ---
    # interview / negotiation / personal / pitch (open vocab). Rows the
    # client hasn't tagged read as "(untagged)". Honors the app + period
    # filters like every other usage_log query here.
    cursor = await db.execute(
        """SELECT COALESCE(scenario, '(untagged)') as scenario,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd
           FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'""" + app_clause + """
           GROUP BY scenario
           ORDER BY requests DESC""",
        (f"-{days} days", *app_params),
    )
    by_scenario = [
        {
            "scenario": r["scenario"],
            "requests": r["requests"],
            "tokens": r["tokens"],
            "cost_usd": round(r["cost_usd"], 4),
        }
        for r in await cursor.fetchall()
    ]

    # --- Usage by user (top 10) ---
    cursor = await db.execute(
        """SELECT u.id, u.email, u.tier,
            COUNT(*) as requests,
            COALESCE(SUM(l.input_tokens), 0) + COALESCE(SUM(l.output_tokens), 0) as total_tokens,
            COALESCE(SUM(l.estimated_cost_usd), 0) as cost_usd,
            MAX(l.request_timestamp) as last_request
           FROM usage_log l
           JOIN users u ON l.user_id = u.id
           WHERE l.request_timestamp >= date('now', ?) AND l.status = 'success'""" + app_clause_l + """
           GROUP BY u.id
           ORDER BY total_tokens DESC
           LIMIT 10""",
        (f"-{days} days", *app_params),
    )
    top_users = [
        {
            "user_id": r[0],
            "email": r[1],
            "tier": r[2],
            "requests": r[3],
            "total_tokens": r[4],
            "cost_usd": round(r[5], 4),
            "last_request": r[6],
        }
        for r in await cursor.fetchall()
    ]

    # --- Today's usage ---
    cursor = await db.execute(
        """SELECT
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd
           FROM usage_log
           WHERE request_timestamp >= ? AND status = 'success'""" + app_clause,
        (today, *app_params),
    )
    today_row = await cursor.fetchone()
    today_usage = {
        "requests": today_row[0],
        "tokens": today_row[1],
        "cost_usd": round(today_row[2], 4),
    }

    # --- Latency percentiles (last N days) ---
    cursor = await db.execute(
        """SELECT response_time_ms FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'""" + app_clause + """
           ORDER BY response_time_ms""",
        (f"-{days} days", *app_params),
    )
    latencies = [r[0] for r in await cursor.fetchall() if r[0] is not None]
    percentiles = {}
    if latencies:
        for p in [50, 75, 90, 95, 99]:
            idx = int(len(latencies) * p / 100)
            percentiles[f"p{p}"] = latencies[min(idx, len(latencies) - 1)]

    # Allocation alerts: users above 80%
    cursor = await db.execute(
        """SELECT u.id, u.email, u.tier, u.monthly_used_usd, u.monthly_cost_limit_usd
           FROM users u
           WHERE u.is_active = 1
             AND u.monthly_cost_limit_usd > 0
             AND u.monthly_used_usd >= u.monthly_cost_limit_usd * 0.8
           ORDER BY (u.monthly_used_usd / u.monthly_cost_limit_usd) DESC"""
    )
    allocation_alerts = [
        {
            "user_id": r["id"],
            "email": r["email"],
            "tier": r["tier"],
            "monthly_used_usd": round(float(r["monthly_used_usd"] or 0), 4),
            "monthly_limit_usd": round(float(r["monthly_cost_limit_usd"] or 0), 4),
            "percent_used": round(float(r["monthly_used_usd"] or 0) / float(r["monthly_cost_limit_usd"]) * 100, 1) if r["monthly_cost_limit_usd"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Trial stats
    cursor = await db.execute(
        "SELECT COUNT(*) FROM users WHERE is_trial = 1"
    )
    active_trials = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM users WHERE is_trial = 0 AND tier != 'free' AND tier != 'admin'"
    )
    converted = (await cursor.fetchone())[0]

    cursor = await db.execute(
        """SELECT id, email, tier, trial_end FROM users
           WHERE is_trial = 1
           ORDER BY trial_end ASC"""
    )
    trial_users = [
        {"user_id": r["id"], "email": r["email"], "tier": r["tier"], "trial_end": r["trial_end"]}
        for r in await cursor.fetchall()
    ]

    # Cached token savings
    cursor = await db.execute(
        """SELECT
            COALESCE(SUM(cached_tokens), 0) as total_cached,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output
           FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'""" + app_clause,
        (f"-{days} days", *app_params),
    )
    cache_row = await cursor.fetchone()
    total_cached = cache_row["total_cached"]
    # Estimate savings: cached tokens would have been billed as input tokens
    # Use approximate Haiku input rate ($0.80/1M) as baseline
    estimated_savings = total_cached * 0.80 / 1_000_000

    # Daily usage trend
    cursor = await db.execute(
        """SELECT
            date(request_timestamp) as day,
            COUNT(*) as requests,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
           FROM usage_log
           WHERE request_timestamp >= date('now', ?)""" + app_clause + """
           GROUP BY date(request_timestamp)
           ORDER BY day""",
        (f"-{days} days", *app_params),
    )
    daily_usage = [
        {"day": r["day"], "requests": r["requests"], "cost": round(r["cost"], 4), "errors": r["errors"]}
        for r in await cursor.fetchall()
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users": {
            "total": total_users,
            "active": active_users,
            "by_tier": tier_breakdown,
        },
        "today": today_usage,
        "usage": usage_summary,
        "by_model": by_model,
        "by_scenario": by_scenario,
        "top_users": top_users,
        "latency_percentiles": percentiles,
        "allocation_alerts": allocation_alerts,
        "trials": {
            "active_trials": active_trials,
            "converted_subscribers": converted,
            "trial_users": trial_users,
        },
        "cache_savings": {
            "cached_tokens": total_cached,
            "estimated_savings_usd": round(estimated_savings, 4),
        },
        "daily_usage": daily_usage,
    }


@router.get("/admin/errors")
async def error_log(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    app: str | None = Query(default=None),
):
    """Recent failed requests for debugging."""
    _verify_admin(request, x_admin_key)

    app_clause = " AND app_id = ?" if app else ""
    app_clause_l = " AND l.app_id = ?" if app else ""
    app_params = (app,) if app else ()

    cursor = await db.execute(
        """SELECT l.id, l.user_id, u.email, l.provider, l.model,
            l.status, l.error_message, l.response_time_ms,
            l.request_timestamp, l.call_type, l.prompt_mode
           FROM usage_log l
           LEFT JOIN users u ON l.user_id = u.id
           WHERE l.status != 'success'
             AND l.request_timestamp >= date('now', ?)""" + app_clause_l + """
           ORDER BY l.request_timestamp DESC
           LIMIT ?""",
        (f"-{days} days", *app_params, limit),
    )
    errors = [
        {
            "id": r["id"],
            "user_email": r["email"],
            "provider": r["provider"],
            "model": r["model"],
            "status": r["status"],
            "error_message": r["error_message"],
            "response_time_ms": r["response_time_ms"],
            "timestamp": r["request_timestamp"],
            "call_type": display_call_type(r["call_type"], r["prompt_mode"]),
            "prompt_mode": r["prompt_mode"],
        }
        for r in await cursor.fetchall()
    ]

    # Error summary by type
    cursor = await db.execute(
        """SELECT status, COUNT(*) as count
           FROM usage_log
           WHERE status != 'success'
             AND request_timestamp >= date('now', ?)""" + app_clause + """
           GROUP BY status
           ORDER BY count DESC""",
        (f"-{days} days", *app_params),
    )
    by_status = {r["status"]: r["count"] for r in await cursor.fetchall()}

    # Error summary by provider
    cursor = await db.execute(
        """SELECT provider, COUNT(*) as count
           FROM usage_log
           WHERE status != 'success'
             AND request_timestamp >= date('now', ?)""" + app_clause + """
           GROUP BY provider
           ORDER BY count DESC""",
        (f"-{days} days", *app_params),
    )
    by_provider = {r["provider"]: r["count"] for r in await cursor.fetchall()}

    return {
        "errors": errors,
        "total": len(errors),
        "by_status": by_status,
        "by_provider": by_provider,
    }


@router.get("/admin/tiers")
async def get_tiers(
    request: Request,
    x_admin_key: str = Header(...),
):
    """View all tier configurations with their model/provider access rules.

    Re-reads the persistent tiers.json from disk so the dashboard always
    sees the current value of any JSON-sourced tunable (max_input_tokens
    today; more tunables to follow). The yaml-sourced fields come from
    tier_config which is loaded at startup; tunables come from
    app.services.tunable_config which prefers the JSON.
    """
    _verify_admin(request, x_admin_key)

    from app.routers.config import load_remote_configs
    from app.services.entitlements import resolved_features as _resolved_features
    from app.services.tunable_config import project_chat_max_input_tokens

    # Refresh remote_configs so any direct edit to /app/data/remote-config/
    # tiers.json shows up in the dashboard immediately.
    request.app.state.remote_configs = load_remote_configs()
    remote_configs = request.app.state.remote_configs

    tier_config = request.app.state.tier_config

    from app.services.search_caps import get_search_caps

    tiers = {}
    for name, tier in tier_config.tiers.items():
        # Resolve current search-cap values from tiers.json so the
        # dashboard renders whatever's persisted (admin tunable edits
        # land in the JSON, not yaml).
        sc = get_search_caps(remote_configs, name, locale=None)
        tiers[name] = {
            "display_name": tier.display_name,
            "default_model": tier.default_model,
            "monthly_cost_limit_usd": tier.monthly_cost_limit_usd,
            "requests_per_minute": tier.requests_per_minute,
            "summary_mode": tier.summary_mode,
            "summary_interval_minutes": tier.summary_interval_minutes,
            "allowed_providers": tier.allowed_providers,
            "allowed_models": tier.allowed_models,
            "max_images_per_request": tier.max_images_per_request,
            "storekit_product_id": tier.storekit_product_id,
            # Phase 2: feature states live in the entitlements matrix (the
            # persistent remote config), not tiers.yml — read the resolver.
            "features": _resolved_features(remote_configs, name),
            # JSON-sourced tunables (dashboard-editable, JSON file is the
            # source of truth, yaml is the fallback default).
            "max_input_tokens": project_chat_max_input_tokens(
                remote_configs, name, yaml_default=tier.max_input_tokens,
            ),
            "searches_per_month": sc.searches_per_month,
            "searches_soft_threshold": sc.searches_soft_threshold,
        }

    return {"tiers": tiers}


@router.get("/admin/entitlements")
async def get_entitlements(
    request: Request,
    x_admin_key: str = Header(...),
    app: str | None = Query(default=None),
):
    """Entitlements Phase 1 (docs/design/feature-entitlements.md §3):
    read-only, per-app aggregation of everything that decides what a user
    may do — the features × tiers matrix, the config-shaped knobs that
    grew outside it, and where each served value comes from (bundle vs
    overlay, via the drift machinery). Pure read; the editable matrix is
    Phase 2, and this view doubles as its migration-verification surface.
    """
    _verify_admin(request, x_admin_key)

    from app.routers.config import (
        detect_overlay_drift,
        load_apps,
        load_remote_configs,
        resolve_app_dir,
        tier_overrides_for_app,
    )
    from app.services.client_config import project_chat_max_input_chars
    from app.services.document_generation import load_generation_config
    from app.services.documents import load_documents_config
    from app.services.search_caps import get_search_caps

    apps = load_apps()
    app_id = (app or apps.get("default_app") or "shouldersurf").strip().lower()
    if app is not None and app_id not in (apps.get("apps") or {}):
        raise HTTPException(status_code=404, detail=f"Unknown app '{app}'")
    app_dir = resolve_app_dir(app_id)
    overrides = tier_overrides_for_app(app_id)

    # Same freshness rule as get_tiers: re-read the persistent overlay so
    # direct edits show up without a restart.
    request.app.state.remote_configs = load_remote_configs()
    remote_configs = request.app.state.remote_configs

    tier_config = request.app.state.tier_config
    feature_config = request.app.state.feature_config
    tier_names = list(tier_config.tiers)

    # 1. The features × tiers matrix — Phase 2: cells come from the
    # entitlements remote config through the SAME resolver enforcement
    # uses (features.yml still carries definitions/copy). Union both
    # directions so a matrix row missing a definition — or a defined
    # feature missing from the matrix — still renders; a missing cell
    # resolves "disabled", same as the resolver.
    from app.services.entitlements import (
        entitlement_matrix,
        entitlement_state,
    )
    feature_names = sorted(
        set(feature_config.features) | set(entitlement_matrix(remote_configs)))
    matrix = {}
    for fname in feature_names:
        fdef = feature_config.features.get(fname)
        matrix[fname] = {
            "display_name": fdef.display_name if fdef else fname,
            "description": fdef.description if fdef else None,
            "teaser_description": fdef.teaser_description if fdef else None,
            "upgrade_cta": fdef.upgrade_cta if fdef else None,
            "category": fdef.category if fdef else None,
            "tiers": {t: entitlement_state(remote_configs, t, fname)
                      for t in tier_names},
        }

    # 2. Config-shaped knobs outside the matrix, resolved through the SAME
    # loaders enforcement uses — the view shows what the server actually
    # does (defaults merged), not what a file happens to contain.
    docs_cfg = load_documents_config(remote_configs)
    gen_cfg = load_generation_config(remote_configs)
    # Derived per-tier availability, computed with the SAME rank logic the
    # gates use — this is the "which subscription level gets file
    # creation" answer, rendered in the per-tier table. Unranked tiers
    # (admin) read disabled because the real gate's tier check fails them
    # today (pre-existing _TIER_RANK gap, reported truthfully).
    from app.services.document_generation import _TIER_RANK

    def _tier_availability(cfg: dict) -> dict:
        return {
            t: bool(cfg.get("enabled"))
            and t in _TIER_RANK
            and _TIER_RANK[t] >= _TIER_RANK.get(cfg.get("min_tier"), 2)
            for t in tier_names
        }

    search = {}
    for t in tier_names:
        sc = get_search_caps(remote_configs, t, locale=None)
        search[t] = {
            "searches_per_month": sc.searches_per_month,
            "searches_soft_threshold": sc.searches_soft_threshold,
        }
    knobs = {
        "documents": {
            **{k: v for k, v in docs_cfg.items() if k != "generation"},
            "tier_availability": _tier_availability(docs_cfg),
        },
        "document_generation": {
            **gen_cfg,
            "tier_availability": _tier_availability(gen_cfg),
        },
        "project_chat_max_input_chars": {
            t: project_chat_max_input_chars(remote_configs, t)
            for t in tier_names},
        "search": search,
        "max_images_per_request": {
            t: overrides.get("max_images_per_request",
                             tier_config.tiers[t].max_images_per_request)
            for t in tier_names},
    }

    # 3. Provenance: served value = overlay; drifted pointers = overlay
    # differs from the repo bundle (#240's view of truth). The matrix
    # itself is boot-loaded repo YAML until Phase 2 moves it.
    drift = detect_overlay_drift()
    configs_prov = {}
    for name in ("entitlements", "client-config", "tiers"):
        # The exact slugs the knob loaders read. Enforcement is
        # app-agnostic today — documents/limits/search all read the flat
        # files; per-app dirs affect config SERVING, not these knobs — so
        # provenance must name the flat slug or it would misreport in any
        # environment where a per-app dir also exists.
        entry = remote_configs.get(name) or {}
        configs_prov[name] = {
            "slug": name if name in remote_configs else None,
            "version": entry.get("version"),
            "drifted_pointers": drift.get(name, []),
        }
    provenance = {
        "matrix_source": ("entitlements remote config (persistent, "
                          "dashboard-edited, hot-reloaded) x features.yml "
                          "definitions"),
        "configs": configs_prov,
        "tier_overrides": overrides,
    }

    return {
        "app": {
            "id": app_id,
            "label": ((apps.get("apps") or {}).get(app_id) or {}).get(
                "label", app_id),
            "dir": app_dir,
        },
        "apps": [{"id": aid, "label": (a or {}).get("label", aid)}
                 for aid, a in (apps.get("apps") or {}).items()],
        "tiers": tier_names,
        "matrix": matrix,
        "knobs": knobs,
        "provenance": provenance,
    }


@router.put("/admin/entitlements/matrix")
async def update_entitlements_matrix_cell(
    body: EntitlementsMatrixCellRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Flip one features × tiers cell (Phase 2). Validates against the
    closed enums, writes the persistent entitlements overlay, bumps its
    version, and hot-reloads remote_configs — enforcement, the served
    /v1/config/entitlements, and the dashboard all read the same object,
    so the flip takes effect on the next request."""
    _verify_admin(request, x_admin_key)

    from app.routers.config import CONFIG_DIR, load_remote_configs
    from app.services.entitlements import SLUG, STATES

    if body.feature not in request.app.state.feature_config.features:
        raise HTTPException(
            status_code=400, detail=f"Unknown feature '{body.feature}'")
    if body.tier not in request.app.state.tier_config.tiers:
        raise HTTPException(
            status_code=400, detail=f"Unknown tier '{body.tier}'")
    if body.state not in STATES:
        raise HTTPException(
            status_code=400,
            detail=f"state must be one of {', '.join(STATES)}")

    path = CONFIG_DIR / f"{SLUG}.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="entitlements config not seeded yet (restart seeds it "
                   "from the bundle)")
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not read {SLUG}.json: {exc}")

    matrix = data.setdefault("matrix", {})
    cells = matrix.setdefault(body.feature, {})
    old_state = cells.get(body.tier)
    if old_state == body.state:
        return {"status": "unchanged", "feature": body.feature,
                "tier": body.tier, "state": body.state}
    cells[body.tier] = body.state
    data["version"] = (data.get("version") or 0) + 1
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    request.app.state.remote_configs = load_remote_configs()
    logger.info(
        "entitlements_matrix_cell_updated feature=%s tier=%s %s->%s v%s",
        body.feature, body.tier, old_state, body.state, data["version"])
    return {
        "status": "updated",
        "feature": body.feature,
        "tier": body.tier,
        "old_state": old_state,
        "new_state": body.state,
        "version": data["version"],
    }


@router.put("/admin/entitlements/documents")
async def update_entitlements_documents(
    body: EntitlementsDocumentsRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Entitlements Phase 1.5 (Scott 2026-07-14): edit the documents knobs
    (enabled / min_tier for passthrough and generation) straight from the
    Entitlements tab. Writes the persistent client-config overlay — the
    single home documents keeps until the Phase 2 matrix fold — across ALL
    locale variants in lockstep, bumps each changed file's version, and
    hot-reloads remote_configs so enforcement flips on the same request.
    Closed enums on every field: a malformed write never lands.
    """
    _verify_admin(request, x_admin_key)

    from app.routers.config import CONFIG_DIR, load_remote_configs

    if body.scope not in ("passthrough", "generation"):
        raise HTTPException(
            status_code=400,
            detail='scope must be "passthrough" or "generation"')
    if body.enabled is None and body.min_tier is None:
        raise HTTPException(
            status_code=400,
            detail="provide at least one of enabled / min_tier")
    if body.min_tier is not None and body.min_tier not in (
            "free", "plus", "pro"):
        raise HTTPException(
            status_code=400,
            detail='min_tier must be "free", "plus", or "pro"')

    files_updated: list[dict] = []
    for slug in ("client-config", "client-config.es", "client-config.ja"):
        path = CONFIG_DIR / f"{slug}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not read {slug}.json: {exc}")
        docs = data.setdefault("documents", {})
        block = (docs if body.scope == "passthrough"
                 else docs.setdefault("generation", {}))
        changes: dict = {}
        if body.enabled is not None and block.get("enabled") != body.enabled:
            changes["enabled"] = {
                "old": block.get("enabled"), "new": body.enabled}
            block["enabled"] = body.enabled
        if (body.min_tier is not None
                and block.get("min_tier") != body.min_tier):
            changes["min_tier"] = {
                "old": block.get("min_tier"), "new": body.min_tier}
            block["min_tier"] = body.min_tier
        if changes:
            data["version"] = (data.get("version") or 0) + 1
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            files_updated.append(
                {"slug": slug, "version": data["version"], **changes})

    if files_updated:
        request.app.state.remote_configs = load_remote_configs()
        logger.info(
            "entitlements_documents_updated scope=%s enabled=%s min_tier=%s "
            "files=%s",
            body.scope, body.enabled, body.min_tier,
            [f["slug"] + ":v" + str(f["version"]) for f in files_updated],
        )

    return {
        "status": "updated" if files_updated else "unchanged",
        "scope": body.scope,
        "files_updated": files_updated,
    }


@router.get("/admin/user/{user_id}")
async def user_detail(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=30, ge=1, le=90),
    app: str | None = Query(default=None),
):
    """Detailed user view with budget, usage breakdown by call type, and query history."""
    _verify_admin(request, x_admin_key)
    tier_config = request.app.state.tier_config

    app_clause = " AND app_id = ?" if app else ""
    app_params = (app,) if app else ()

    # User info
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user_tier = row["tier"]
    tier = tier_config.tiers.get(user_tier)

    # Budget stats — filtered by the requested time period
    cursor = await db.execute(
        """SELECT
            COALESCE(SUM(COALESCE(input_tokens, 0)), 0) as input_tokens,
            COALESCE(SUM(COALESCE(output_tokens, 0)), 0) as output_tokens,
            COALESCE(SUM(COALESCE(cached_tokens, 0)), 0) as cached_tokens,
            COALESCE(SUM(COALESCE(estimated_cost_usd, 0)), 0) as total_cost,
            COUNT(*) as total_requests
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'""" + app_clause,
        (user_id, f"-{days} days", *app_params),
    )
    month_row = await cursor.fetchone()

    # Use the user's stored limit (set during verify-receipt/sync), fall back to tier config
    db_limit = row["monthly_cost_limit_usd"]
    if db_limit is not None and db_limit >= 0:
        monthly_limit = db_limit
    elif tier and tier.monthly_cost_limit_usd >= 0:
        monthly_limit = tier.monthly_cost_limit_usd
    else:
        monthly_limit = -1
    monthly_used = month_row["total_cost"]

    # Usage by call type. The SummaryConsolidation pass ships as
    # call_type="analysis"; relabel it "consolidation" for display so it
    # doesn't merge with genuine PostSessionAnalysis. Grouping on the CASE
    # (not on call_type) keeps the AVG latency weighted correctly. Mirrors
    # display_labels.display_call_type — keep the two in sync.
    cursor = await db.execute(
        """SELECT
            CASE WHEN call_type = 'analysis' AND prompt_mode = 'SummaryConsolidation'
                 THEN 'consolidation' ELSE call_type END AS call_type,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(cached_tokens), 0) as cached_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms,
            COALESCE(SUM(image_count), 0) as total_images
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'""" + app_clause + """
           GROUP BY CASE WHEN call_type = 'analysis' AND prompt_mode = 'SummaryConsolidation'
                 THEN 'consolidation' ELSE call_type END
           ORDER BY requests DESC""",
        (user_id, f"-{days} days", *app_params),
    )
    by_call_type = [
        {
            "call_type": r["call_type"] or "unknown",
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cached_tokens": r["cached_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
            "total_images": r["total_images"],
        }
        for r in await cursor.fetchall()
    ]

    # Usage by prompt mode
    cursor = await db.execute(
        """SELECT
            prompt_mode,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as total_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'""" + app_clause + """
           GROUP BY prompt_mode
           ORDER BY requests DESC""",
        (user_id, f"-{days} days", *app_params),
    )
    by_prompt_mode = [
        {
            "prompt_mode": r["prompt_mode"] or "unknown",
            "requests": r["requests"],
            "total_tokens": r["total_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Usage by model
    cursor = await db.execute(
        """SELECT
            provider, model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'""" + app_clause + """
           GROUP BY provider, model
           ORDER BY requests DESC""",
        (user_id, f"-{days} days", *app_params),
    )
    by_model = [
        {
            "provider": r["provider"],
            "model": r["model"],
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Daily usage trend (last N days)
    cursor = await db.execute(
        """SELECT
            date(request_timestamp) as day,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'""" + app_clause + """
           GROUP BY date(request_timestamp)
           ORDER BY day""",
        (user_id, f"-{days} days", *app_params),
    )
    daily_trend = [
        {"day": r["day"], "requests": r["requests"], "tokens": r["tokens"], "cost": round(r["cost"], 4)}
        for r in await cursor.fetchall()
    ]

    # Most recent coarse location for this user (country + region + city
    # derived from the IP via GeoIP at telemetry ingestion; the raw IP is never
    # stored, no lat/long). City collected since #318/#364 (2026-07-08) — null
    # on rows ingested before that or when the geo DB lacks it.
    loc_row = await (await db.execute(
        "SELECT country, region, city FROM telemetry_events "
        "WHERE user_id = ? AND country IS NOT NULL "
        "ORDER BY received_at DESC LIMIT 1",
        (user_id,),
    )).fetchone()
    location = (
        {"country": loc_row["country"], "region": loc_row["region"], "city": loc_row["city"]}
        if loc_row else None
    )

    return {
        "user": {
            "id": row["id"],
            "email": row["email"],
            "tier": user_tier,
            "created_at": row["created_at"],
            "is_active": bool(row["is_active"]),
            "location": location,
        },
        "budget": {
            "tier": user_tier,
            "monthly_limit_usd": round(monthly_limit, 2) if monthly_limit != -1 else -1,
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_remaining_usd": round(monthly_limit - monthly_used, 4) if monthly_limit != -1 else -1,
            "percent_used": round(monthly_used / monthly_limit * 100, 1) if monthly_limit > 0 else 0,
            "this_month": {
                "requests": month_row["total_requests"],
                "input_tokens": month_row["input_tokens"],
                "output_tokens": month_row["output_tokens"],
                "cached_tokens": month_row["cached_tokens"],
            },
        },
        "by_call_type": by_call_type,
        "by_prompt_mode": by_prompt_mode,
        "by_model": by_model,
        "daily_trend": daily_trend,
    }


@router.get("/admin/user/{user_id}/search-usage")
async def user_search_usage(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Per-user web-search usage history.

    Returns the live counter (`searches_used` from users table — the
    rolling-period number that the gate checks against) and a recent
    audit trail from `search_usage`. Useful for verifying the gate is
    counting accurately and for debugging "why did my CTA fire" reports.
    """
    _verify_admin(request, x_admin_key)

    # Live state — the same fields the chat-router gate reads.
    cursor = await db.execute(
        "SELECT searches_used, allocation_resets_at, tier "
        "FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    # Per-tier cap from current remote-config (English locale here —
    # the dashboard is admin-only, no localization needed).
    from app.services.search_caps import get_search_caps
    caps = get_search_caps(
        request.app.state.remote_configs,
        row["tier"],
        locale=None,
    )

    # Recent audit rows — one per search-bearing response.
    cursor = await db.execute(
        """SELECT id, request_timestamp, meeting_id, provider, model,
                  searches_count, search_cost_usd, usage_log_id
           FROM search_usage
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
           ORDER BY request_timestamp DESC
           LIMIT ?""",
        (user_id, f"-{days} days", limit),
    )
    rows = await cursor.fetchall()
    history = [
        {
            "id": r["id"],
            "timestamp": r["request_timestamp"],
            "meeting_id": r["meeting_id"],
            "provider": r["provider"],
            "model": r["model"],
            "searches_count": r["searches_count"],
            "search_cost_usd": r["search_cost_usd"],
            "usage_log_id": r["usage_log_id"],
        }
        for r in rows
    ]

    # Aggregates over the requested window — useful for "this user has
    # generated $X in search fees this month" queries.
    cursor = await db.execute(
        """SELECT COALESCE(SUM(searches_count), 0) AS total_searches,
                  COALESCE(SUM(search_cost_usd), 0) AS total_cost_usd,
                  COUNT(*) AS total_requests
           FROM search_usage
           WHERE user_id = ? AND request_timestamp >= date('now', ?)""",
        (user_id, f"-{days} days"),
    )
    agg = await cursor.fetchone()

    return {
        "user_id": user_id,
        "tier": row["tier"],
        "current_period": {
            "used": int(row["searches_used"] or 0),
            "total": caps.searches_per_month,
            "soft_threshold": caps.searches_soft_threshold,
            "resets_at": row["allocation_resets_at"],
        },
        "window_days": days,
        "window_aggregate": {
            "total_searches": int(agg["total_searches"] or 0),
            "total_cost_usd": round(float(agg["total_cost_usd"] or 0), 4),
            "total_requests": int(agg["total_requests"] or 0),
        },
        "history": history,
    }


@router.get("/admin/user/{user_id}/queries")
async def user_queries(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List individual queries for a user with raw request/response JSON."""
    _verify_admin(request, x_admin_key)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Get total count for pagination
    count_row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM usage_log WHERE user_id = ? AND request_timestamp >= ?",
        (user_id, cutoff),
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    cursor = await db.execute(
        """SELECT id, provider, model, input_tokens, output_tokens, cached_tokens,
                  estimated_cost_usd, response_time_ms, status, error_message,
                  call_type, prompt_mode, image_count, request_timestamp, metadata
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= ?
           ORDER BY request_timestamp DESC
           LIMIT ? OFFSET ?""",
        (user_id, cutoff, limit, offset),
    )
    rows = await cursor.fetchall()

    queries = []
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        queries.append({
            "id": row["id"],
            "provider": row["provider"],
            "model": row["model"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cached_tokens": row["cached_tokens"],
            "cost": row["estimated_cost_usd"],
            "latency_ms": row["response_time_ms"],
            "status": row["status"],
            "error": row["error_message"],
            "call_type": display_call_type(row["call_type"], row["prompt_mode"]),
            "prompt_mode": row["prompt_mode"],
            "image_count": row["image_count"],
            "timestamp": row["request_timestamp"],
            "raw_request": meta.get("raw_request"),
            "raw_response": meta.get("raw_response"),
        })

    return {"queries": queries, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/users")
async def list_users(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
    app: str | None = Query(default=None),
):
    """List all users with their usage stats, filtered by time period."""
    _verify_admin(request, x_admin_key)

    from app.services.device_models import to_marketing_name

    tier_config = request.app.state.tier_config

    # Optional per-app scoping. When `app` is set, every usage_log subquery
    # below (windowed and lifetime) is restricted to that app_id so the row
    # reflects a single app end to end; an empty/absent `app` keeps them
    # global. Telemetry device/locale subqueries stay unscoped — they
    # describe the user's device, not per-app activity. Each filtered
    # subquery contributes one `?` (only when `app` is set), in SELECT order.
    def _app_filt(alias: str) -> str:
        return f" AND {alias}.app_id = ?" if app else ""

    app_params = (app,) if app else ()

    # When an app is selected, HIDE users with no activity in it: keep only
    # users with at least one usage_log OR telemetry_events row tagged with
    # that app_id. So filtering by Tech Rehearsal drops users who only touch
    # SS, and vice versa. Users are shared across apps, so someone active in
    # both apps shows up under both filters. The two `?`s bind last (the
    # outer WHERE follows every SELECT-list subquery in the SQL text).
    app_user_filter = (
        " WHERE EXISTS (SELECT 1 FROM usage_log ux"
        " WHERE ux.user_id = u.id AND ux.app_id = ?)"
        " OR EXISTS (SELECT 1 FROM telemetry_events tx"
        " WHERE tx.user_id = u.id AND tx.app_id = ?)"
        if app else ""
    )
    app_user_params = (app, app) if app else ()

    cursor = await db.execute(
        f"""SELECT u.id, u.apple_sub, u.email, u.display_name, u.tier, u.created_at, u.is_active,
            u.simulated_tier, u.simulated_exhausted,
            u.monthly_used_usd, u.monthly_cost_limit_usd, u.allocation_resets_at,
            u.is_trial, u.trial_end,
            -- Windowed totals (last `days` days). The "window_" prefix
            -- makes the date filter visible at the call site so future
            -- editors don't confuse these with all-time aggregates.
            (SELECT COUNT(*) FROM usage_log l WHERE l.user_id = u.id AND l.status = 'success'
             AND l.request_timestamp >= date('now', ?){_app_filt('l')}) as window_requests,
            (SELECT COALESCE(SUM(COALESCE(l2.input_tokens,0)), 0)
             FROM usage_log l2 WHERE l2.user_id = u.id AND l2.status = 'success'
             AND l2.request_timestamp >= date('now', ?){_app_filt('l2')}) as window_input_tokens,
            (SELECT COALESCE(SUM(COALESCE(l2.output_tokens,0)), 0)
             FROM usage_log l2 WHERE l2.user_id = u.id AND l2.status = 'success'
             AND l2.request_timestamp >= date('now', ?){_app_filt('l2')}) as window_output_tokens,
            (SELECT COALESCE(SUM(l3.estimated_cost_usd), 0)
             FROM usage_log l3 WHERE l3.user_id = u.id AND l3.status = 'success'
             AND l3.request_timestamp >= date('now', ?){_app_filt('l3')}) as window_cost_usd,
            -- Lifetime aggregates (no date filter). Bounded by the
            -- usage_log table's lifetime, which is never purged today.
            (SELECT COUNT(*) FROM usage_log lt1 WHERE lt1.user_id = u.id AND lt1.status = 'success'{_app_filt('lt1')})
              as lifetime_requests,
            (SELECT COALESCE(SUM(COALESCE(lt2.input_tokens,0)), 0)
             FROM usage_log lt2 WHERE lt2.user_id = u.id AND lt2.status = 'success'{_app_filt('lt2')})
              as lifetime_input_tokens,
            (SELECT COALESCE(SUM(COALESCE(lt3.output_tokens,0)), 0)
             FROM usage_log lt3 WHERE lt3.user_id = u.id AND lt3.status = 'success'{_app_filt('lt3')})
              as lifetime_output_tokens,
            (SELECT COALESCE(SUM(lt4.estimated_cost_usd), 0)
             FROM usage_log lt4 WHERE lt4.user_id = u.id AND lt4.status = 'success'{_app_filt('lt4')})
              as lifetime_cost_usd,
            (SELECT MAX(l4.request_timestamp) FROM usage_log l4 WHERE l4.user_id = u.id{_app_filt('l4')}) as last_request,
            u.marketing_opt_in,
            -- Latest non-null device/version/locale from telemetry pings.
            -- Per-field "latest non-null" so an older build that doesn't send
            -- device_model/app_locale doesn't blank out a value a newer ping set.
            (SELECT t.app_locale FROM telemetry_events t
             WHERE t.user_id = u.id AND t.app_locale IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as app_locale,
            (SELECT t.os_version FROM telemetry_events t
             WHERE t.user_id = u.id AND t.os_version IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as os_version,
            (SELECT t.app_version FROM telemetry_events t
             WHERE t.user_id = u.id AND t.app_version IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as app_version,
            (SELECT t.device_model FROM telemetry_events t
             WHERE t.user_id = u.id AND t.device_model IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as device_model,
            -- Coarse location: latest non-null country/region/city from
            -- telemetry (GeoIP-derived at ingestion; never the raw IP). Unscoped
            -- like the other device columns — location is the user's, not per-app.
            (SELECT t.country FROM telemetry_events t
             WHERE t.user_id = u.id AND t.country IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as country,
            (SELECT t.region FROM telemetry_events t
             WHERE t.user_id = u.id AND t.region IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as region,
            (SELECT t.city FROM telemetry_events t
             WHERE t.user_id = u.id AND t.city IS NOT NULL
             ORDER BY t.received_at DESC LIMIT 1) as city
           FROM users u""" + app_user_filter + """
           ORDER BY u.created_at DESC""",
        (
            f"-{days} days", *app_params,   # window_requests
            f"-{days} days", *app_params,   # window_input_tokens
            f"-{days} days", *app_params,   # window_output_tokens
            f"-{days} days", *app_params,   # window_cost_usd
            *app_params,                    # lifetime_requests
            *app_params,                    # lifetime_input_tokens
            *app_params,                    # lifetime_output_tokens
            *app_params,                    # lifetime_cost_usd
            *app_params,                    # last_request
            *app_user_params,               # outer WHERE: hide non-app users
        ),
    )
    users = []
    for r in await cursor.fetchall():
        monthly_used = float(r["monthly_used_usd"] or 0)
        monthly_limit = float(r["monthly_cost_limit_usd"] or 0)
        window_cost = float(r["window_cost_usd"] or 0)
        tier_name = r["tier"]
        tier_def = tier_config.tiers.get(tier_name)

        # Derive the display gauge from `window_cost_usd` (the sum from
        # usage_log over the last `days` window), NOT from
        # `monthly_used_usd`. The latter is the budget-gate counter, and
        # `usage_tracker.record_cost` early-returns out of it for
        # unlimited tiers (`effective_limit == -1`) — so Plus/Pro/admin
        # users would always show 0 hours / 0% no matter how active they
        # are. usage_log is the source of truth, so derive from there.
        model_cost_per_hour = 0.19 if tier_def and "sonnet" in (tier_def.default_model or "") else 0.05
        hours_used = window_cost / model_cost_per_hour if model_cost_per_hour > 0 else 0
        hours_limit = monthly_limit / model_cost_per_hour if monthly_limit > 0 else -1
        percent_used = round(window_cost / monthly_limit * 100, 1) if monthly_limit > 0 else 0

        window_input = r["window_input_tokens"] or 0
        window_output = r["window_output_tokens"] or 0
        lifetime_input = r["lifetime_input_tokens"] or 0
        lifetime_output = r["lifetime_output_tokens"] or 0

        users.append({
            "id": r["id"],
            "apple_sub": r["apple_sub"][:8] + "..." if r["apple_sub"] else None,
            "email": r["email"],
            "display_name": r["display_name"],
            "tier": tier_name,
            "tier_display_name": tier_def.display_name if tier_def else tier_name,
            "created_at": r["created_at"],
            "is_active": bool(r["is_active"]),
            "simulated_tier": r["simulated_tier"],
            "simulated_exhausted": bool(r["simulated_exhausted"]),
            "is_trial": bool(r["is_trial"]),
            "trial_end": r["trial_end"],
            # Current month allocation
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_limit_usd": round(monthly_limit, 4),
            "percent_used": percent_used,
            "hours_used": round(hours_used, 1),
            "hours_limit": round(hours_limit, 1) if hours_limit != -1 else -1,
            "allocation_resets_at": r["allocation_resets_at"],
            # Windowed totals — bound by the `days` query parameter.
            "window_requests": r["window_requests"],
            "window_input_tokens": window_input,
            "window_output_tokens": window_output,
            "window_tokens": window_input + window_output,
            "window_cost_usd": round(window_cost, 4),
            # Lifetime totals — all time, no date filter.
            "lifetime_requests": r["lifetime_requests"],
            "lifetime_input_tokens": lifetime_input,
            "lifetime_output_tokens": lifetime_output,
            "lifetime_tokens": lifetime_input + lifetime_output,
            "lifetime_cost_usd": round(r["lifetime_cost_usd"] or 0, 4),
            "last_request": r["last_request"],
            # Profile / device dimensions (marketing opt-in from users;
            # language/iOS/device from the user's latest telemetry pings).
            "marketing_opt_in": bool(r["marketing_opt_in"]),
            "language": r["app_locale"],
            "ios_version": r["os_version"],
            "app_version": r["app_version"],
            "device": to_marketing_name(r["device_model"]),
            "location": (
                {"country": r["country"], "region": r["region"], "city": r["city"]}
                if r["country"] else None
            ),
        })

    return {"users": users, "count": len(users)}


# --- Telemetry summary (anonymous lifecycle pings) ---


@router.get("/admin/telemetry/summary")
async def telemetry_summary(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=30, ge=1, le=180),
):
    """Aggregated telemetry data for the admin dashboard's Telemetry tab.

    Reads from `telemetry_daily_rollups` (kept indefinitely, populated by
    the startup job in `app.services.telemetry_rollup`) so this endpoint
    can answer trend queries beyond the raw 30-day TTL on
    `telemetry_events`.
    """
    _verify_admin(request, x_admin_key)

    cursor = await db.execute(
        """SELECT day, metric, value
           FROM telemetry_daily_rollups
           WHERE day >= date('now', ?)
           ORDER BY day ASC, metric ASC""",
        (f"-{days} days",),
    )

    by_day: dict[str, dict[str, float]] = {}
    model_totals: dict[str, float] = {}
    for r in await cursor.fetchall():
        day, metric, value = r["day"], r["metric"], r["value"]
        by_day.setdefault(day, {})[metric] = value
        if metric.startswith("meetings_per_model:"):
            model_id = metric.split(":", 1)[1]
            model_totals[model_id] = model_totals.get(model_id, 0) + value

    series_keys = [
        "app_starts",
        "meetings_started",
        "meetings_stopped",
        "distinct_devices",
        "distinct_users",
    ]
    series = {
        k: [{"day": d, "value": by_day[d].get(k, 0)} for d in sorted(by_day)]
        for k in series_keys
    }

    # Duration aggregate over the window. Weight the average by
    # meetings_stopped that day so the period mean reflects activity,
    # not unweighted day averages.
    weighted_sum = 0.0
    weighted_n = 0.0
    min_secs: list[float] = []
    max_secs: list[float] = []
    for m in by_day.values():
        stopped = m.get("meetings_stopped", 0)
        if "duration_avg_sec" in m and stopped > 0:
            weighted_sum += m["duration_avg_sec"] * stopped
            weighted_n += stopped
        if "duration_min_sec" in m:
            min_secs.append(m["duration_min_sec"])
        if "duration_max_sec" in m:
            max_secs.append(m["duration_max_sec"])

    duration_summary = {
        "avg_sec": round(weighted_sum / weighted_n, 1) if weighted_n > 0 else None,
        "min_sec": min(min_secs) if min_secs else None,
        "max_sec": max(max_secs) if max_secs else None,
        "sample_size": int(weighted_n),
    }

    return {
        "days": days,
        "series": series,
        "models": sorted(
            ({"model_id": k, "meetings": v} for k, v in model_totals.items()),
            key=lambda x: -x["meetings"],
        ),
        "duration": duration_summary,
    }


@router.get("/admin/telemetry/rich")
async def telemetry_rich(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=30, ge=1, le=90),
    app: str | None = Query(default=None),
    app_version: str | None = Query(default=None),
    device_model: str | None = Query(default=None),
    model_id: str | None = Query(default=None),
    os_version: str | None = Query(default=None),
):
    """Ad-hoc rich telemetry rollups for the visual dashboard.

    Queries raw `telemetry_events` directly so we can slice by any
    dimension within the 30-day TTL window. Filters cascade — every
    query honors all four optional filters.

    The trade-off vs. /admin/telemetry/summary: this can't go past the
    raw-events TTL, but it can answer every breakdown the dashboard
    renders without pre-aggregation. For the dashboard's typical 7-30
    day windows that's the right call.
    """
    _verify_admin(request, x_admin_key)
    from app.services.device_models import to_marketing_name
    from app.services.model_display import to_display_name

    # Build a reusable WHERE clause + bound parameters.
    clauses = ["received_at >= datetime('now', ?)"]
    params: list[object] = [f"-{days} days"]
    if app:
        clauses.append("app_id = ?")
        params.append(app)
    if app_version:
        clauses.append("app_version = ?")
        params.append(app_version)
    if device_model:
        clauses.append("device_model = ?")
        params.append(device_model)
    if model_id:
        clauses.append("model_id = ?")
        params.append(model_id)
    if os_version:
        clauses.append("os_version = ?")
        params.append(os_version)
    where = " AND ".join(clauses)

    async def _all(sql: str, *extra: object) -> list[dict]:
        cur = await db.execute(sql, (*params, *extra))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # --- KPIs --------------------------------------------------------------
    kpi_rows = await _all(f"""
        SELECT
            COUNT(*) AS total_events,
            COUNT(DISTINCT device_id) AS distinct_devices,
            COUNT(DISTINCT user_id) AS distinct_users,
            SUM(CASE WHEN event_type='app_start' THEN 1 ELSE 0 END) AS app_starts,
            SUM(CASE WHEN event_type='meeting_start' THEN 1 ELSE 0 END) AS meeting_starts,
            SUM(CASE WHEN event_type='meeting_stop' THEN 1 ELSE 0 END) AS meeting_stops,
            AVG(CASE WHEN event_type='meeting_stop' THEN duration_seconds END) AS avg_duration_sec
        FROM telemetry_events
        WHERE {where}
    """)
    kpis = kpi_rows[0] if kpi_rows else {}

    # --- Daily app_starts stacked by version -------------------------------
    series_rows = await _all(f"""
        SELECT date(received_at) AS day,
               COALESCE(app_version, 'unknown') AS app_version,
               COUNT(*) AS n
        FROM telemetry_events
        WHERE {where} AND event_type='app_start'
        GROUP BY day, app_version
        ORDER BY day, app_version
    """)
    # Pivot into per-version series.
    days_seen: list[str] = sorted({r["day"] for r in series_rows})
    versions_seen: list[str] = sorted({r["app_version"] for r in series_rows})
    lookup = {(r["day"], r["app_version"]): r["n"] for r in series_rows}
    version_series = [
        {
            "name": v,
            "data": [{"x": d, "y": int(lookup.get((d, v), 0))} for d in days_seen],
        }
        for v in versions_seen
    ]

    # --- Meetings by model_id ---------------------------------------------
    raw_models = await _all(f"""
        SELECT COALESCE(model_id, 'unknown') AS model_id, COUNT(*) AS meetings
        FROM telemetry_events
        WHERE {where} AND event_type='meeting_start'
        GROUP BY model_id
        ORDER BY meetings DESC
    """)
    # Attach the product-facing display name (e.g. cloudzap/auto → "SS AI")
    # so the dashboard doesn't leak internal routing aliases to the operator.
    models = [
        {
            "model_id": r["model_id"],
            "display_name": (
                to_display_name(r["model_id"])
                if r["model_id"] != "unknown" else "unknown"
            ),
            "meetings": r["meetings"],
        }
        for r in raw_models
    ]

    # --- Meetings by device_model (with marketing name) -------------------
    raw_devices = await _all(f"""
        SELECT COALESCE(device_model, 'unknown') AS device_model, COUNT(*) AS meetings
        FROM telemetry_events
        WHERE {where} AND event_type='meeting_start'
        GROUP BY device_model
        ORDER BY meetings DESC
    """)
    # Null device_model on the wire means a pre-1.13 iOS build — SS only
    # added the field in 1.13. Label the bucket clearly so the dashboard
    # doesn't look like we have a mountain of unrecognized device codes,
    # which gives the wrong impression of mapping coverage. Decays
    # naturally as old builds churn out of the 30-day raw event TTL.
    _DEVICE_MISSING_LABEL = "Field missing (pre-1.13 build)"
    devices = [
        {
            "device_model": r["device_model"],
            "marketing_name": (
                to_marketing_name(r["device_model"])
                if r["device_model"] != "unknown" else _DEVICE_MISSING_LABEL
            ),
            "meetings": r["meetings"],
        }
        for r in raw_devices
    ]

    # --- Distinct devices by os_version -----------------------------------
    os_versions = await _all(f"""
        SELECT COALESCE(os_version, 'unknown') AS os_version,
               COUNT(DISTINCT device_id) AS devices
        FROM telemetry_events
        WHERE {where}
        GROUP BY os_version
        ORDER BY devices DESC
    """)

    # --- Heatmap: meetings by (day_of_week, hour) ------------------------
    # SQLite: strftime('%w') = day of week 0-6 (Sun=0). strftime('%H') = hour.
    heat_rows = await _all(f"""
        SELECT CAST(strftime('%w', received_at) AS INTEGER) AS dow,
               CAST(strftime('%H', received_at) AS INTEGER) AS hour,
               COUNT(*) AS n
        FROM telemetry_events
        WHERE {where} AND event_type='meeting_start'
        GROUP BY dow, hour
    """)
    heatmap = [{"dow": r["dow"], "hour": r["hour"], "n": r["n"]} for r in heat_rows]

    # --- Funnel (totals) -------------------------------------------------
    funnel = [
        {"stage": "App start", "n": int(kpis.get("app_starts") or 0)},
        {"stage": "Meeting start", "n": int(kpis.get("meeting_starts") or 0)},
        {"stage": "Meeting stop", "n": int(kpis.get("meeting_stops") or 0)},
    ]

    # --- Filter options (so the dashboard can populate dropdowns) ---------
    # Strip filter so dropdowns show ALL options, not just ones matching
    # the current filter set. Otherwise picking "iPhone 16" hides every
    # other device.
    opt_rows = await db.execute(
        "SELECT DISTINCT app_version, os_version, device_model, model_id "
        "FROM telemetry_events WHERE received_at >= datetime('now', ?)",
        (f"-{days} days",),
    )
    options = {
        "app_versions": set(),
        "os_versions": set(),
        "device_models": set(),
        "model_ids": set(),
    }
    for r in await opt_rows.fetchall():
        d = dict(r)
        for k, src in [
            ("app_versions", d["app_version"]),
            ("os_versions", d["os_version"]),
            ("device_models", d["device_model"]),
            ("model_ids", d["model_id"]),
        ]:
            if src:
                options[k].add(src)

    # --- Per-device directory: name (if signed in) + language -------------
    # One row per device. Name comes from users.display_name via the most
    # recent signed-in event; language from the latest app_locale ping.
    # Both are sparse by design: anonymous devices have no name, older
    # builds sent no locale.
    _LANG_NAMES = {
        "en": "English", "es": "Spanish", "ja": "Japanese", "fr": "French",
        "de": "German", "pt": "Portuguese", "zh": "Chinese", "it": "Italian",
        "ko": "Korean", "ru": "Russian", "nl": "Dutch", "ar": "Arabic",
    }

    def _lang_name(locale: str | None) -> str | None:
        if not locale:
            return None
        code = locale.replace("-", "_").split("_")[0].lower()
        return _LANG_NAMES.get(code, code)

    dir_rows = await _all(f"""
        SELECT e.device_id AS device_id,
          (SELECT COALESCE(NULLIF(u.display_name, ''), u.email)
             FROM telemetry_events e3 JOIN users u ON u.id = e3.user_id
             WHERE e3.device_id = e.device_id AND e3.user_id IS NOT NULL
             ORDER BY e3.received_at DESC LIMIT 1) AS name,
          (SELECT app_locale FROM telemetry_events e2 WHERE e2.device_id = e.device_id
             AND e2.app_locale IS NOT NULL ORDER BY e2.received_at DESC LIMIT 1) AS locale,
          (SELECT app_version FROM telemetry_events e4 WHERE e4.device_id = e.device_id
             AND e4.app_version IS NOT NULL ORDER BY e4.received_at DESC LIMIT 1) AS app_version,
          MAX(e.device_model) AS device_model,
          COUNT(*) AS events,
          MAX(e.received_at) AS last_seen,
          MAX(CASE WHEN e.user_id IS NOT NULL THEN 1 ELSE 0 END) AS signed_in
        FROM telemetry_events e
        WHERE {where}
        GROUP BY e.device_id
        ORDER BY events DESC
        LIMIT 200
    """)
    directory = []
    lang_devices: dict[str, int] = {}
    for r in dir_rows:
        lang = _lang_name(r["locale"])
        directory.append({
            "name": r["name"],
            "locale": r["locale"],
            "language": lang,
            "device": to_marketing_name(r["device_model"]) if r["device_model"] else None,
            "app_version": r["app_version"],
            "events": r["events"],
            "last_seen": r["last_seen"],
            "signed_in": bool(r["signed_in"]),
        })
        key = lang or "Unknown"
        lang_devices[key] = lang_devices.get(key, 0) + 1
    by_language = sorted(
        ({"language": k, "devices": v} for k, v in lang_devices.items()),
        key=lambda x: -x["devices"],
    )

    # --- Location breakdown: distinct users/devices by country + region --
    # Coarse GeoIP (country + region only; city and raw IP are never stored).
    # Honors the same filters. A null country means the geo DB isn't installed
    # or the ping predates geo capture — excluded from the breakdown.
    by_location = await _all(f"""
        SELECT country,
               COALESCE(NULLIF(region, ''), NULL) AS region,
               COUNT(DISTINCT device_id) AS devices,
               COUNT(DISTINCT user_id) AS users,
               COUNT(*) AS events
        FROM telemetry_events
        WHERE {where} AND country IS NOT NULL
        GROUP BY country, region
        ORDER BY devices DESC, users DESC
    """)

    return {
        "days": days,
        "filters": {
            "app_version": app_version,
            "device_model": device_model,
            "model_id": model_id,
            "os_version": os_version,
        },
        "kpis": {
            "total_events": int(kpis.get("total_events") or 0),
            "distinct_devices": int(kpis.get("distinct_devices") or 0),
            "distinct_users": int(kpis.get("distinct_users") or 0),
            "app_starts": int(kpis.get("app_starts") or 0),
            "meeting_starts": int(kpis.get("meeting_starts") or 0),
            "meeting_stops": int(kpis.get("meeting_stops") or 0),
            "avg_duration_sec": (
                round(kpis["avg_duration_sec"], 1)
                if kpis.get("avg_duration_sec") is not None else None
            ),
        },
        "version_series": version_series,
        "models": models,
        "directory": directory,
        "by_language": by_language,
        "devices": devices,
        "os_versions": os_versions,
        "by_location": by_location,
        "heatmap": heatmap,
        "funnel": funnel,
        "options": {k: sorted(v) for k, v in options.items()},
    }


# --- Email management (Resend webhook events + suppression list) ---


@router.get('/admin/email/stats')
async def email_stats(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
):
    """Aggregate email-event counts for the dashboard overview cards.

    Returns counts by event type over the last N days, the current
    suppression-list size, and a breakdown of suppression reasons.
    """
    _verify_admin(request, x_admin_key)

    cursor = await db.execute(
        '''SELECT event_type, COUNT(*) as count
           FROM email_events
           WHERE received_at >= datetime('now', ?)
           GROUP BY event_type
           ORDER BY count DESC''',
        (f'-{days} days',),
    )
    by_type = {r['event_type']: r['count'] for r in await cursor.fetchall()}

    cursor = await db.execute(
        '''SELECT COUNT(*) as count, MIN(received_at) as oldest, MAX(received_at) as newest
           FROM email_events
           WHERE received_at >= datetime('now', ?)''',
        (f'-{days} days',),
    )
    activity = (await cursor.fetchone()) or {}

    cursor = await db.execute(
        '''SELECT reason, COUNT(*) as count
           FROM email_suppression
           GROUP BY reason
           ORDER BY count DESC'''
    )
    by_reason = {r['reason']: r['count'] for r in await cursor.fetchall()}

    cursor = await db.execute('SELECT COUNT(*) as c FROM email_suppression')
    suppression_count = (await cursor.fetchone())['c']

    cursor = await db.execute(
        '''SELECT COUNT(*) as c FROM email_events
           WHERE event_type = 'email.bounced'
             AND bounce_type = 'hard'
             AND received_at >= datetime('now', ?)''',
        (f'-{days} days',),
    )
    hard_bounces = (await cursor.fetchone())['c']

    cursor = await db.execute(
        '''SELECT COUNT(*) as c FROM email_events
           WHERE event_type = 'email.complained'
             AND received_at >= datetime('now', ?)''',
        (f'-{days} days',),
    )
    complaints = (await cursor.fetchone())['c']

    total_events = sum(by_type.values())

    # Webhook health: is the signing secret reachable, and how long
    # since the last successful inbound event? Surfaces "we silently
    # stopped receiving events" — e.g. dashboard secret rotated but
    # SM not updated → 401 storm → no rows added → counters look
    # normal until you check this field.
    from app.secrets import get_secret as _get_secret
    webhook_secret_configured = bool(
        _get_secret("resend-webhook-secret", env_var="CZ_RESEND_WEBHOOK_SECRET")
    )
    cursor = await db.execute(
        "SELECT MAX(received_at) as last FROM email_events",
    )
    row = await cursor.fetchone()
    last_event_at = row["last"] if row else None

    return {
        'days': days,
        'total_events': total_events,
        'by_type': by_type,
        'hard_bounces': hard_bounces,
        'complaints': complaints,
        'suppression_count': suppression_count,
        'suppression_by_reason': by_reason,
        'oldest_event': activity['oldest'] if activity else None,
        'newest_event': activity['newest'] if activity else None,
        'webhook': {
            'signing_secret_configured': webhook_secret_configured,
            'last_event_received_at': last_event_at,
        },
    }


@router.get('/admin/email/events')
async def email_events_list(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    recipient: str | None = Query(default=None),
):
    """Paginated email-event log, filterable by type and recipient."""
    _verify_admin(request, x_admin_key)

    where = ['received_at >= datetime(\'now\', ?)']
    params: list = [f'-{days} days']
    if event_type:
        where.append('event_type = ?')
        params.append(event_type)
    if recipient:
        where.append('recipient = ?')
        params.append(recipient.strip().lower())

    where_sql = ' AND '.join(where)

    cursor = await db.execute(
        f'''SELECT id, event_type, recipient, email_id, bounce_type, received_at
            FROM email_events
            WHERE {where_sql}
            ORDER BY received_at DESC
            LIMIT ? OFFSET ?''',
        params + [limit, offset],
    )
    events = [dict(r) for r in await cursor.fetchall()]

    cursor = await db.execute(
        f'SELECT COUNT(*) as c FROM email_events WHERE {where_sql}',
        params,
    )
    total = (await cursor.fetchone())['c']

    return {'events': events, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/admin/email/suppression')
async def email_suppression_list(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Active suppression list (recipients we will never send to)."""
    _verify_admin(request, x_admin_key)

    cursor = await db.execute(
        '''SELECT recipient, reason, source_event_id, suppressed_at
           FROM email_suppression
           ORDER BY suppressed_at DESC
           LIMIT ? OFFSET ?''',
        (limit, offset),
    )
    rows = [dict(r) for r in await cursor.fetchall()]

    cursor = await db.execute('SELECT COUNT(*) as c FROM email_suppression')
    total = (await cursor.fetchone())['c']

    return {'suppression': rows, 'total': total, 'limit': limit, 'offset': offset}



# ---------------------------------------------------------------------------
# Critical-failure alert administration
#
# Recipients CRUD + incident history + test-send. The detection +
# email dispatch live in app/services/alerting.py; this surface is
# purely operator-facing.
# ---------------------------------------------------------------------------


class AlertRecipientRequest(BaseModel):
    """Create or update an alert recipient.

    `email` is required on create, optional on update (PATCH-style).
    `categories` is a list of category tokens from
    `alerting.KNOWN_CATEGORIES`. Empty or null = receive every category.
    """
    email: str | None = None
    display_name: str | None = None
    active: bool | None = None
    categories: list[str] | None = None


class AlertTestSendRequest(BaseModel):
    """Force a test alert send to every active recipient. Used by the
    dashboard to verify deliverability after adding a new address."""
    category: str = "cq_unreachable"
    subject: str = "test-send"
    note: str | None = None


@router.get("/admin/alerts/categories")
async def list_alert_categories(
    request: Request,
    x_admin_key: str = Header(...),
):
    """Stable category catalog. Dashboard renders the subscription
    picker against this; clients shouldn't hardcode the list."""
    _verify_admin(request, x_admin_key)
    from app.services.alerting import KNOWN_CATEGORIES
    return {
        "categories": [
            {"id": k, "label": v["label"], "description": v["description"]}
            for k, v in KNOWN_CATEGORIES.items()
        ],
    }


@router.get("/admin/alerts/recipients")
async def list_alert_recipients(
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """All recipients, active and inactive. UI distinguishes by the
    `active` column."""
    _verify_admin(request, x_admin_key)
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT id, email, display_name, active, categories, "
        "       created_at, updated_at "
        "FROM alert_recipients ORDER BY email"
    )
    rows = []
    for row in await cursor.fetchall():
        d = dict(row)
        d["active"] = bool(d["active"])
        try:
            d["categories"] = json.loads(d["categories"]) if d["categories"] else []
        except (json.JSONDecodeError, TypeError):
            d["categories"] = []
        rows.append(d)
    return {"recipients": rows}


@router.post("/admin/alerts/recipients")
async def create_alert_recipient(
    body: AlertRecipientRequest,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Add a recipient. Email is required, must be unique. Categories
    list is optional — empty/missing = subscribed to everything."""
    _verify_admin(request, x_admin_key)
    if not body.email or "@" not in body.email:
        raise HTTPException(status_code=400, detail="valid email required")

    import uuid
    from app.services.alerting import KNOWN_CATEGORIES

    if body.categories:
        bad = [c for c in body.categories if c not in KNOWN_CATEGORIES]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"unknown categories: {bad} (known: {list(KNOWN_CATEGORIES)})",
            )

    now = datetime.now(timezone.utc).isoformat()
    rid = str(uuid.uuid4())
    try:
        await db.execute(
            "INSERT INTO alert_recipients "
            "(id, email, display_name, active, categories, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rid,
                body.email.lower().strip(),
                body.display_name,
                1 if body.active is None else (1 if body.active else 0),
                json.dumps(body.categories) if body.categories else None,
                now, now,
            ),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="email already registered")

    return {"id": rid, "email": body.email.lower().strip()}


@router.patch("/admin/alerts/recipients/{recipient_id}")
async def update_alert_recipient(
    recipient_id: str,
    body: AlertRecipientRequest,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Patch a recipient. Only the fields present in the body are
    updated. Toggling `active=false` keeps the row but stops alerts."""
    _verify_admin(request, x_admin_key)
    from app.services.alerting import KNOWN_CATEGORIES

    if body.categories is not None and body.categories:
        bad = [c for c in body.categories if c not in KNOWN_CATEGORIES]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"unknown categories: {bad}",
            )

    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT id FROM alert_recipients WHERE id = ?", (recipient_id,),
    )
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="recipient not found")

    sets: list[str] = []
    args: list = []
    if body.email is not None:
        sets.append("email = ?")
        args.append(body.email.lower().strip())
    if body.display_name is not None:
        sets.append("display_name = ?")
        args.append(body.display_name)
    if body.active is not None:
        sets.append("active = ?")
        args.append(1 if body.active else 0)
    if body.categories is not None:
        sets.append("categories = ?")
        args.append(json.dumps(body.categories) if body.categories else None)

    if not sets:
        return {"id": recipient_id, "updated": False}

    sets.append("updated_at = ?")
    args.append(datetime.now(timezone.utc).isoformat())
    args.append(recipient_id)

    try:
        await db.execute(
            f"UPDATE alert_recipients SET {', '.join(sets)} WHERE id = ?",
            tuple(args),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="email already in use")

    return {"id": recipient_id, "updated": True}


@router.delete("/admin/alerts/recipients/{recipient_id}")
async def delete_alert_recipient(
    recipient_id: str,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    _verify_admin(request, x_admin_key)
    cursor = await db.execute(
        "DELETE FROM alert_recipients WHERE id = ?", (recipient_id,),
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="recipient not found")
    await db.commit()
    return {"id": recipient_id, "deleted": True}


@router.get("/admin/alerts/incidents")
async def list_alert_incidents(
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Recent incidents (open + resolved). Newest first. Used by the
    dashboard's history panel."""
    _verify_admin(request, x_admin_key)
    from app.services.alerting import list_incidents
    rows = await list_incidents(db, limit=limit)
    return {"incidents": rows}


@router.post("/admin/alerts/test-send")
async def test_send_alert(
    body: AlertTestSendRequest,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Fire a synthetic incident under the chosen category so all
    subscribed-active recipients receive an email. Useful after
    adding a new address to confirm deliverability + Resend DKIM
    setup. The synthetic incident lands in the history list and
    auto-resolves like any other."""
    _verify_admin(request, x_admin_key)
    from app.services.alerting import report_incident, KNOWN_CATEGORIES
    if body.category not in KNOWN_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown category: {body.category}",
        )

    settings = request.app.state.settings
    result = await report_incident(
        db,
        category=body.category,
        subject=f"test-send/{body.subject}",
        details={
            "test_send": True,
            "note": body.note or "Manual deliverability check from admin dashboard.",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
        from_addr=settings.alert_email_from,
    )
    return {
        "incident_id": result.incident_id,
        "is_new": result.is_new,
        "emailed_to": result.emailed_to,
        "suppressed_reason": result.suppressed_reason,
    }


# --- Cert pin manifest (admin) ---------------------------------------------
# Proposal: /Users/scottguida/ShoulderSurf/docs/CERT_PINNING_PROPOSAL.md
# Service: app/services/cert_pin_signing.py
# Public read endpoint: GET /v1/config/cert-pins (in app/routers/cert_pins.py).

class PublishCertPinsRequest(BaseModel):
    pins: list[str]
    days_valid: int = 60


@router.get("/admin/cert-pins/current")
async def admin_cert_pins_current(
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Show the most-recent signed manifest plus the public key so the
    operator can hand the public key to SS for baking into iOS. Also
    runs a round-trip signature verification as a sanity check — if it
    returns verified=false the signing key the server holds doesn't
    match the manifest in the DB (key rotation got out of sync).
    """
    _verify_admin(request, x_admin_key)
    from app.services.cert_pin_signing import (
        get_public_key_b64, latest_manifest, verify_signature,
    )
    settings = request.app.state.settings
    pub_b64 = get_public_key_b64(settings)
    manifest = await latest_manifest(db)
    verified = (
        verify_signature(pub_b64, manifest)
        if (pub_b64 and manifest) else None
    )
    return {
        "signing_configured": pub_b64 is not None,
        "public_key_b64": pub_b64,
        "manifest": manifest,
        "verified": verified,
    }


@router.get("/admin/provider-health/status")
async def admin_provider_health_status(
    request: Request,
    x_admin_key: str = Header(...),
):
    """Most recent probe result per provider. Backs the dashboard tile.
    Cache lives in app/services/provider_health._last_check (process
    memory; first tick after restart populates it ~10s in)."""
    _verify_admin(request, x_admin_key)
    from app.services.provider_health import get_last_check
    checks = get_last_check()
    return {
        "providers": {name: r.to_dict() for name, r in checks.items()},
        "interval_seconds": request.app.state.settings.provider_health_check_interval_seconds,
    }


@router.get("/admin/cert-pins/status")
async def admin_cert_pins_status(
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Banner data for the dashboard. Read-only, no side effects.
    Returned shape is consumed by app/static/admin.html."""
    _verify_admin(request, x_admin_key)
    from app.services.cert_pin_auto_republish import (
        compute_status, get_last_check,
    )
    from app.services.cert_pin_signing import latest_manifest
    settings = request.app.state.settings
    signing_configured = bool((settings.cert_pin_signing_key_raw_b64 or "").strip())
    current = await latest_manifest(db)
    return compute_status(
        signing_configured=signing_configured,
        current=current,
        last_check=get_last_check(),
    )


@router.post("/admin/cert-pins/publish")
async def admin_cert_pins_publish(
    body: PublishCertPinsRequest,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Sign and persist a new pin manifest with monotonically increasing
    version. Returns the wire-shape dict served at /v1/config/cert-pins.

    Pin format is whatever iOS expects — currently base64 SPKI SHA-256
    hashes — but this endpoint stores them as opaque strings so the wire
    format can evolve without a server change.
    """
    _verify_admin(request, x_admin_key)
    from app.services.cert_pin_signing import publish_manifest, CertPinSigningError
    suffix = (x_admin_key or "")[-6:]
    try:
        manifest = await publish_manifest(
            db, request.app.state.settings,
            pins=body.pins, days_valid=body.days_valid,
            admin_key_suffix=suffix,
        )
    except CertPinSigningError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return manifest


# --- Promo Campaign Management (#promo, slice 1) ---
#
# CRUD for server-decided promo campaigns. GP is the brains: these rows are the
# authored campaigns the decision engine reads (targeting/frequency/schedule are
# GP-internal; `variants` carry the SS-facing render payload). The Campaigns
# dashboard tab drives these endpoints. The decision engine + event ingestion +
# analytics are separate slices.

_CAMPAIGN_STATUSES = {"draft", "active", "paused", "archived"}
_CAMPAIGN_JSON_COLS = ("targeting", "frequency", "placements", "variants")
# Locked allowlist of CTA action targets a native variant may author. The
# client allowlists the same set and ignores anything else, so an unknown type
# in a payload can't make the app open something it shouldn't. "none" =
# display-only. Purchases (storekit_offer/paywall) resolve through StoreKit on
# device; prices and purchase tokens never ride in the payload.
_CTA_ACTION_TYPES = {"appstore", "storekit_offer", "paywall", "url", "deeplink", "none"}
# Plans a paywall CTA may feature (action.plan). Absent => default paywall, no
# featured plan. Matches the paid subscription tiers.
_PAYWALL_PLANS = {"plus", "pro"}
# Per-app allowlist of campaign-authorable deeplink targets. The client
# allowlists the same routes; GP only authors what it will accept. SS provided
# (2026-06-26): shouldersurf://record only — meeting/<uuid> and project/<uuid>
# exist but take the user's own on-device id, so they're not broadcast-authorable.
# TR routes TBD (they'll send when they add any).
_DEEPLINK_ALLOWLIST: dict[str, set[str]] = {
    "shouldersurf": {"shouldersurf://record"},
}


class CampaignBody(BaseModel):
    id: str                                   # marketer-set slug, e.g. tr_crosspromo_2026_07
    name: str
    app_id: str                               # which app (X-App-ID) it targets
    status: str = "draft"
    starts_at: str | None = None
    expires_at: str | None = None
    priority: int = 0
    mutual_exclusion_group: str | None = None
    targeting: dict = {}
    frequency: dict = {}
    placements: list = []
    variants: list = []


def _campaign_from_row(row) -> dict:
    """DB row -> API object, parsing the JSON columns back to structures."""
    d = dict(row)
    for col in _CAMPAIGN_JSON_COLS:
        raw = d.get(col)
        try:
            d[col] = json.loads(raw) if raw else ({} if col in ("targeting", "frequency") else [])
        except (json.JSONDecodeError, TypeError):
            d[col] = {} if col in ("targeting", "frequency") else []
    return d


@router.get("/admin/campaigns")
async def list_campaigns(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    app: str | None = Query(default=None),
):
    """List all promo campaigns (newest first), optionally scoped to an app."""
    _verify_admin(request, x_admin_key)
    where, params = "", ()
    if app:
        where, params = " WHERE app_id = ?", (app,)
    cur = await db.execute(
        f"SELECT * FROM promo_campaigns{where} ORDER BY updated_at DESC", params
    )
    rows = await cur.fetchall()
    return {"campaigns": [_campaign_from_row(r) for r in rows]}


@router.get("/admin/campaign/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    _verify_admin(request, x_admin_key)
    cur = await db.execute("SELECT * FROM promo_campaigns WHERE id = ?", (campaign_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found")
    return _campaign_from_row(row)


def _validate_campaign(body: CampaignBody) -> None:
    if body.status not in _CAMPAIGN_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_CAMPAIGN_STATUSES)}")
    # Variant weights, when present, should sum to 100 (soft check, allowed for draft).
    weights = [v.get("weight", 0) for v in body.variants if isinstance(v, dict)]
    if body.status == "active" and weights and sum(weights) != 100:
        raise HTTPException(status_code=400, detail=f"active campaign variant weights must sum to 100 (got {sum(weights)})")
    # Geo targeting shape: countries/regions/cities are lists; min_audience (the
    # privacy floor raise) is a non-negative int. Reject malformed so a campaign
    # can't ship a geo block the decision engine silently ignores.
    geo = body.targeting.get("geo")
    if geo is not None:
        if not isinstance(geo, dict):
            raise HTTPException(status_code=400, detail="targeting.geo must be an object")
        for k in ("countries", "regions", "cities"):
            if k in geo and not isinstance(geo[k], list):
                raise HTTPException(status_code=400, detail=f"targeting.geo.{k} must be a list")
    min_aud = body.targeting.get("min_audience")
    if min_aud is not None and (not isinstance(min_aud, int) or isinstance(min_aud, bool) or min_aud < 0):
        raise HTTPException(status_code=400, detail="targeting.min_audience must be a non-negative integer")
    # Each native CTA must declare an action.type from the locked allowlist
    # (incl. "none"); reject unknown so a payload can't ship a target the client
    # won't render. Optional per-CTA cta_id is the link-attribution key echoed
    # back on promo_click (promo_events.cta_id).
    for v in body.variants:
        if not isinstance(v, dict):
            continue
        # Capability gate: min_app_version (when present) must be a version string
        # like "1.6.0". resolve withholds the variant from clients below it, so
        # token/storekit cards only reach builds that can render them.
        mav = v.get("min_app_version")
        if mav is not None:
            parts = str(mav).split(".")
            if not (1 <= len(parts) <= 3 and all(p.isdigit() for p in parts)):
                raise HTTPException(status_code=400, detail="variant min_app_version must be a version string like 1.6.0")
        # Native render variants carry a versioned, additive native block. Reject
        # malformed so GP only authors what the client can render (same posture as
        # the deeplink allowlist).
        if v.get("render") == "native":
            nat = v.get("native")
            if not isinstance(nat, dict):
                raise HTTPException(status_code=400, detail="native render variant must carry a native block")
            if nat.get("schema_version") != 1:
                raise HTTPException(status_code=400, detail="native.schema_version must be 1")
            if not nat.get("title") or not isinstance(nat.get("title"), str):
                raise HTTPException(status_code=400, detail="native.title is required and must be a string")
            if nat.get("body") is not None and not isinstance(nat.get("body"), str):
                raise HTTPException(status_code=400, detail="native.body must be a string")
            media = nat.get("media")
            if media is not None:
                url = media.get("url") if isinstance(media, dict) else None
                if not isinstance(media, dict) or media.get("type") != "image":
                    raise HTTPException(status_code=400, detail="native.media.type must be 'image'")
                if not isinstance(url, str) or not url.startswith("https://"):
                    raise HTTPException(status_code=400, detail="native.media.url must be https")
        # Per-locale content overrides (partial native blocks). Validate the
        # shape and every override's CTAs against the same allowlist as the
        # base block — a locale variant must not smuggle an unknown action.
        locs = v.get("content_locales")
        if locs is not None:
            if not isinstance(locs, dict) or not all(
                    isinstance(k, str) and isinstance(o, dict) for k, o in locs.items()):
                raise HTTPException(
                    status_code=400,
                    detail="variant content_locales must map locale tags to override objects")
            for o in locs.values():
                if "title" in o and (not o["title"] or not isinstance(o["title"], str)):
                    raise HTTPException(status_code=400, detail="content_locales title must be a non-empty string")
                if "body" in o and o["body"] is not None and not isinstance(o["body"], str):
                    raise HTTPException(status_code=400, detail="content_locales body must be a string")
        _cta_sources = [(v.get("native") or {}).get("ctas") or []]
        for o in (locs or {}).values():
            _cta_sources.append(o.get("ctas") or [])
        for cta in [c for src_ in _cta_sources for c in src_]:
            if not isinstance(cta, dict):
                continue
            atype = (cta.get("action") or {}).get("type")
            if atype not in _CTA_ACTION_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"cta action.type must be one of {sorted(_CTA_ACTION_TYPES)} (got {atype!r})",
                )
            if atype == "deeplink":
                allowed = _DEEPLINK_ALLOWLIST.get(body.app_id, set())
                value = (cta.get("action") or {}).get("value")
                if value not in allowed:
                    raise HTTPException(
                        status_code=400,
                        detail=f"deeplink {value!r} not in {body.app_id} allowlist {sorted(allowed)}",
                    )
            if atype == "paywall":
                # value = optional placement id (string); absent => default paywall.
                # plan = optional featured plan; client highlights plus|pro.
                pv = (cta.get("action") or {}).get("value")
                if pv is not None and not isinstance(pv, str):
                    raise HTTPException(status_code=400, detail="paywall action.value must be a placement-id string when present")
                plan = (cta.get("action") or {}).get("plan")
                if plan is not None and plan not in _PAYWALL_PLANS:
                    raise HTTPException(status_code=400, detail=f"paywall action.plan must be one of {sorted(_PAYWALL_PLANS)} when present")
            cid = cta.get("cta_id")
            if cid is not None and not isinstance(cid, str):
                raise HTTPException(status_code=400, detail="cta_id must be a string when present")
            # Every native CTA needs button text. GP owns the wording (title,
            # body, and the CTA label), and a label-less CTA would render an
            # empty button. Served verbatim; the client reads native.ctas[].label.
            label = cta.get("label")
            if not label or not isinstance(label, str):
                raise HTTPException(status_code=400, detail="cta label is required and must be a non-empty string")



async def _enforce_geo_floor_on_activation(db, body: CampaignBody) -> None:
    """#318 §9: a geo-targeted campaign may only go ACTIVE when its matched geo
    segment meets the enforced min-audience floor (25, raisable via
    targeting.min_audience). Draft/paused saves are allowed regardless so a
    campaign can be authored ahead of audience growth; resolve enforces the
    same floor continuously as defense in depth."""
    if body.status != "active":
        return
    from app.routers.promo import GEO_MIN_AUDIENCE_FLOOR, _geo_audience, _geo_constraint, _geo_floor

    countries, regions, cities = _geo_constraint(body.targeting)
    if not (countries or regions or cities):
        return
    floor = _geo_floor(body.targeting)
    audience = await _geo_audience(db, countries, regions, cities)
    if audience < floor:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Geo-targeted campaign cannot activate: matched audience is "
                f"{audience} devices, below the privacy floor of {floor} "
                f"(enforced minimum {GEO_MIN_AUDIENCE_FLOOR}). Broaden the geo "
                f"constraint or save as draft/paused until the segment grows."
            ),
        )

@router.post("/admin/campaigns")
async def create_campaign(
    body: CampaignBody,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Create a campaign. id is the marketer-set slug and must be unique."""
    _verify_admin(request, x_admin_key)
    _validate_campaign(body)
    await _enforce_geo_floor_on_activation(db, body)
    existing = await (await db.execute("SELECT 1 FROM promo_campaigns WHERE id = ?", (body.id,))).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail=f"Campaign '{body.id}' already exists")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO promo_campaigns
           (id, name, status, app_id, starts_at, expires_at, priority, mutual_exclusion_group,
            targeting, frequency, placements, variants, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (body.id, body.name, body.status, body.app_id, body.starts_at, body.expires_at,
         body.priority, body.mutual_exclusion_group,
         json.dumps(body.targeting), json.dumps(body.frequency),
         json.dumps(body.placements), json.dumps(body.variants), now, now),
    )
    await db.commit()
    return {"status": "created", "id": body.id}


@router.put("/admin/campaign/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    body: CampaignBody,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Full update of an existing campaign (preserves created_at)."""
    _verify_admin(request, x_admin_key)
    _validate_campaign(body)
    await _enforce_geo_floor_on_activation(db, body)
    row = await (await db.execute("SELECT created_at FROM promo_campaigns WHERE id = ?", (campaign_id,))).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE promo_campaigns SET name=?, status=?, app_id=?, starts_at=?, expires_at=?,
           priority=?, mutual_exclusion_group=?, targeting=?, frequency=?, placements=?,
           variants=?, updated_at=? WHERE id=?""",
        (body.name, body.status, body.app_id, body.starts_at, body.expires_at, body.priority,
         body.mutual_exclusion_group, json.dumps(body.targeting), json.dumps(body.frequency),
         json.dumps(body.placements), json.dumps(body.variants), now, campaign_id),
    )
    await db.commit()
    return {"status": "updated", "id": campaign_id}


@router.delete("/admin/campaign/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    _verify_admin(request, x_admin_key)
    cur = await db.execute("DELETE FROM promo_campaigns WHERE id = ?", (campaign_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found")
    return {"status": "deleted", "id": campaign_id}


@router.get("/admin/campaign/{campaign_id}/report")
async def campaign_report(
    campaign_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Promo funnel for one campaign from promo_events: impressions, clicks
    (with per-CTA breakdown), dismisses, converts, CTR. Stand-in for the
    dashboard reporting slice — lets us read live results before that's built."""
    _verify_admin(request, x_admin_key)
    cur = await db.execute(
        "SELECT event_type, COUNT(*) AS n FROM promo_events WHERE campaign_id = ? GROUP BY event_type",
        (campaign_id,),
    )
    counts = {r["event_type"]: r["n"] for r in await cur.fetchall()}
    cur = await db.execute(
        "SELECT COALESCE(cta_id, '(none)') AS cta, COUNT(*) AS n FROM promo_events "
        "WHERE campaign_id = ? AND event_type = 'click' GROUP BY cta ORDER BY n DESC",
        (campaign_id,),
    )
    clicks_by_cta = {r["cta"]: r["n"] for r in await cur.fetchall()}
    impressions = counts.get("impression", 0)
    clicks = counts.get("click", 0)
    cur = await db.execute(
        "SELECT COUNT(DISTINCT device_id) AS n FROM promo_events WHERE campaign_id = ?",
        (campaign_id,),
    )
    reach = (await cur.fetchone())["n"]
    return {
        "campaign_id": campaign_id,
        "reach_devices": reach,
        "impressions": impressions,
        "clicks": clicks,
        "dismisses": counts.get("dismiss", 0),
        "converts": counts.get("convert", 0),
        "ctr": round(clicks / impressions, 4) if impressions else None,
        "clicks_by_cta": clicks_by_cta,
    }


@router.get("/admin/campaign/{campaign_id}/events")
async def campaign_events(
    campaign_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Raw interaction timeline for one campaign: each impression/click/dismiss/
    convert with its device, user, what was clicked (cta_id), and dwell
    (visible_ms). Newest first. Enriched with readable user (email/tier) and
    device (marketing name + locale), reusing the Users-tab pattern. Powers the
    dashboard Activity view."""
    _verify_admin(request, x_admin_key)
    from app.services.device_models import to_marketing_name

    cur = await db.execute(
        "SELECT created_at, device_id, user_id, event_type, cta_id, visible_ms "
        "FROM promo_events WHERE campaign_id = ? ORDER BY created_at DESC LIMIT ?",
        (campaign_id, limit),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    # Batch-resolve the opaque ids to readable info (2 queries, not N).
    user_ids = sorted({r["user_id"] for r in rows if r["user_id"]})
    device_ids = sorted({r["device_id"] for r in rows if r["device_id"]})
    users: dict[str, dict] = {}
    if user_ids:
        ph = ",".join("?" * len(user_ids))
        for u in await (await db.execute(
            f"SELECT id, email, display_name, tier FROM users WHERE id IN ({ph})", user_ids
        )).fetchall():
            users[u["id"]] = {"email": u["email"], "name": u["display_name"], "tier": u["tier"]}
    devices: dict[str, dict] = {}
    if device_ids:
        ph = ",".join("?" * len(device_ids))
        # Latest non-null device_model / app_locale per device (newest first, fill once).
        for t in await (await db.execute(
            f"SELECT device_id, device_model, app_locale FROM telemetry_events "
            f"WHERE device_id IN ({ph}) AND (device_model IS NOT NULL OR app_locale IS NOT NULL) "
            f"ORDER BY received_at DESC", device_ids
        )).fetchall():
            d = devices.setdefault(t["device_id"], {"device_model": None, "app_locale": None})
            if d["device_model"] is None and t["device_model"]:
                d["device_model"] = t["device_model"]
            if d["app_locale"] is None and t["app_locale"]:
                d["app_locale"] = t["app_locale"]

    out = []
    for r in rows:
        u = users.get(r["user_id"]) or {}
        dv = devices.get(r["device_id"]) or {}
        out.append({
            **r,
            "email": u.get("email"),
            "tier": u.get("tier"),
            "device": to_marketing_name(dv.get("device_model")),
            "locale": dv.get("app_locale"),
        })
    return {"campaign_id": campaign_id, "events": out}


# --- Subscription history / bookkeeping --------------------------------------

@router.get("/admin/subscriptions")
async def subscriptions_report(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Bookkeeping report: top-line summary + month-by-month tier history.

    Built from the append-only subscription_events log (users.tier is only the
    current state). Powers the dashboard Subscriptions tab."""
    _verify_admin(request, x_admin_key)
    from app.services import subscriptions as subs
    return {
        "summary": await subs.summary(db),
        "monthly": await subs.monthly_aggregates(db),
    }


@router.get("/admin/subscriptions/events")
async def subscriptions_events(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Newest subscription events across all users (enriched with email/tier)."""
    _verify_admin(request, x_admin_key)
    from app.services import subscriptions as subs
    return {"events": await subs.recent_events(db, limit=limit)}


@router.get("/admin/subscriptions/export.csv")
async def subscriptions_export_csv(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Month-by-month report as CSV for spreadsheets / accounting."""
    _verify_admin(request, x_admin_key)
    from app.services import subscriptions as subs
    months = await subs.monthly_aggregates(db)
    tiers = sorted({t for m in months for t in m["active_by_tier"]})
    header = ["month", *[f"active_{t}" for t in tiers], "active_total",
              "new_subscriptions", "churns", "gross_usd", "net_usd"]
    lines = [",".join(header)]
    for m in months:
        row = [m["month"], *[str(m["active_by_tier"].get(t, 0)) for t in tiers],
               str(m["active_total"]), str(m["new_subscriptions"]),
               str(m["churns"]), f"{m['gross_usd']:.2f}", f"{m['net_usd']:.2f}"]
        lines.append(",".join(row))
    return PlainTextResponse("\n".join(lines), media_type="text/csv")


@router.get("/admin/user/{user_id}/subscription")
async def user_subscription(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """One user's subscription state + full event timeline."""
    _verify_admin(request, x_admin_key)
    from app.services import subscriptions as subs
    urow = await (await db.execute(
        "SELECT id, email, tier, ever_subscribed, first_subscribed_at, "
        "original_transaction_id, is_trial FROM users WHERE id = ?", (user_id,),
    )).fetchone()
    if not urow:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    return {
        "user": {
            "id": urow["id"], "email": urow["email"], "tier": urow["tier"],
            "ever_subscribed": bool(urow["ever_subscribed"]),
            "first_subscribed_at": urow["first_subscribed_at"],
            "is_trial": bool(urow["is_trial"]),
            "linked_to_apple": bool(urow["original_transaction_id"]),
        },
        "timeline": await subs.user_timeline(db, user_id),
    }


@router.post("/admin/subscriptions/reconcile")
async def subscriptions_reconcile(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Trigger a reconciliation sweep against Apple's App Store Server API now.

    Returns {checked, fixed, fixes}. No-op ({skipped:'not_configured'}) until
    the App Store Server API key is provisioned."""
    _verify_admin(request, x_admin_key)
    from app.services import subscription_reconcile as recon
    return await recon.sweep(db)


class MintOfferCodesRequest(BaseModel):
    """Mint a batch of one-time-use codes against a configured offer."""
    offer_code_id: str
    number_of_codes: int = 10
    expiration_date: str  # ISO-8601 date "YYYY-MM-DD"


@router.post("/admin/offer-codes/mint")
async def mint_offer_codes(
    body: MintOfferCodesRequest, request: Request, x_admin_key: str = Header(...)
):
    """Mint one-time-use subscription offer codes via the App Store Connect API
    and return the redeemable code strings.

    `offer_code_id` is the configured `subscriptionOfferCodes` resource (set up
    once by hand in App Store Connect). 400 with a clear message until the
    Connect API key is provisioned. Apple requires 10-10000 codes per batch."""
    _verify_admin(request, x_admin_key)
    from app.services import offer_codes
    try:
        return await offer_codes.mint_and_fetch(
            body.offer_code_id, body.number_of_codes, body.expiration_date
        )
    except offer_codes.OfferCodeError as e:
        raise HTTPException(status_code=400, detail=str(e))


class LoadPoolRequest(BaseModel):
    """Load a minted (or sandbox) batch's codes into the dispense pool for an
    (offer_id, environment). Idempotent — re-loading the same batch is a no-op."""
    offer_id: str                        # ASC subscriptionOfferCodes id
    environment: str                     # sandbox|production
    batch_id: str                        # ASC one-time-use batch id to pull values from
    product_id: str | None = None


@router.post("/admin/offer-codes/load-pool")
async def load_offer_code_pool(
    body: LoadPoolRequest,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Pull a batch's code strings from the Connect API and load them into the
    dispense pool as 'available'. Use after minting a production batch (or with
    the existing sandbox batch id) to stock a campaign's storekit_offer CTA."""
    _verify_admin(request, x_admin_key)
    if body.environment not in ("sandbox", "production"):
        raise HTTPException(status_code=400, detail="environment must be sandbox|production")
    from app.services import offer_codes, offer_dispense
    try:
        codes = await offer_codes.fetch_code_values(body.batch_id)
    except offer_codes.OfferCodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = await offer_dispense.load_pool(
        db, offer_id=body.offer_id, environment=body.environment,
        codes=codes, batch_id=body.batch_id, product_id=body.product_id,
    )
    status = await offer_dispense.pool_status(
        db, offer_id=body.offer_id, environment=body.environment
    )
    return {**result, "pool": status}


@router.get("/admin/offer-codes/pool-status")
async def offer_code_pool_status(
    request: Request,
    offer_id: str,
    environment: str,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Available / reserved counts for a dispense pool — the exhaustion gauge."""
    _verify_admin(request, x_admin_key)
    from app.services import offer_dispense
    return await offer_dispense.pool_status(db, offer_id=offer_id, environment=environment)


# --- Promo creatives (hot-reloadable, no deploy) -----------------------------

# --- Force-upgrade: instant runtime flip (#force-version-gate break-glass) ----


class AppVersionOverrideRequest(BaseModel):
    """Patch a bundle/platform's force-upgrade floor at runtime. Only the
    provided fields change; omit a field to leave it as-is."""
    bundle_id: str
    platform: str = "ios"
    min_supported_version: str | None = None
    min_supported_blocking: bool | None = None
    blocked_versions: list[str] | None = None


def _reload_app_versions(request: Request):
    from app.services import app_version as av
    from app.config import get_settings
    request.app.state.app_versions = av.load_effective(get_settings().app_versions_path)
    return request.app.state.app_versions


@router.get("/admin/app-version")
async def get_app_version_state(request: Request, x_admin_key: str = Header(...)):
    """Effective force-upgrade registry (bundle YAML + runtime overlay) plus the
    raw overlay, so an operator can see exactly what's flipped."""
    _verify_admin(request, x_admin_key)
    from app.services import app_version as av
    return {
        "effective": getattr(request.app.state, "app_versions", {}) or {},
        "overlay": av.load_overlay(),
    }


@router.post("/admin/app-version/override")
async def app_version_override(
    body: AppVersionOverrideRequest, request: Request, x_admin_key: str = Header(...)
):
    """Break-glass: flip a build off NOW. Persists an override to the overlay and
    reloads the live registry in one shot, so the version gate enforces it on the
    very next request — no PR, no deploy. Set min_supported_blocking=true to hard
    gate below the floor, and/or blocked_versions to cut off specific builds."""
    _verify_admin(request, x_admin_key)
    from app.services import app_version as av
    overlay = av.load_overlay()
    plat = overlay.setdefault(body.bundle_id, {}).setdefault("platforms", {}).setdefault(body.platform, {})
    if body.min_supported_version is not None:
        plat["min_supported_version"] = body.min_supported_version
    if body.min_supported_blocking is not None:
        plat["min_supported_blocking"] = body.min_supported_blocking
    if body.blocked_versions is not None:
        plat["blocked_versions"] = body.blocked_versions
    av.save_overlay(overlay)
    eff = _reload_app_versions(request)
    effective_plat = (eff.get(body.bundle_id, {}).get("platforms", {}) or {}).get(body.platform, {})
    return {"status": "applied", "bundle_id": body.bundle_id, "platform": body.platform,
            "effective": effective_plat, "overlay": plat}


@router.delete("/admin/app-version/override/{bundle_id:path}")
async def app_version_override_clear(
    bundle_id: str, request: Request, platform: str = "ios", x_admin_key: str = Header(...)
):
    """Revert a bundle/platform to its YAML floor (clears the runtime override)
    and reloads live. Use this to STAND DOWN an incident cutoff."""
    _verify_admin(request, x_admin_key)
    from app.services import app_version as av
    overlay = av.load_overlay()
    changed = False
    if bundle_id in overlay:
        plats = (overlay[bundle_id].get("platforms") or {})
        if platform in plats:
            del plats[platform]
            changed = True
        if not plats:
            del overlay[bundle_id]
    av.save_overlay(overlay)
    _reload_app_versions(request)
    return {"status": "cleared" if changed else "no_override", "bundle_id": bundle_id, "platform": platform}


@router.get("/admin/promo-assets")
async def list_promo_assets(request: Request, x_admin_key: str = Header(...)):
    """List promo creatives — bundled defaults plus any live-edited store copies."""
    _verify_admin(request, x_admin_key)
    from app.services import promo_assets
    return {"assets": promo_assets.listing()}


@router.put("/admin/promo-asset/{name}")
async def upload_promo_asset(name: str, request: Request, x_admin_key: str = Header(...)):
    """Upload/replace a promo creative live (raw HTML or image body). Writes the
    store copy, which wins over the bundled default at serve time — no code deploy."""
    _verify_admin(request, x_admin_key)
    from app.services import promo_assets
    if promo_assets.safe_name(name) is None:
        raise HTTPException(status_code=400, detail="name must be a flat .html/.png/.jpg/.webp/.svg/.gif filename")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="empty body")
    if len(content) > promo_assets.MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"creative exceeds {promo_assets.MAX_BYTES} bytes")
    promo_assets.save(name, content)
    return {"status": "saved", "name": name, "bytes": len(content), "source": "store"}


@router.delete("/admin/promo-asset/{name}")
async def delete_promo_asset(name: str, request: Request, x_admin_key: str = Header(...)):
    """Remove the live store copy (serve reverts to the bundled default if any)."""
    _verify_admin(request, x_admin_key)
    from app.services import promo_assets
    if promo_assets.remove(name):
        return {"status": "deleted", "name": name}
    raise HTTPException(status_code=404, detail="no live copy (bundled default unaffected)")


# --- Transcript cleanup: original vs cleaned --------------------------------

@router.get("/admin/meeting/{meeting_id}/transcripts")
async def get_meeting_transcripts(
    meeting_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Raw (OCR) transcript and the cleaned version for one meeting, for the
    original-vs-cleaned view. `cleaned` is null until cleanup has run."""
    _verify_admin(request, x_admin_key)
    cur = await db.execute(
        "SELECT transcript, cleaned_transcript, cleaned_at FROM meeting_transcripts WHERE meeting_id = ?",
        (meeting_id,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No transcript for meeting '{meeting_id}'")
    raw = row["transcript"] or ""
    cleaned = row["cleaned_transcript"]
    return {
        "meeting_id": meeting_id,
        "raw": raw,
        "cleaned": cleaned,
        "cleaned_at": row["cleaned_at"],
        "raw_chars": len(raw),
        "cleaned_chars": len(cleaned) if cleaned else 0,
    }


@router.post("/admin/meeting/{meeting_id}/clean-transcript")
async def clean_meeting_transcript(
    meeting_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Run the OCR cleanup pass on a meeting's stored raw transcript on demand
    (bypasses the client transcript_source gate), persist the result, and return
    both. Lets us clean any meeting and see before/after without the client."""
    _verify_admin(request, x_admin_key)
    cur = await db.execute(
        "SELECT transcript FROM meeting_transcripts WHERE meeting_id = ?", (meeting_id,)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No transcript for meeting '{meeting_id}'")
    raw = row["transcript"] or ""

    from app.services.transcript_cleanup import clean_transcript
    cleaned = await clean_transcript(
        request.app.state.provider_router,
        raw,
        request.app.state.remote_configs,
        "ocr_captions",
        locale="en",
        meeting_id=meeting_id,
    )
    if not cleaned:
        raise HTTPException(
            status_code=502,
            detail="cleanup produced no output (prompt missing, input empty/too large, or model failed)",
        )
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE meeting_transcripts SET cleaned_transcript = ?, cleaned_at = ? WHERE meeting_id = ?",
        (cleaned, now, meeting_id),
    )
    await db.commit()
    return {
        "meeting_id": meeting_id,
        "raw_chars": len(raw),
        "cleaned_chars": len(cleaned),
        "cleaned_at": now,
        "raw": raw,
        "cleaned": cleaned,
    }
