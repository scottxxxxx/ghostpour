"""Per-app spend caps, and which apps stay on the shared account meter.

THERE ARE TWO METERS AND THEY ARE NOT THE SAME METER. Confusing them is how
we got here, so this module names both out loud.

    users.monthly_used_usd   ONE row per ACCOUNT. What the ShoulderSurf tier
                             allowance is measured against. `record_cost`
                             has always written here with no app argument.

    usage_log.app_id         One row per REQUEST, app-attributed since #249.
                             What a per-app cap is measured against.

Every app under this developer team shares one account row, because SIWA
issues subject ids per team. So before this module, a Tech Rehearsal or
N-400 call drew down the user's SHOULDERSURF allowance, and a TR user was
metered twice: once by TR's own cap and again by an allowance belonging to
an app they may never have opened. Both gates were individually correct and
neither could see the other, which is the shape rule 8 exists for.

The fix is deliberately conservative and has a direction:

  - An app is taken OFF the shared account meter only when it carries its
    own ENFORCED cap (`budget.enabled` and `budget.own_account_meter`).
    Removing an app from the shared meter without giving it a cap of its
    own converts a double-metered app into an UNCAPPED one, which is worse
    than the bug.
  - A cap whose served value cannot be read falls back to the floor
    declared in apps.yml rather than to no cap. For money, absent config
    must never mean unlimited.

Cap SHAPE lives in apps.yml (code, needs a deploy) and cap VALUE lives in
served config (a dial). That split is the point: whether an app meters
itself is structural, what its ceiling is this month is not. Read
`config/remote/OWNERSHIP.md` before moving a value: adding a key propagates
on boot, CHANGING one does not, so a number edited in this repo is not a
number in production until it is pushed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("ghostpour.app_budget")

# Cap shapes. `entitlement` is Tech Rehearsal's existing gate, keyed by the
# X-TR-Entitlement header (see tr_budget.py, which still owns it). `flat` is
# one ceiling for every caller of the app, which is what an app whose paid
# tier never reaches GP needs.
SHAPE_FLAT = "flat"
SHAPE_ENTITLEMENT = "entitlement"


def app_entry(apps_registry: dict, app_id: str | None) -> dict:
    norm = (app_id or "").strip().lower()
    if not norm:
        return {}
    return (apps_registry.get("apps") or {}).get(norm) or {}


def budget_config(apps_registry: dict, app_id: str | None) -> dict:
    return app_entry(apps_registry, app_id).get("budget") or {}


def meters_on_its_own(apps_registry: dict, app_id: str | None) -> bool:
    """True when this app's spend must NOT be added to the account row.

    Requires BOTH `enabled` and `own_account_meter`, so an app cannot leave
    the shared meter by declaring an intention. It leaves by having a cap
    that actually runs.
    """
    cfg = budget_config(apps_registry, app_id)
    return bool(cfg.get("enabled")) and bool(cfg.get("own_account_meter"))


def flat_cap_usd(remote_configs: dict | None, apps_registry: dict,
                 app_id: str | None) -> float | None:
    """This app's flat monthly ceiling in USD, or None when none applies.

    None means "this gate does not run", which is correct for an app with no
    budget block and for the entitlement-shaped gate that tr_budget owns. It
    is NOT used to mean "unlimited by accident": a declared flat budget whose
    served value is missing or unreadable falls back to the apps.yml floor,
    and only an explicit -1 means unlimited.
    """
    cfg = budget_config(apps_registry, app_id)
    if not cfg.get("enabled") or cfg.get("shape") != SHAPE_FLAT:
        return None

    floor = cfg.get("monthly_cost_limit_usd")
    served = None
    slug = cfg.get("config_slug")
    if slug:
        doc = (remote_configs or {}).get(slug)
        if isinstance(doc, dict):
            served = doc.get("monthly_cost_limit_usd")
        else:
            # States what it OBSERVED, not what it expects to happen next.
            # A log line that names the outcome is a line that can go on
            # asserting a fallback after someone removes the fallback.
            logger.warning(
                "app_budget_served_config_unreadable app=%s slug=%s", app_id, slug,
            )

    for candidate, source in ((served, "served"), (floor, "floor")):
        if candidate is None:
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            logger.error(
                "app_budget_bad_value app=%s source=%s value=%r; falling through",
                app_id, source, candidate,
            )
            continue
        resolved = None if value == -1 else value
        logger.debug(
            "app_budget_cap_resolved app=%s source=%s cap=%s",
            app_id, source, resolved,
        )
        return resolved

    logger.error(
        "app_budget_enabled_with_no_readable_cap app=%s; gate cannot run", app_id,
    )
    return None


def audit_uncapped_reachable_apps(
    remote_configs: dict | None,
    apps_registry: dict,
    apple_bundle_id: str | None,
) -> list[dict]:
    """Apps that have NO spend ceiling AND whose bundle id can now sign in.

    ⚠ This is the combination nobody should ever ship, and it is created by
    two SAFE-LOOKING changes made in the wrong order rather than by one bad
    one. Uncapping a tenant is safe while it is unreachable; adding a bundle
    id to the audience allowlist is safe while that tenant is capped. Do
    either second and you have unlimited spend PER SIGNUP.

    ⚠⚠ PER SIGNUP, not per app. `app_month_spend_usd` sums
    `WHERE user_id = ? AND app_id = ?`, so the cap is a ceiling for each
    person individually, never a shared pot. With one QA identity that reads
    as harness headroom. The day real users can authenticate it becomes an
    unlimited allowance for every one of them, and for an app with
    `own_account_meter` there is nothing behind it: the shared account meter
    it was taken off is not there to catch the overflow.

    Returns one entry per violating app. Empty is the healthy state.
    """
    allowed = {b.strip() for b in (apple_bundle_id or "").split(",") if b.strip()}
    if not allowed:
        return []

    found: list[dict] = []
    # The registry nests apps under an `apps` key; iterating the top level
    # walks {'apps': ...} and silently finds nothing. `app_entry` is the
    # accessor that knows this, and the first draft here did not use it.
    for app_id, entry in ((apps_registry or {}).get("apps") or {}).items():
        if not isinstance(entry, dict):
            continue
        bundle_id = entry.get("bundle_id")
        if not bundle_id or bundle_id not in allowed:
            continue
        cfg = budget_config(apps_registry, app_id)
        if not cfg.get("enabled") or cfg.get("shape") != SHAPE_FLAT:
            continue
        if flat_cap_usd(remote_configs, apps_registry, app_id) is not None:
            continue
        found.append({
            "app_id": app_id,
            "bundle_id": bundle_id,
            "own_account_meter": bool(cfg.get("own_account_meter")),
        })
    return found


async def report_uncapped_reachable(
    db,
    remote_configs: dict | None,
    apps_registry: dict,
    apple_bundle_id: str | None,
    *,
    from_addr: str = "alerts@noreply.invalid",
) -> list[dict]:
    """Run the audit and RAISE AN INCIDENT for anything it finds.

    ⚠ A log line is not an alert. The first version of this only logged at
    startup, which fails twice: nobody tails a log, and startup is the wrong
    moment. The bundle-id half requires a container recreate, so THAT order is
    caught by a restart — but the cap half is a served-config save that
    hot-reloads with NO restart. Add the bundle id first and set the cap to -1
    afterwards and a startup-only check never runs again. So this is also
    called from the admin config write, which is the other way in.

    Never raises: an alert must not fail the write that noticed the problem.
    """
    try:
        found = audit_uncapped_reachable_apps(
            remote_configs, apps_registry, apple_bundle_id)
        if not found:
            return []
        from app.services.alerting import report_incident
        for v in found:
            logger.error(
                "UNCAPPED_AND_REACHABLE app=%s bundle_id=%s own_account_meter=%s "
                "— no spend ceiling and its bundle id passes the audience "
                "check, so every signup has an unlimited allowance",
                v["app_id"], v["bundle_id"], v["own_account_meter"],
            )
            await report_incident(
                db,
                category="uncapped_and_reachable",
                subject=v["app_id"],
                details=v,
                from_addr=from_addr,
            )
        return found
    except Exception as e:  # noqa: BLE001 — never break the caller
        logger.warning("uncapped_reachable report failed (non-fatal): %s", e)
        return []


def refuse_uncapped_reachable(
    remote_configs: dict | None,
    apps_registry: dict,
    app_id: str | None,
    apple_bundle_id: str | None,
) -> dict | None:
    """This app's violation entry when it has NO ceiling AND its bundle id
    passes the audience check, else None. A caller holding an entry must
    REFUSE the call rather than serve it.

    ⚠ WHY REFUSE RATHER THAN SUBSTITUTE A CEILING. There is nothing to fall
    back to. The floor in apps.yml for the only app in this state is itself
    -1, deliberately, so a fallback to the floor resolves to unlimited and the
    guard reads as configured while gating nothing, which is the exact shape
    this file already warns about for entitlement-keying. A constant in code
    is worse: a number nobody chose, drifting from what the app is worth,
    which would one day meter a real user at whatever last year's guess was.
    Refusing states what has actually happened, which is that the
    configuration is in a state the operator said must never exist.

    ⚠ THIS DOES NOT OVERRIDE THE UNCAPPING. Scott's 2026-09-08 ruling is
    quoted in apps.yml and carries its own premise: "There's no chance of a
    production user using the N-400 lane so we need to open it up." This
    re-imposes a stop at the moment that premise stops holding, which is the
    condition the ruling was granted under rather than a reversal of it.

    ⚠ PREVENTION, NOT DETECTION, AND BOTH ARE WANTED. `report_uncapped_reachable`
    already raises an incident from boot and from the admin config write, so
    the pair is not silent today. An alert tells an operator to go and fix
    something; it does nothing about the requests served in the minutes before
    anyone reads it. This closes those minutes.

    The forbidden state is defined ONCE, in `audit_uncapped_reachable_apps`.
    This filters that to a single app rather than restating the rule, because
    two statements of one invariant drift and then disagree in the case
    nobody tested.
    """
    norm = (app_id or "").strip().lower()
    if not norm:
        return None
    for violation in audit_uncapped_reachable_apps(
            remote_configs, apps_registry, apple_bundle_id):
        if violation["app_id"] == norm:
            return violation
    return None


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0,
                       microsecond=0).isoformat()


def month_reset_iso() -> str:
    """First instant of the next UTC month, ISO. Machine-readable on purpose:
    formatting a date is the client's job and we do not know their locale."""
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc).isoformat()


async def app_month_spend_usd(db: aiosqlite.Connection, user_id: str,
                              app_id: str) -> float:
    """This user's realized spend in THIS app this UTC month."""
    cur = await db.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_log "
        "WHERE user_id = ? AND app_id = ? AND request_timestamp >= ?",
        (user_id, app_id, _month_start_iso()),
    )
    row = await cur.fetchone()
    return float(row[0] or 0.0) if row else 0.0


async def would_exceed_flat_budget(
    db: aiosqlite.Connection, user_id: str, app_id: str,
    estimate_usd: float | None, cap_usd: float | None,
) -> tuple[bool, dict]:
    """(block, info) for one call against this app's flat cap.

    The marginal estimate is allowed to be None, because pricing is not
    always resolvable for a model we have just started serving. When it is,
    the already-over-cap check still fires: an unpriceable call cannot let a
    user who is ALREADY past the ceiling through, it can only let one final
    call cross it.
    """
    info = {"app_id": app_id, "cap": cap_usd, "spent": 0.0, "estimate": estimate_usd}
    if cap_usd is None:
        return False, info

    spent = await app_month_spend_usd(db, user_id, app_id)
    info["spent"] = spent
    if spent >= cap_usd:
        return True, info
    if estimate_usd is not None and (spent + estimate_usd) > cap_usd:
        return True, info
    return False, info


# `text` is a LOCALE MAP here, not a string. Tech Rehearsal's equivalent
# copy is a bare string, and N-400's client pins the map shape in its own
# decode tests, so the fallback has to be the shape the only app using this
# module actually reads. A fallback in the wrong shape is worse than none:
# it turns a missing-config warning into a decode failure on the device.
_FALLBACK_COPY = {
    "kind": "budget_exhausted",
    "text": {
        "en": "You have used this month's allowance for this app.",
        "es": "Ha usado su asignación de este mes para esta aplicación.",
        "pt": "Você usou sua cota deste mês para este aplicativo.",
    },
    "action": None,
}


def _has_text(copy: dict) -> bool:
    """True when this copy block would actually say something.

    Accepts both a bare string and a locale map, because the two apps that
    stop a user disagree about which one they read, and a shape check that
    only knew one of them would call the other empty and silently replace
    real served copy with the fallback.
    """
    text = copy.get("text")
    if isinstance(text, str):
        return bool(text.strip())
    if isinstance(text, dict):
        return any(isinstance(v, str) and v.strip() for v in text.values())
    return False


def exhausted_copy(remote_configs: dict | None, apps_registry: dict,
                   app_id: str | None) -> dict:
    """Served copy for the stop, or a sentence rather than silence.

    A rejection with no reason reads as the app being broken, which is worse
    than the rejection. So this never returns empty.
    """
    slug = budget_config(apps_registry, app_id).get("config_slug")
    doc = (remote_configs or {}).get(slug) if slug else None
    if isinstance(doc, dict):
        copy = doc.get("exhausted")
        if isinstance(copy, dict) and _has_text(copy):
            return copy
    logger.warning("app_budget_exhausted_copy_missing app=%s slug=%s", app_id, slug)
    return dict(_FALLBACK_COPY)


# --- counters that live on the shared ACCOUNT row --------------------------
#
# `users` is one row per account and SIWA issues subject ids per developer
# TEAM, so these counters serve all three apps at once. Scott's multitenancy
# ruling (2026-09-02) says that is exactly what the apps must not do.
#
# MEASURED the same day: both leaks are LATENT. artifact_generation is
# ShoulderSurf's alone (6 calls ever) and no other app produces capture
# traffic, so nothing is sharing these today. That is the argument FOR
# guarding them: a latent leak gives no signal on the day it stops being
# latent, and the second app's first generation would simply spend the
# first app's allowance behind a 200.
#
# The permanent fix is a per-app counter table. Deliberately not built:
# these two use DIFFERENT period models (memory carries its own
# `memory_period`, generations rides the allocation cycle), so reconciling
# them is a migration with a backfill rather than a guard.
SHARED_ACCOUNT_COUNTERS = {
    "memory_used_this_period": "shouldersurf",
    "generations_used": "shouldersurf",
}


def may_charge_shared_counter(counter: str, app_id: str | None) -> bool:
    """May this app decrement a counter that lives on the shared account row?

    False for an app that does not own the lane. Refusing to charge is the
    safe direction: sharing is the bug, so the failure mode should be "this
    app got no decrement" rather than "this app spent another app's
    allowance".

    Identity is resolved through `resolve_app_dir` rather than compared as a
    raw string, so this agrees with config resolution instead of
    reimplementing it. That matters more than it looks: a missing header
    reaches different call sites as None and as the LITERAL STRING
    "unknown", and the first version of this compared strings and refused
    the second one. Two real tests caught it, both for users whose requests
    carry no app id at all, which is most of the older field builds.

    So absent, blank, "unknown" and unrecognised all resolve to the default
    app and CHARGE, failing open to today's behaviour exactly as
    resolve_app_dir does. Only an app that resolves to a DIFFERENT
    registered dir is refused.

    An unregistered counter name charges too, so this can never become a
    silent gate on something nobody put in the map.
    """
    owner = SHARED_ACCOUNT_COUNTERS.get(counter)
    if owner is None:
        return True
    from app.routers.config import resolve_app_dir
    return resolve_app_dir(app_id) == resolve_app_dir(owner)
