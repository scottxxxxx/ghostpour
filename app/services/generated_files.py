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
        # Discovery keeps done files for 7 days, so a busy week can reach the
        # cap. Files the client has already ACKED (presented and ingested)
        # go first, oldest first; only when nothing acked is left is the
        # new file refused.
        await purge_oldest_acked_for_user(
            db, user_id, len(content) - (PER_USER_LIVE_CAP_BYTES - await live_bytes_for_user(db, user_id)))
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


async def extend_expiry(db: aiosqlite.Connection, file_ids: list[str], expires_at: str) -> int:
    """Move staged files onto their generation's clock (7 days for done)."""
    if not file_ids:
        return 0
    cur = await db.execute(
        f"UPDATE generated_files SET expires_at = ? WHERE id IN ({','.join('?' * len(file_ids))})",
        [expires_at, *file_ids],
    )
    await db.commit()
    return cur.rowcount or 0


async def acked_file_ids_for_user(db: aiosqlite.Connection, user_id: str) -> list[tuple[str, str]]:
    """(file_id, acked_at) for every file of an acked generation, oldest ack first."""
    import json as _json
    try:
        rows = await (await db.execute(
            "SELECT files_json, acked_at FROM generations WHERE user_id = ? AND acked_at IS NOT NULL "
            "ORDER BY acked_at ASC", (user_id,),
        )).fetchall()
    except Exception as e:  # noqa: BLE001
        # A database without the generations table (a test's synthetic
        # schema) has nothing acked. On prod the table always exists, so
        # this is logged rather than swallowed silently.
        logger.warning("generated_files: could not read acked generations for %s: %s", user_id[:8], e)
        return []
    out = []
    for r in rows:
        for f in _json.loads(r["files_json"] or "[]"):
            if f.get("file_id"):
                out.append((f["file_id"], r["acked_at"]))
    return out


async def purge_oldest_acked_for_user(db: aiosqlite.Connection, user_id: str, needed_bytes: int) -> int:
    """Free at least `needed_bytes` by deleting this user's acked files,
    oldest ack first. Returns bytes freed. Never touches an unacked file."""
    freed = 0
    if needed_bytes <= 0:
        return 0
    for fid, _ in await acked_file_ids_for_user(db, user_id):
        row = await (await db.execute(
            "SELECT id, size_bytes, storage_path FROM generated_files WHERE id = ? AND user_id = ?",
            (fid, user_id))).fetchone()
        if not row:
            continue
        try:
            Path(row["storage_path"]).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("generated_files: could not delete %s: %s", row["storage_path"], e)
        await db.execute("DELETE FROM generated_files WHERE id = ?", (row["id"],))
        freed += int(row["size_bytes"] or 0)
        if freed >= needed_bytes:
            break
    await db.commit()
    if freed:
        logger.info("generated_files: freed %d bytes of acked files for %s", freed, user_id[:8])
    return freed
