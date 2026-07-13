"""Staging store for generated documents (phase 2a, design:
docs/design/documents-phase2-returned-files.md §4).

NOT a file store: SS downloads the artifact the moment the response lands
and persists it client-side (meeting record / save-as-Reference). GP holds
bytes only for the fetch window — 6h expiry, 50MB live cap per user, purge
sweep at startup and hourly. The serve endpoint authenticates and checks
ownership; a purged or expired id is a plain 404 (the client's copy is the
durable one, so a dead staging entry costs nothing).
"""

from __future__ import annotations

import logging
import os
import time
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("ghostpour.generated_files")

STAGING_DIR = Path(os.environ.get("CZ_DATA_DIR", "data")) / "generated_files"
EXPIRY_HOURS = 6
PER_USER_LIVE_CAP_BYTES = 50 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def live_bytes_for_user(db: aiosqlite.Connection, user_id: str) -> int:
    row = await (await db.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM generated_files "
        "WHERE user_id = ? AND expires_at > ?",
        (user_id, _now().isoformat()),
    )).fetchone()
    return int(row["n"] if row else 0)


async def stage(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    app_id: str | None,
    name: str,
    media_type: str,
    content: bytes,
) -> dict | None:
    """Write one artifact into staging. Returns the row dict for the wire
    payload, or None when the per-user live cap would be exceeded (the
    generation's text answer still returns — files are best-effort)."""
    if await live_bytes_for_user(db, user_id) + len(content) > PER_USER_LIVE_CAP_BYTES:
        logger.warning("generated_files: user %s over live cap — dropping %r", user_id[:8], name)
        return None

    fid = "gpf_" + uuid.uuid4().hex
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / fid
    path.write_bytes(content)

    created = _now()
    expires = created + timedelta(hours=EXPIRY_HOURS)
    await db.execute(
        """INSERT INTO generated_files
           (id, user_id, app_id, name, media_type, size_bytes, storage_path, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fid, user_id, app_id, name, media_type, len(content), str(path),
         created.isoformat(), expires.isoformat()),
    )
    await db.commit()
    return {
        "file_id": fid,
        "name": name,
        "media_type": media_type,
        "size_bytes": len(content),
        # SS renders the transcript card from this entry while the bytes
        # download behind it; sha256 lets the client verify the download
        # against what was staged (SS ask, 2026-07-11).
        "sha256": hashlib.sha256(content).hexdigest(),
        "url": f"/v1/generated-files/{fid}",
        "expires_at": expires.isoformat(),
    }


async def fetch(db: aiosqlite.Connection, file_id: str, user_id: str) -> dict | None:
    """Row for a live, owned staging entry — None for missing, expired, or
    someone else's file (all indistinguishable 404s at the endpoint)."""
    row = await (await db.execute(
        "SELECT * FROM generated_files WHERE id = ? AND user_id = ? AND expires_at > ?",
        (file_id, user_id, _now().isoformat()),
    )).fetchone()
    return dict(row) if row else None


async def purge_expired(db: aiosqlite.Connection) -> int:
    """Delete expired rows and their bytes. Called at startup and hourly."""
    rows = await (await db.execute(
        "SELECT id, storage_path FROM generated_files WHERE expires_at <= ?",
        (_now().isoformat(),),
    )).fetchall()
    for r in rows:
        try:
            Path(r["storage_path"]).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("generated_files purge: could not delete %s: %s", r["storage_path"], e)
    if rows:
        await db.execute(
            f"DELETE FROM generated_files WHERE id IN ({','.join('?' * len(rows))})",
            [r["id"] for r in rows],
        )
        await db.commit()
        logger.info("generated_files: purged %d expired artifact(s)", len(rows))
    return len(rows)
