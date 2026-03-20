import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt


class JWTService:
    def __init__(
        self,
        secret: str,
        algorithm: str = "HS256",
        access_expire_minutes: int = 60,
        refresh_expire_days: int = 30,
    ):
        self.secret = secret
        self.algorithm = algorithm
        self.access_expire = timedelta(minutes=access_expire_minutes)
        self.refresh_expire = timedelta(days=refresh_expire_days)

    def create_access_token(self, user_id: str) -> str:
        """Create a JWT. Tier is NOT encoded — always read from DB."""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + self.access_expire,
            "type": "access",
        }
        return pyjwt.encode(payload, self.secret, algorithm=self.algorithm)

    def create_refresh_token(self) -> tuple[str, str, datetime]:
        """Returns (raw_token, token_hash, expires_at)."""
        raw = uuid.uuid4().hex + uuid.uuid4().hex
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + self.refresh_expire
        return raw, hashed, expires_at

    def verify_access_token(self, token: str) -> dict:
        """Verify and decode an access token. Raises on invalid/expired."""
        return pyjwt.decode(token, self.secret, algorithms=[self.algorithm])

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
