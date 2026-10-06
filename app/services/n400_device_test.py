"""Scott's on-device N-400 test (2026-10-05): while it runs, the N-400 lane
answers ONLY his phone. Scott: "I do not want you to accept any other n-400
tests, just the one I run from my device."

His phone and the auditor's harness sign in as the SAME account (408a4694),
so the account cannot tell them apart. The User-Agent can: the app sends
URLSession's default "N-400%20Helper/<build> CFNetwork/...", the harness
"Python-urllib/3.x", the simulator UI tests "xctest/...", read from the
proxy access log on 2026-10-05. A User-Agent is trivially forged, so this
stops teammates' test runs, not an adversary; nobody else can reach the
lane anyway (com.weirtech.n400helper is absent from CZ_APPLE_BUNDLE_ID).

The same switch raises the test account's hourly latch for the run, because
a full interview is 90 to 130 turns and the latch trips at 100 in an hour.
With every other caller refused, the only calls counted are his.

Both dials live in the served `n400/budget` doc under `device_test`, so they
move with a scoped config PUT, not a deploy:

    "device_test": {"only_device": true, "user_agent_app_name": "N-400 Helper",
                    "hourly_cap": 400}
"""
from __future__ import annotations

import logging
from urllib.parse import unquote

from fastapi import HTTPException

logger = logging.getLogger(__name__)

APP_ID = "n400"
CONFIG_SLUG = "n400/budget"


def _settings(remote_configs: dict | None) -> dict:
    doc = (remote_configs or {}).get(CONFIG_SLUG)
    dt = doc.get("device_test") if isinstance(doc, dict) else None
    return dt if isinstance(dt, dict) else {}


def active(remote_configs: dict | None, app_id: str | None) -> bool:
    return app_id == APP_ID and _settings(remote_configs).get("only_device") is True


def is_device(user_agent: str | None, app_name: str) -> bool:
    """True when the UA leads with "<app_name>/", percent-encoded or not."""
    return bool(user_agent) and unquote(user_agent).startswith(f"{app_name}/")


def enforce(remote_configs: dict | None, app_id: str | None, user_agent: str | None) -> None:
    """403 every N-400 call that is not the phone app while the test is on."""
    if not active(remote_configs, app_id):
        return
    name = str(_settings(remote_configs).get("user_agent_app_name") or "N-400 Helper")
    if is_device(user_agent, name):
        return
    logger.warning("n400_device_test_refused user_agent=%r", (user_agent or "")[:80])
    raise HTTPException(status_code=403, detail={
        "code": "n400_device_test_only",
        "message": "The N-400 lane is reserved for an on-device test right now."})


def hourly_cap(remote_configs: dict | None, app_id: str | None) -> int | None:
    """The raised latch for the test, or None to keep the default."""
    if not active(remote_configs, app_id):
        return None
    try:
        cap = int(_settings(remote_configs).get("hourly_cap"))
    except (TypeError, ValueError):
        return None
    return cap if cap > 0 else None
