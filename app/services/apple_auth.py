import jwt
from jwt import PyJWKClient

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


class AppleAuthVerifier:
    def __init__(self, bundle_id: str):
        self.bundle_id = bundle_id
        self._jwks_client = PyJWKClient(
            APPLE_JWKS_URL, cache_jwk_set=True, lifespan=86400
        )

    def verify_identity_token(self, token: str) -> dict:
        """Verify an Apple identity token and return its claims.

        Returns dict with 'sub', 'email', 'email_verified', etc.
        Raises jwt.exceptions.* on invalid/expired tokens.
        """
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.bundle_id,
            issuer=APPLE_ISSUER,
        )
        return claims
