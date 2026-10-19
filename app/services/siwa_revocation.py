"""Sign in with Apple token revocation (account deletion, 5.1.1(v)).

Apple requires apps using SIWA to revoke the user's tokens when the
account is deleted. GP's /auth/apple only ever sees identity tokens
(public-key verification), so revocation rides a fresh
`authorization_code` the client obtains from Apple's re-auth sheet and
sends with the delete request: exchange it at /auth/token for a refresh
token, then POST /auth/revoke.

DORMANT until the SIWA key is provisioned (same pattern as the App
Store Server / Connect keys): `is_configured()` is False while the
`siwa_*` settings are blank, and the delete endpoint skips revocation.
The key is a dedicated "Sign in with Apple" .p8 from App Store Connect
(Keys -> create with the Sign in with Apple capability), NOT the ASC
Connect team key: the client secret it signs carries different claims
(iss=team id, sub=app bundle id, aud=appleid.apple.com).
"""

import base64
import logging
import time

import httpx
import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

_APPLE_AUTH = "https://appleid.apple.com"


def _key(app_id: str | None = None) -> tuple[str, str]:
    """(key id, base64 .p8) of the SIWA key configured for this app's App
    ID. Tech Rehearsal has its own; every other app uses the ShoulderSurf
    key, and TR never falls back to it, because a secret signed with SS's
    key is refused for TR's App ID and would fail at Apple every time."""
    s = get_settings()
    if app_id == "techrehearsal":
        return s.tr_siwa_key_id, s.tr_siwa_private_key_b64
    return s.siwa_key_id, s.siwa_private_key_b64


def is_configured(app_id: str | None = None) -> bool:
    s = get_settings()
    key_id, key = _key(app_id)
    return bool(s.siwa_team_id and key_id and key)


def _client_id(app_id: str | None = None) -> str:
    """The bundle id Apple issued the authorization code to. A code is
    redeemable only by the client it was issued for, so a Tech Rehearsal
    delete must exchange and revoke as Tech Rehearsal (2026-10-18: every
    revocation went out as ShoulderSurf, the first CZ_APPLE_BUNDLE_ID entry,
    and Apple refused TR's). The deleting app's bundle_id in apps.yml, else
    the old default."""
    if app_id:
        from app.routers.config import load_apps
        bundle = ((load_apps().get("apps") or {}).get(app_id) or {}).get("bundle_id")
        if bundle:
            return str(bundle)
    s = get_settings()
    return s.app_store_bundle_id or s.apple_bundle_id.split(",")[0].strip()


def _client_secret(client_id: str, app_id: str | None = None) -> str:
    s = get_settings()
    key_id, key = _key(app_id)
    now = int(time.time())
    return jwt.encode(
        {"iss": s.siwa_team_id, "iat": now, "exp": now + 600,
         "aud": _APPLE_AUTH, "sub": client_id},
        base64.b64decode(key.strip()),
        algorithm="ES256",
        headers={"kid": key_id},
    )


async def revoke_with_authorization_code(authorization_code: str,
                                         app_id: str | None = None) -> bool:
    """Exchange the code for a refresh token and revoke it.

    Best-effort: returns True when Apple accepted the revocation, False
    on any failure (logged). Account deletion never blocks on this —
    the data purge is the user-facing contract; revocation is Apple
    hygiene we retry-by-design on the next fresh sign-in + delete.
    """
    client_id = _client_id(app_id)
    secret = _client_secret(client_id, app_id)
    async with httpx.AsyncClient(base_url=_APPLE_AUTH, timeout=15) as client:
        try:
            token_resp = await client.post("/auth/token", data={
                "client_id": client_id,
                "client_secret": secret,
                "grant_type": "authorization_code",
                "code": authorization_code,
            })
            token_resp.raise_for_status()
            refresh_token = token_resp.json().get("refresh_token")
            if not refresh_token:
                logger.warning("siwa_revoke: token exchange returned no refresh_token")
                return False
            revoke_resp = await client.post("/auth/revoke", data={
                "client_id": client_id,
                "client_secret": secret,
                "token": refresh_token,
                "token_type_hint": "refresh_token",
            })
            revoke_resp.raise_for_status()
            logger.info("siwa_revoke: token revoked client_id=%s", client_id)
            return True
        except Exception as e:
            logger.warning("siwa_revoke: failed client_id=%s: %s", client_id, e)
            return False
