"""What a finished meeting is entitled to, read from served config.

Three bands, all of them ours and all of them tunable without a deploy:

    >= report_min_seconds            report, generated automatically
    request_min_seconds .. that      no automatic report; the user may ASK,
                                     when allow_request_below_minimum
    <  request_min_seconds           nothing, and no offer to make one

The client reads these to decide what to offer. The server reads the SAME
document to decide what to serve, so moving a number in the dashboard moves
both at once. That is the point: before this, the threshold was a client
constant, and a demo meeting captured 298 seconds against a 300 second bar
and silently produced nothing (2026-07-31).
"""

from __future__ import annotations

from app.routers.config import candidate_slugs, resolve_app_dir

_DEFAULTS = {
    "report_min_seconds": 300,
    "allow_request_below_minimum": True,
    "request_min_seconds": 30,
}


def post_session_policy(remote_configs: dict, app_id: str | None = None) -> dict:
    """The post_session block the given app is actually served.

    Resolved through the same candidate order as GET /v1/config/{name}, so
    the policy we enforce is the policy that app reads. Base config only:
    a localized variant may change copy, never a gate.
    """
    for cand in candidate_slugs(resolve_app_dir(app_id), "client-config"):
        cfg = remote_configs.get(cand)
        if cfg is not None:
            return {**_DEFAULTS, **(cfg.get("post_session") or {})}
    return dict(_DEFAULTS)


def report_floor_seconds(remote_configs: dict, app_id: str | None) -> int:
    """Seconds below which a report request is refused. 0 means do not enforce.

    Apps other than the default get a floor ONLY when their own per-app config
    declares one. They inherit the flat file for everything else, but a new
    rejection path is not something to inherit: Tech Rehearsal has used this
    route, sends no duration telemetry we could have calibrated against, and
    its sessions are rehearsals rather than meetings. A floor measured on
    ShoulderSurf meetings has no business refusing a TR request.
    """
    app_dir = resolve_app_dir(app_id)
    if app_dir == resolve_app_dir(None):
        block = post_session_policy(remote_configs, app_id)
    else:
        own = remote_configs.get(f"{app_dir}/client-config") or {}
        block = own.get("post_session") or {}
    try:
        return max(0, int(block.get("request_min_seconds") or 0))
    except (TypeError, ValueError):
        # a bad value must not become an outage: serve the report
        return 0
