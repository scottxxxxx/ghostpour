"""Push to Apple, for the one case a poll cannot reach.

SS shipped a background-poll fallback for a file build the user
backgrounds away from, but iOS cancels background work when the user
force-quits from the app switcher and will not relaunch the app until
they open it themselves. A push is the only thing that reaches that
phone, and it is better in the covered case too: it fires when the build
finishes rather than on the next rung of a ladder.

DORMANT WHILE THE KEY IS BLANK, the same shape as the three other Apple
keys in app/config.py: no key, no sends, no errors, and the registration
routes keep storing tokens so the day the key lands every phone is
already known.

⚠ APNs is HTTP/2 ONLY, so `h2` is pinned in requirements.txt. It was
installed in the local venv and ABSENT from the prod image when this was
written, which is the shape that passes every local test and fails on
the first real send.
"""

from __future__ import annotations

import json
import logging
import time
from base64 import b64decode

import aiosqlite
import httpx
import jwt as pyjwt

from app.services import device_tokens

logger = logging.getLogger("ghostpour.apns")

HOSTS = {"production": "https://api.push.apple.com",
         "sandbox": "https://api.sandbox.push.apple.com"}
# Apple wants a provider token reused, not minted per push (re-minting
# faster than once per 20 minutes is rate limited), and refused once it is
# an hour old.
_TOKEN_TTL_SECONDS = 45 * 60
_cached: dict[str, tuple[str, float]] = {}

TITLE_LOC_KEY = "Your file is ready"
BODY_LOC_KEY = "Tap to open the chat where you asked for it."


def configured(settings) -> bool:
    """True when Scott has provisioned the key. Everything else no-ops."""
    return bool(getattr(settings, "apns_private_key_b64", "")
                and getattr(settings, "apns_key_id", "")
                and getattr(settings, "apns_team_id", ""))


def provider_token(settings) -> str:
    """ES256 JWT for the provider connection, cached under the key id."""
    key_id = settings.apns_key_id
    hit = _cached.get(key_id)
    now = time.time()
    if hit and now - hit[1] < _TOKEN_TTL_SECONDS:
        return hit[0]
    private_key = b64decode(settings.apns_private_key_b64).decode("utf-8")
    token = pyjwt.encode(
        {"iss": settings.apns_team_id, "iat": int(now)},
        private_key, algorithm="ES256", headers={"kid": key_id},
    )
    _cached[key_id] = (token, now)
    return token


def build_payload(*, generation_id: str, project_id: str | None,
                  meeting_id: str | None, session_id: str | None,
                  file_name: str | None) -> dict:
    """The alert plus the routing keys the tap handler reads.

    `title-loc-key` and `loc-key` are the exact English strings in SS's
    String Catalog, so iOS looks them up in the app bundle and renders
    them in the user's language: no locale column here and no drift when
    they reword. A FILE NAME is not translatable, so it rides as a plain
    `body` instead of the loc-key when there is one.
    """
    alert: dict = {"title-loc-key": TITLE_LOC_KEY}
    if file_name:
        alert["body"] = file_name
    else:
        alert["loc-key"] = BODY_LOC_KEY
    payload: dict = {"aps": {"alert": alert, "sound": "default"},
                     "generation_id": generation_id}
    for key, value in (("project_id", project_id), ("meeting_id", meeting_id),
                       ("session_id", session_id)):
        if value:
            payload[key] = value
    return payload


async def _post(client: httpx.AsyncClient, *, host: str, token: str, headers: dict,
                payload: dict) -> httpx.Response:
    return await client.post(f"{host}/3/device/{token}", headers=headers,
                             content=json.dumps(payload).encode("utf-8"))


async def send_to_token(client: httpx.AsyncClient, db: aiosqlite.Connection, *,
                        row: dict, settings, payload: dict, expiration: int,
                        collapse_id: str) -> str:
    """One push. Returns what happened, for the caller's log line.

    Apple's two answers that mean something about the ROW are handled
    here: 410 Unregistered retires the token, and 400 BadDeviceToken is
    retried once against the other host because a phone that moves
    between an Xcode install and TestFlight gets a different token in
    each and the stale one is otherwise indistinguishable.
    """
    headers = {
        "authorization": f"bearer {provider_token(settings)}",
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-topic": row["bundle_id"],
        "apns-collapse-id": collapse_id[:64],
        "apns-expiration": str(expiration),
    }
    env = (row.get("environment") or "production").lower()
    host = HOSTS.get(env, HOSTS["production"])
    resp = await _post(client, host=host, token=row["device_token"],
                       headers=headers, payload=payload)
    # One line per attempt, because "Apple accepted it and iOS dropped it"
    # and "we never got that far" are indistinguishable from the device.
    # `apns-id` is the receipt: Apple returns it when it has taken the
    # notification, so its presence moves the investigation off our side.
    # The token is truncated; it identifies a device and the prefix is
    # enough to correlate rows.
    logger.info(
        "apns_send host=%s env=%s topic=%s collapse_id=%s token=%s… status=%s apns_id=%s",
        host.replace("https://", ""), env, headers["apns-topic"],
        headers["apns-collapse-id"], str(row["device_token"])[:8],
        resp.status_code, getattr(resp, "headers", {}).get("apns-id", "-"),
    )
    if resp.status_code == 200:
        return "sent"
    reason = ""
    try:
        reason = (resp.json() or {}).get("reason", "")
    except Exception:  # noqa: BLE001 — Apple sends an empty body on some 200s
        reason = ""
    if resp.status_code == 410 or reason == "Unregistered":
        await device_tokens.forget(db, row["device_token"])
        return "unregistered"
    if resp.status_code == 400 and reason == "BadDeviceToken":
        other = device_tokens.other_environment(env)
        retry = await _post(client, host=HOSTS[other], token=row["device_token"],
                            headers=headers, payload=payload)
        logger.warning(
            "apns_send_retry host=%s from_env=%s to_env=%s collapse_id=%s "
            "status=%s apns_id=%s — a retry here means the row's environment "
            "was wrong, NOT a routine correction",
            HOSTS[other].replace("https://", ""), env, other,
            headers["apns-collapse-id"], retry.status_code,
            getattr(retry, "headers", {}).get("apns-id", "-"),
        )
        if retry.status_code == 200:
            await device_tokens.set_environment(db, row["device_token"], other)
            return f"sent_after_environment_correction_to_{other}"
        if retry.status_code == 410:
            await device_tokens.forget(db, row["device_token"])
            return "unregistered"
        return f"bad_device_token_both_environments_{retry.status_code}"
    return f"error_{resp.status_code}_{reason or 'no_reason'}"


async def notify_generation_done(db: aiosqlite.Connection, *, settings, user_id: str,
                                 generation_id: str) -> dict:
    """Push "your file is ready" to this user's phones. Never raises.

    The acked_at check is re-read from the row HERE, at send time, not
    when this was scheduled: the client can ack in the seconds between
    the file landing and this running, and a push after the user has
    already seen the file is noise.
    """
    outcome: dict = {"sent": 0, "skipped": None, "results": []}
    if not configured(settings):
        outcome["skipped"] = "apns_not_configured"
        return outcome
    row = await (await db.execute(
        "SELECT status, acked_at, expires_at, files_json, project_id, meeting_id, session_id "
        "FROM generations WHERE generation_id = ? AND user_id = ?",
        (generation_id, user_id),
    )).fetchone()
    if row is None or row["status"] != "done":
        outcome["skipped"] = "not_a_done_row"
        return outcome
    if row["acked_at"]:
        outcome["skipped"] = "already_acked"
        return outcome
    tokens = await device_tokens.for_user(db, user_id)
    if not tokens:
        outcome["skipped"] = "no_registered_devices"
        return outcome
    try:
        files = json.loads(row["files_json"] or "[]")
    except (TypeError, ValueError):
        files = []
    payload = build_payload(
        generation_id=generation_id, project_id=row["project_id"],
        meeting_id=row["meeting_id"], session_id=row["session_id"],
        file_name=(files[0].get("name") if files and isinstance(files[0], dict) else None),
    )
    from datetime import datetime
    try:
        expiration = int(datetime.fromisoformat(row["expires_at"]).timestamp())
    except (TypeError, ValueError):
        expiration = int(time.time()) + 3600
    async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
        for t in tokens:
            try:
                result = await send_to_token(client, db, row=t, settings=settings,
                                             payload=payload, expiration=expiration,
                                             collapse_id=generation_id)
            except Exception as e:  # noqa: BLE001 — a push must never fail a turn
                result = f"exception_{type(e).__name__}"
                logger.warning("apns: send failed for one token: %s", e)
            outcome["results"].append(result)
            if result.startswith("sent"):
                outcome["sent"] += 1
    logger.info("apns_generation_done generation_id=%s devices=%d sent=%d results=%s",
                generation_id, len(tokens), outcome["sent"], ",".join(outcome["results"]))
    return outcome
