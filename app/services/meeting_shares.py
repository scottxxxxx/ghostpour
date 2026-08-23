"""Meeting share (iMessage), GP half: storage, tokens, dials, purge.

Scoped docs/design/meeting-share-scoping.md, ruled 2026-08-21: the payload
is SS's existing `.shouldersurf` archive (bytes, stored as uploaded, served
back by share id); a recipient without the app reads the whole meeting on
the hosted page (Variant A); creation is free on every tier; the host is
share.shouldersurf.com. Nothing here touches Context Quilt: the shared
object is SS's meeting record, never quilt memory, and a test pins that.

Storage: one row in `meeting_shares`, bytes on the data volume beside
generated_files. Token: 128 random bits, base64url, carries no user id,
and is a credential (it is the URL), so it is never logged here; routes
log share_id only.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("ghostpour.meeting_shares")

SHARE_DIR = Path(os.environ.get("CZ_DATA_DIR", "data")) / "meeting_shares"
DEFAULT_EXPIRY_DAYS = 30
MAX_EXPIRY_DAYS = 365
DEFAULT_CREATIONS_PER_DAY = 50
DEFAULT_HOST = "https://share.shouldersurf.com"

# Link-preview fetchers announce themselves; their fetch of the page is not
# a read by a person and must not count as one (doc: shape-changer 1).
_PREVIEW_UA_MARKERS = (
    "facebookexternalhit", "twitterbot", "slackbot", "discordbot", "telegrambot",
    "whatsapp", "linkedinbot", "imessage", "applebot", "googlebot", "bingbot",
    "skypeuripreview", "embedly", "quora link preview", "pinterestbot", "bot/",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(16)  # 128 bits


# base64url of 16 bytes, unpadded: 22 characters from the URL-safe alphabet.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


def is_token_shaped(token: str) -> bool:
    """Whether a path segment could be one of our tokens at all.

    Defence in depth, added 2026-08-22 after SS sabotaged their own
    universal-link parser and found that widening "exactly two segments" to
    "two or more, take the last" turns `/s/abc/../secret` into the token
    "secret". On their side that is a traversal fragment walking into the
    next request's URL. GP is the only side that can enforce the shape for
    EVERY client rather than trusting each of them to parse correctly, so
    it is enforced here.

    Nothing downstream was exploitable: the token is only ever a bound
    parameter in a SELECT, and `storage_path` comes from the row rather
    than from the URL, so a strange token has always just missed. This
    stops it reaching the database at all, and it means a malformed token
    is answered identically to a wrong one, which keeps the 410-for-
    everything property intact.
    """
    return bool(_TOKEN_RE.match(token or ""))


def is_preview_fetcher(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return any(m in ua for m in _PREVIEW_UA_MARKERS)


# --- card text --------------------------------------------------------------
#
# Scott's ruling, 2026-08-23, from a bubble that read "Summary: Unable to
# provide meaningful meeting summary / share.shouldersurf.com": a failed
# summary is never rendered anywhere a recipient can see it. og:title is
# the meeting title; og:description is the summary only when a real one
# exists, otherwise the date and duration.
#
# The client stored that failure string in BOTH title and summary_line on
# a real share, and the page rendered exactly what it was given. SS has
# since made their title refuse failure text, but the rows already stored
# still carry it, older builds still send it, and a rule that lives only
# in one client is a rule that is only sometimes true. So it lives here
# too, at render time, which also repairs every share already stored.

_FAILURE_MARKERS = (
    "unable to provide meaningful meeting summary",
    "unable to provide a meaningful",
    "no meaningful summary",
    "not enough content to summarize",
    "insufficient content",
)


def _is_failure_text(text: str | None) -> bool:
    t = (text or "").strip().lower()
    return not t or any(m in t for m in _FAILURE_MARKERS)


def _clean_line(text: str) -> str:
    """A summary line as a sentence, not as markdown: strip bold markers,
    heading hashes and a leading "Summary:" label, collapse whitespace."""
    t = text.replace("**", "").replace("__", "")
    t = re.sub(r"^\s*#+\s*", "", t)
    t = re.sub(r"^\s*summary\s*:\s*", "", t, flags=re.I)
    return " ".join(t.split())


def _format_when(meeting_date: str | None) -> str | None:
    """The date as a recipient reads it, in the sender's own offset.
    Accepts ISO with offset or a display string; a display string is
    returned as is, since the client already formatted it."""
    if not meeting_date:
        return None
    try:
        dt = datetime.fromisoformat(meeting_date)
    except (TypeError, ValueError):
        return meeting_date.strip() or None
    return dt.strftime("%b %-d, %Y at %-I:%M %p")


def _format_duration(seconds) -> str | None:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return None
    m = int(round(seconds / 60))
    if m < 1:
        return "under a minute"
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} hr {r} min" if r else f"{h} hr"


def card_text(row) -> tuple[str, str]:
    """(title, description) for the card tags, with the ruling applied.

    Title: the stored title unless it is a failure string, then the
    meeting date, then "Shared meeting". Never empty, because an empty
    og:title makes some fetchers show the URL as the title.

    Description: the stored summary line, cleaned, unless it is a failure
    string, then date and duration, then "" (a missing description
    renders as nothing, which is correct; a fabricated one is not).
    """
    when = _format_when(row["meeting_date"])
    dur = _format_duration(row["duration_seconds"])
    raw_title = row["title"]
    if _is_failure_text(raw_title):
        title = when or "Shared meeting"
    else:
        title = _clean_line(raw_title)
    raw_desc = row["summary_line"]
    if _is_failure_text(raw_desc):
        desc = " · ".join(x for x in (when, dur) if x)
    else:
        desc = _clean_line(raw_desc)
    return title, desc


# --- dials ------------------------------------------------------------------

def share_settings(remote_configs: dict) -> dict:
    """client-config.share: {host, default_expiry_days, max_expiry_days}.
    Every value has a code default so an absent block serves the feature
    at the scoped numbers rather than breaking it."""
    block = ((remote_configs.get("client-config") or {}).get("share")) or {}
    host = str(block.get("host") or DEFAULT_HOST).rstrip("/")
    return {
        "host": host,
        "default_expiry_days": int(block.get("default_expiry_days") or DEFAULT_EXPIRY_DAYS),
        "max_expiry_days": int(block.get("max_expiry_days") or MAX_EXPIRY_DAYS),
        # Numeric App Store id, as a STRING because it is an identifier
        # rather than a quantity and nothing ever does arithmetic on it.
        # A dial rather than a constant so it can move without a deploy,
        # same as the host. Absent = the page shows no App Store route at
        # all, which is the right failure: a dead store link on a page a
        # stranger opens is worse than no link.
        "app_store_id": str(block["app_store_id"]).strip()
        if str(block.get("app_store_id") or "").strip() else None,
        # The unfurl image and the touch icon. Dials so they can move to a
        # CDN or a redesign without a deploy; default to the share origin's
        # own /share-assets/ so a bare config still produces a bubble with
        # the mark on it rather than a Safari compass.
        "og_image_url": str(block.get("og_image_url") or f"{host}/share-assets/card-1200x630.png"),
        "icon_url": str(block.get("icon_url") or f"{host}/share-assets/icon-512.png"),
    }


def tier_share_caps(remote_configs: dict, tier: str) -> dict:
    """tiers.{tier}.feature_definitions.share: {creations_per_day,
    max_archive_mb}. Default 50 creations a day.

    `transcript_allowed` was here and is gone (2026-08-22). It was written
    on the belief that the sender had a per-share transcript toggle doing
    the work; SS checked their exporter and there is no such toggle and
    never was, so the dial could only ever turn every share from a tier
    into an unrecoverable 403. Withholding sharing from a tier is the
    `share` entitlement's job."""
    tiers = ((remote_configs.get("tiers") or {}).get("tiers") or {})
    block = ((tiers.get(tier) or {}).get("feature_definitions") or {}).get("share") or {}
    cap = block.get("creations_per_day")
    mb = block.get("max_archive_mb")
    return {
        "creations_per_day": int(cap) if isinstance(cap, int) and not isinstance(cap, bool) else DEFAULT_CREATIONS_PER_DAY,
        # Archive size cap per tier, in MB; null/absent = NO cap (Scott,
        # 2026-08-22: no cap now, dashboard control, gate as necessary).
        # SS measured real bundles at 275 KB to 36.9 MB, audio in eleven of
        # twelve, so any number here is a product choice, not a safety one.
        "max_archive_bytes": int(mb * 1048576) if isinstance(mb, (int, float)) and not isinstance(mb, bool) and mb > 0 else None,
    }


def aasa_app_ids(remote_configs: dict) -> list[str]:
    """client-config.share.aasa_app_ids: ["F22KGHDYAE.com.shouldersurf.ShoulderSurf"].
    Empty until SS supplies the Team ID; the AASA route 404s until then so
    Apple never caches an association with no app in it."""
    block = ((remote_configs.get("client-config") or {}).get("share")) or {}
    ids = block.get("aasa_app_ids") or []
    return [i for i in ids if isinstance(i, str) and "." in i]


# --- rows -------------------------------------------------------------------

async def creations_today(db: aiosqlite.Connection, user_id: str) -> int:
    since = (_now() - timedelta(days=1)).isoformat()
    row = await (await db.execute(
        "SELECT COUNT(*) AS n FROM meeting_shares WHERE user_id = ? AND created_at > ?",
        (user_id, since))).fetchone()
    return int(row["n"] if row else 0)


async def create_share(db: aiosqlite.Connection, *, user_id: str, app_id: str | None,
                       archive: bytes, media_type: str, title: str, meeting_date: str | None,
                       duration_seconds: int | None, summary_line: str | None,
                       transcript_included: bool, expiry_days: int) -> dict:
    share_id = secrets.token_hex(8)
    token = new_token()
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    path = SHARE_DIR / f"{share_id}.bin"
    path.write_bytes(archive)
    now = _now(); expires = now + timedelta(days=expiry_days)
    await db.execute(
        """INSERT INTO meeting_shares
           (id, user_id, app_id, token, storage_path, media_type, size_bytes, title,
            meeting_date, duration_seconds, summary_line, transcript_included,
            created_at, expires_at, revoked_at, view_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)""",
        (share_id, user_id, app_id, token, str(path), media_type, len(archive), title,
         meeting_date, duration_seconds, summary_line, 1 if transcript_included else 0,
         now.isoformat(), expires.isoformat()))
    await db.commit()
    logger.info("meeting_share_created", extra={
        "share_id": share_id, "user_id": user_id, "size_bytes": len(archive),
        "transcript_included": transcript_included, "expires_at": expires.isoformat()})
    return {"share_id": share_id, "token": token, "expires_at": expires.isoformat()}


async def share_by_token(db: aiosqlite.Connection, token: str) -> aiosqlite.Row | None:
    return await (await db.execute(
        "SELECT * FROM meeting_shares WHERE token = ?", (token,))).fetchone()


async def share_by_id(db: aiosqlite.Connection, share_id: str) -> aiosqlite.Row | None:
    return await (await db.execute(
        "SELECT * FROM meeting_shares WHERE id = ?", (share_id,))).fetchone()


def is_live(row) -> bool:
    if row is None or row["revoked_at"]:
        return False
    return datetime.fromisoformat(row["expires_at"]) > _now()


async def count_view(db: aiosqlite.Connection, share_id: str) -> None:
    await db.execute("UPDATE meeting_shares SET view_count = view_count + 1 WHERE id = ?", (share_id,))
    await db.commit()


async def revoke(db: aiosqlite.Connection, share_id: str) -> None:
    row = await share_by_id(db, share_id)
    if row is None:
        return
    try:
        Path(row["storage_path"]).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("meeting_share revoke: could not delete %s: %s", row["storage_path"], e)
    await db.execute("UPDATE meeting_shares SET revoked_at = ?, storage_path = '' WHERE id = ?",
                     (_now().isoformat(), share_id))
    await db.commit()
    logger.info("meeting_share_revoked", extra={"share_id": share_id})


async def purge_expired(db: aiosqlite.Connection) -> int:
    """Delete expired and revoked rows and their bytes. Runs on the same
    periodic retention sweep as generated_files. Delete, never archive."""
    rows = await (await db.execute(
        "SELECT id, storage_path FROM meeting_shares WHERE expires_at <= ? OR revoked_at IS NOT NULL",
        (_now().isoformat(),))).fetchall()
    for r in rows:
        if r["storage_path"]:
            try:
                Path(r["storage_path"]).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("meeting_share purge: could not delete %s: %s", r["storage_path"], e)
    if rows:
        await db.execute("DELETE FROM meeting_shares WHERE id IN (%s)" % ",".join("?" * len(rows)),
                         [r["id"] for r in rows])
        await db.commit()
    return len(rows)
