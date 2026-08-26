"""Force-upgrade enforcement (#force-version-gate).

Server-side teeth behind the in-app force-upgrade gate: when an app's
`min_supported_blocking` flag is on, the gateway rejects below-floor builds of
that app with HTTP 426 across the LLM / Context Quilt / config paths — cutting a
compromised or broken build off immediately, even mid-session, without depending
on the user choosing to update.

Two safety properties are non-negotiable:
  - DEFAULT OFF. A floor with `min_supported_blocking` false serves normally; only
    the flag (or an explicit `blocked_versions` entry) ever blocks. A mistaken
    min_supported_version bump on its own can never lock the install base out.
  - FAIL OPEN. Any ambiguity — no version header, an unparseable version, an
    unknown app, no floor configured — means DO NOT block. We only 426 when we
    positively know the build is below an actively-blocking floor (or is
    explicitly blocklisted).

Contract: docs/wire-contracts/app-version-endpoint.md.
"""

from __future__ import annotations

DEFAULT_MESSAGE = "A newer version of the app is required to continue. Please update."


def _semver(v: str) -> tuple[int, int, int] | None:
    """Parse a dotted marketing version (CFBundleShortVersionString) to a 3-tuple.
    Returns None when it can't be parsed — the caller treats that as fail-open."""
    parts = str(v).strip().split(".")
    if not parts or not parts[0]:
        return None
    out: list[int] = []
    for p in parts[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        if digits == "":
            return None
        out.append(int(digits))
    while len(out) < 3:
        out.append(0)
    return tuple(out)  # type: ignore[return-value]


def _bundle_for_app(apps_registry: dict, app_id: str | None) -> str | None:
    """Resolve an X-App-ID slug to its Apple bundle id via apps.yml. Case-
    insensitive. None when the app is unknown or has no bundle_id (fail open)."""
    if not app_id:
        return None
    apps = (apps_registry or {}).get("apps", {}) or {}
    want = app_id.strip().lower()
    for slug, entry in apps.items():
        if str(slug).lower() == want and isinstance(entry, dict):
            bid = entry.get("bundle_id")
            return str(bid) if bid else None
    return None


def _payload(platform: dict) -> dict:
    """The 426 body the client renders its hard gate from. Shape agreed with SS:
    code / message / upgrade_url / min_supported_version, top-level."""
    upgrade_url = None
    latest = platform.get("latest")
    if isinstance(latest, dict):
        upgrade_url = latest.get("upgrade_url")
    upgrade_url = upgrade_url or platform.get("upgrade_url")
    return {
        "code": "upgrade_required",
        "message": platform.get("blocking_message") or DEFAULT_MESSAGE,
        "upgrade_url": upgrade_url,
        "min_supported_version": platform.get("min_supported_version"),
    }


# --- Per-route build floor: report WRITES (Scott via CQ, 2026-08-26) ---------
#
# Distinct from the app-wide 426 gate above on purpose. An old build (335)
# regenerated a Spanish meeting's report in English because it predates the
# transcript_language field, and iCloud sync overwrote the Spanish report with
# it. The app-wide gate would cut that build off entirely; this floor only
# stops it from GENERATING or overwriting a report. Reads and chat keep
# working, so nobody is locked out, and the refusal is a 412 with a code the
# client can name rather than a silent 200 or the hard-gate 426.
#
# Same fail-open discipline as `evaluate`: no floor configured, unknown app,
# or no build we can read => allow.

def build_number(app_build: str | None, user_agent: str | None,
                 ua_app_name: str | None = None) -> int | None:
    """The client's CFBundleVersion as an int. X-App-Build first. Else, when
    the caller names the app, the leading "<CFBundleName>/<build>" token of
    a default URLSession User-Agent ("Shoulder%20Surf/335 CFNetwork/...").
    Build 335 predates X-App-Build and set no custom UA, so that token is
    the only way to read it. Only the NAMED app's token counts: a stray
    "python-httpx/0" must read as unknown, never as build 0. None when
    nothing is readable, which callers treat as allow."""
    if app_build is not None:
        raw = str(app_build).strip()
        if raw.isdigit():
            return int(raw)
    if user_agent and ua_app_name:
        from re import escape, match
        from urllib.parse import unquote
        m = match(escape(ua_app_name) + r"/(\d+)(?![\d.])", unquote(user_agent.strip()))
        if m:
            return int(m.group(1))
    return None


def report_write_floor(version_registry: dict, apps_registry: dict, app_id: str | None,
                       platform: str = "ios") -> int | None:
    """The `report_write_min_build` for this app's platform, or None when no
    floor is configured (never block by default)."""
    bundle = _bundle_for_app(apps_registry, app_id)
    if not bundle:
        return None
    entry = (version_registry or {}).get(bundle)
    if not isinstance(entry, dict):
        return None
    plat = (entry.get("platforms") or {}).get(platform)
    if not isinstance(plat, dict):
        return None
    raw = plat.get("report_write_min_build")
    try:
        floor = int(raw)
    except (TypeError, ValueError):
        return None
    return floor if floor > 0 else None


def user_agent_app_name(version_registry: dict, apps_registry: dict, app_id: str | None,
                        platform: str = "ios") -> str | None:
    """The CFBundleName a default URLSession UA leads with, served beside the
    floor so the UA fallback only ever reads THIS app's token."""
    bundle = _bundle_for_app(apps_registry, app_id)
    entry = (version_registry or {}).get(bundle) if bundle else None
    plat = ((entry or {}).get("platforms") or {}).get(platform) if isinstance(entry, dict) else None
    name = plat.get("user_agent_app_name") if isinstance(plat, dict) else None
    return str(name) if name else None


def report_write_refusal(min_build: int, app_build: int | None) -> dict:
    """The 412 body. Named code, the numbers, and what to do about it."""
    return {
        "code": "report_build_floor",
        "message": "This build cannot generate reports; update Shoulder Surf.",
        "min_build": min_build,
        "app_build": app_build,
        "recovery_action": "update_app",
    }


def evaluate(
    version_registry: dict,
    apps_registry: dict,
    app_id: str | None,
    app_version: str | None,
    app_build: str | None,
    platform: str = "ios",
) -> dict | None:
    """Decide whether to 426 this request. Returns the 426 body dict to block, or
    None to allow. None on every ambiguous/unconfigured case (fail open)."""
    bundle = _bundle_for_app(apps_registry, app_id)
    if not bundle:
        return None  # unknown app / no bundle mapping -> never block
    entry = version_registry.get(bundle)
    if not isinstance(entry, dict):
        return None
    plat = (entry.get("platforms") or {}).get(platform)
    if not isinstance(plat, dict):
        return None  # no floor config for this platform -> never block

    # Surgical blocklist: an exact marketing version or build is cut off even
    # when above the floor, regardless of the blocking flag.
    blocked = {str(b) for b in (plat.get("blocked_versions") or [])}
    if (app_version and str(app_version) in blocked) or (app_build and str(app_build) in blocked):
        return _payload(plat)

    # Floor enforcement only when the flag is explicitly on.
    if not plat.get("min_supported_blocking"):
        return None
    floor = _semver(plat.get("min_supported_version") or "")
    have = _semver(app_version or "")
    if floor is None or have is None:
        return None  # can't compare confidently -> fail open
    if have < floor:
        return _payload(plat)
    return None
