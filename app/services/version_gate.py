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
