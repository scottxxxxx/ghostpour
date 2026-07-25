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


def is_configured() -> bool:
    s = get_settings()
    return bool(s.siwa_team_id and s.siwa_key_id and s.siwa_private_key_b64)


def _client_id() -> str:
    s = get_settings()
    return s.app_store_bundle_id or s.apple_bundle_id.split(",")[0].strip()


def _client_secret() -> str:
    s = get_settings()
    now = int(time.time())
    return jwt.encode(
        {"iss": s.siwa_team_id, "iat": now, "exp": now + 600,
         "aud": _APPLE_AUTH, "sub": _client_id()},
        base64.b64decode(s.siwa_private_key_b64.strip()),
        algorithm="ES256",
        headers={"kid": s.siwa_key_id},
    )


async def revoke_with_authorization_code(authorization_code: str) -> bool:
    """Exchange the code for a refresh token and revoke it.

    Best-effort: returns True when Apple accepted the revocation, False
    on any failure (logged). Account deletion never blocks on this —
    the data purge is the user-facing contract; revocation is Apple
    hygiene we retry-by-design on the next fresh sign-in + delete.
    """
    secret = _client_secret()
    async with httpx.AsyncClient(base_url=_APPLE_AUTH, timeout=15) as client:
        try:
            token_resp = await client.post("/auth/token", data={
                "client_id": _client_id(),
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
                "client_id": _client_id(),
                "client_secret": secret,
                "token": refresh_token,
                "token_type_hint": "refresh_token",
            })
            revoke_resp.raise_for_status()
            logger.info("siwa_revoke: token revoked")
            return True
        except Exception as e:
            logger.warning("siwa_revoke: failed: %s", e)
            return False
