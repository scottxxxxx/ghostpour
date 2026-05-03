"""Marketing email opt-in state + unsubscribe-link tokens.

Two roles:
1. CRUD on `users.marketing_opt_in` (with timestamped audit of source).
2. HMAC-signed token generation/verification for the public
   `/unsubscribe?token=...` link in marketing emails. The link must
   work without auth (the email client opens it), so the token has to
   self-identify the user AND be unforgeable. We sign with the same
   `JWT_SECRET` the rest of the app trusts.

Token format: `<base64url(user_id)>.<base64url(hmac_sha256)>`
- No expiry — old emails should still unsubscribe correctly.
- Domain-separated by the literal prefix `unsubscribe:` so the same
  secret can't be reused to forge other token types we may add later.
- `compare_digest` for constant-time comparison.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
from datetime import datetime, timezone

import aiosqlite

_TOKEN_PURPOSE = b"unsubscribe:"
SOURCE_IOS = "ios_toggle"
SOURCE_UNSUBSCRIBE_LINK = "unsubscribe_link"
SOURCE_SPAM_COMPLAINT = "spam_complaint"
SOURCE_ADMIN = "admin"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def generate_unsubscribe_token(user_id: str, secret: str) -> str:
    if not user_id or not secret:
        raise ValueError("user_id and secret are required")
    uid_b64 = _b64url_encode(user_id.encode("utf-8"))
    sig = hmac.new(
        secret.encode("utf-8"),
        _TOKEN_PURPOSE + user_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{uid_b64}.{_b64url_encode(sig)}"


def verify_unsubscribe_token(token: str, secret: str) -> str | None:
    """Return the user_id encoded in the token if the signature checks
    out, else None. Constant-time comparison."""
    if not token or "." not in token:
        return None
    uid_b64, sig_b64 = token.split(".", 1)
    try:
        user_id = _b64url_decode(uid_b64).decode("utf-8")
        provided_sig = _b64url_decode(sig_b64)
    except (UnicodeDecodeError, ValueError, base64.binascii.Error):
        return None
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        _TOKEN_PURPOSE + user_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None
    return user_id


async def get_marketing_opt_in(
    db: aiosqlite.Connection, user_id: str
) -> dict[str, object]:
    cursor = await db.execute(
        "SELECT marketing_opt_in, marketing_opt_in_updated_at, marketing_opt_in_source"
        " FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"opt_in": False, "updated_at": None, "source": None}
    return {
        "opt_in": bool(row["marketing_opt_in"]),
        "updated_at": row["marketing_opt_in_updated_at"],
        "source": row["marketing_opt_in_source"],
    }


async def set_marketing_opt_in(
    db: aiosqlite.Connection,
    user_id: str,
    *,
    opt_in: bool,
    source: str,
) -> bool:
    """Set the user's marketing opt-in state. Returns True if the value
    changed (so callers can decide whether to push to the email
    provider's audience), False if it was already at the requested
    value."""
    cursor = await db.execute(
        "SELECT marketing_opt_in FROM users WHERE id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    new_value = 1 if opt_in else 0
    if int(row["marketing_opt_in"]) == new_value:
        return False
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE users SET marketing_opt_in = ?,"
        " marketing_opt_in_updated_at = ?,"
        " marketing_opt_in_source = ?"
        " WHERE id = ?",
        (new_value, now, source, user_id),
    )
    await db.commit()
    return True


async def opt_out_by_recipient(
    db: aiosqlite.Connection, recipient: str, source: str
) -> bool:
    """Look up the user by email (case-insensitive) and flip them off.
    Used by the spam-complaint webhook handler. Returns True if a user
    was matched and updated, False otherwise."""
    if not recipient:
        return False
    cursor = await db.execute(
        "SELECT id FROM users WHERE LOWER(email) = LOWER(?) LIMIT 1",
        (recipient.strip(),),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    return await set_marketing_opt_in(
        db, row["id"], opt_in=False, source=source,
    )
