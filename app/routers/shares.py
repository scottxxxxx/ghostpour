"""Meeting share via iMessage: the GP routes.

Authenticated (SS, bearer JWT):
  POST   /v1/shares                 archive bytes + preview fields -> {share_id, url, expires_at}
  DELETE /v1/shares/{share_id}      owner only; immediate; page goes 410
  GET    /v1/shares/{share_id}/stats owner only; view_count excludes preview fetchers

Public (the share host, no sign-in):
  GET /s/{token}                    the hosted page (renderer lands with SS's archive spec)
  GET /s/{token}/archive            the archive bytes, for the app's universal-link handler
  GET /.well-known/apple-app-site-association

A link-preview fetcher only ever meets the page route, never the archive,
because the split is by path, not by Accept. Nothing here imports
Context Quilt: the shared object is SS's meeting record, never memory.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import meeting_shares as shares
from app.services.entitlements import entitlement_state

logger = logging.getLogger("ghostpour.shares")

router = APIRouter()
public = APIRouter()


def _meta(request: Request, key: str) -> str | None:
    v = request.headers.get(f"X-Share-{key}")
    return v if v else None


def _card_text(value: str | None) -> str | None:
    """Decode a card field carried in an X-Share-* header.

    The card fields ride in headers so the body can be the archive bytes
    with no multipart parsing between SS's zip and our disk. That is the
    right call for the BYTES and a trap for the TEXT: an HTTP header value
    is not a place to put user-generated Unicode. A meeting titled
    "四半期レビュー" cannot be put in a header at all by a strict client
    (httpx raises UnicodeEncodeError outright), and a client that writes
    the raw UTF-8 bytes anyway gets them back as latin-1 mojibake here,
    stored, and rendered onto the card and the og:title where a human
    reads it.

    So the contract is: UTF-8, percent-encoded. `%E5%9B%9B...`. That is
    ASCII on the wire, survives every proxy, is one rule rather than a
    guess, and is a no-op for a plain ASCII title, which is why an
    unencoded English title still works. A literal percent must be sent
    as %25, per the same rule as every URL anyone has ever written.

    `errors="replace"` on purpose: a malformed sequence should cost the
    reader one character, not the whole share. Nothing here 4xxs on text,
    because a share that fails to send is worse than a title with a
    replacement character in it.

    Deliberately NOT also repairing latin-1 mojibake. Two accepted
    encodings on one carrier is how a field comes to mean two things, and
    SS has not built this client yet, so there is no legacy to carry.
    """
    if value is None:
        return None
    # Reject rather than store mojibake. A correctly encoded value is pure
    # ASCII by construction, so a byte above 0x7f can only mean the client
    # did not encode: it wrote raw UTF-8, ASGI decoded it latin-1, and what
    # we have in hand is already wrong. We cannot recover the original
    # reliably and we must not guess, so the only honest options are 400
    # here or a wrong title on a card forever. CQ's point, and it is right:
    # this is the kind of rule only the RECEIVING side can enforce, and it
    # is cheaper than the support ticket. The client learns at build time
    # instead of a user learning from a bubble that says something else.
    from urllib.parse import unquote
    if any(ch > "\x7f" for ch in value):
        raise HTTPException(status_code=400, detail={
            "code": "share_header_not_encoded",
            "message": "Card text must be UTF-8 percent-encoded; this header "
                       "carries raw non-ASCII bytes and cannot be recovered."})
    return unquote(value, encoding="utf-8", errors="replace")


# An absolute ceiling on an upload, separate from the product dial and not
# configurable. Scott ruled the archive uncapped by default (2026-08-22) and
# that ruling is about PRODUCT limits: what a plan is allowed to share. This
# is a memory bound, and the two must not be the same number, because
# "uncapped" cannot mean "whatever an authenticated client feels like
# sending". SS measured real bundles at 275 KB to 36.9 MB with audio running
# about 14.4 MB an hour, so even a six hour meeting lands near 90 MB. 256 MB
# is far above any real archive and far below anything that hurts.
ABSOLUTE_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_UPLOAD_CHUNK_CEILING = ABSOLUTE_MAX_ARCHIVE_BYTES


async def _read_archive(request: Request, cap_bytes: int | None) -> bytes:
    """Read the uploaded archive, refusing it DURING the read.

    This used to be `await request.body()` followed by a length check, which
    is the check-after-read mistake in its original habitat: the tier dial
    could not protect anything, because by the time a 25 MB limit said no,
    25 MB was the least of what had already been buffered. The proxy in
    front of us allows 2000m (Bifrost, measured on the live VM, 2026-08-22),
    so an authenticated client could make this process allocate two
    gigabytes and then be told politely that the limit was twenty five
    megabytes.

    Found because CQ asked whether the edge caps request bodies. It does
    not, and the answer to their question was less interesting than what
    looking for it turned up. Same shape as the unbounded unzip one layer
    up, in code I had read the same morning without seeing it.

    Content-Length is checked first because it is free, and is NOT trusted,
    for the same reason a zip's declared entry size is not: it is a claim by
    the sender. The streaming total is the actual bound.
    """
    effective = min(cap_bytes, ABSOLUTE_MAX_ARCHIVE_BYTES) if cap_bytes is not None \
        else ABSOLUTE_MAX_ARCHIVE_BYTES

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > effective:
        _too_large(int(declared), effective, cap_bytes)

    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > effective:
            _too_large(len(buf), effective, cap_bytes, exceeded=True)
    if not buf:
        raise HTTPException(status_code=422, detail={
            "code": "share_empty", "message": "No archive in body."})
    return bytes(buf)


def _too_large(size: int, effective: int, cap_bytes: int | None, exceeded: bool = False):
    """Two different refusals, deliberately, because they mean different
    things to whoever reads them. A tier dial is a product limit with a
    recovery the sender can act on. The absolute ceiling means this is not a
    meeting archive, and telling someone to share a shorter meeting would be
    a lie about what went wrong."""
    mb = round(size / 1048576, 1)
    if cap_bytes is not None and effective == cap_bytes:
        limit_mb = round(cap_bytes / 1048576, 1)
        raise HTTPException(status_code=413, detail={
            "code": "share_too_large",
            "message": f"This share is {'over ' if exceeded else ''}{mb} MB; the limit on this plan is "
                       f"{limit_mb} MB. Share it without audio, or share a shorter meeting.",
            "size_bytes": size, "limit_bytes": cap_bytes})
    raise HTTPException(status_code=413, detail={
        "code": "share_archive_rejected",
        "message": f"This upload is {'over ' if exceeded else ''}{mb} MB, which is larger than any meeting archive.",
        "size_bytes": size, "limit_bytes": effective})


@router.post("/shares")
async def create_share(
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
    content_type: str = Header(default="application/octet-stream"),
    x_share_title: str = Header(..., alias="X-Share-Title"),
    x_share_date: str | None = Header(default=None, alias="X-Share-Date"),
    x_share_duration: int | None = Header(default=None, alias="X-Share-Duration-Seconds"),
    x_share_summary: str | None = Header(default=None, alias="X-Share-Summary-Line"),
    x_share_transcript: str | None = Header(default=None, alias="X-Share-Transcript-Included"),
    x_share_expiry: int | None = Header(default=None, alias="X-Share-Expiry-Days"),
):
    """Body is the raw `.shouldersurf` archive; the card fields ride in
    X-Share-* headers so the bytes are stored exactly as uploaded with no
    multipart parsing between SS's archive and our disk."""
    rc = request.app.state.remote_configs
    if entitlement_state(rc, user.effective_tier, "share",
                         getattr(request.state, "app_id", None)) == "disabled":
        raise HTTPException(status_code=403, detail={"code": "share_disabled", "message": "Sharing is not available on this plan."})
    caps = shares.tier_share_caps(rc, user.effective_tier)
    # A FACT about the archive, not a choice. SS's exporter has no
    # transcript toggle and never had one: BundlePayloadOptions covers
    # audio, images, diarization, chat, report and generated files, and
    # applyPayloadFilter strips only chat and report. The transcript is a
    # field on the meeting record, so it always travels; SS measured all
    # twelve real bundles and the 21-meeting project bundle carrying one.
    # Scott ruled 2026-08-22 that it is SHOWN on the page behind
    # tap-to-reveal on that basis.
    #
    # There used to be a 403 here when a tier's `transcript_allowed` dial
    # was off. It is gone, because it gated something no user can change:
    # flipping that dial did not restrict a feature, it made EVERY share
    # from that tier fail, with a message telling the sender to do a thing
    # the app cannot do. A control whose only reachable state is
    # unrecoverable failure is a footgun, not a dial. It had never been
    # set on any tier, so nothing observable changes today; what changes
    # is that nobody can arm it by accident from the dashboard.
    #
    # If a real per-share transcript choice is wanted later, it is SS
    # exporter work (strip transcript, cleanedTranscript,
    # transcriptSegments) plus an archive spec change, and the gate then
    # belongs next to the toggle that actually exists. Removing this does
    # not foreclose that. Withholding sharing from a tier entirely is
    # already the `share` entitlement, one line above.
    transcript_included = (x_share_transcript or "").lower() in ("1", "true", "yes")
    if await shares.creations_today(db, user.id) >= caps["creations_per_day"]:
        raise HTTPException(status_code=429, detail={"code": "share_rate_limited", "message": "Daily share limit reached."})
    settings = shares.share_settings(rc)
    cap_bytes = caps["max_archive_bytes"]
    archive = await _read_archive(request, cap_bytes)
    expiry = min(max(int(x_share_expiry or settings["default_expiry_days"]), 1), settings["max_expiry_days"])
    created = await shares.create_share(
        db, user_id=user.id, app_id=getattr(request.state, "app_id", None),
        archive=archive, media_type=content_type, title=_card_text(x_share_title),
        meeting_date=x_share_date, duration_seconds=x_share_duration,
        summary_line=_card_text(x_share_summary), transcript_included=transcript_included,
        expiry_days=expiry)
    return {"share_id": created["share_id"],
            "url": f"{settings['host']}/s/{created['token']}",
            "expires_at": created["expires_at"]}


@router.delete("/shares/{share_id}")
async def revoke_share(share_id: str, user: UserRecord = Depends(get_current_user),
                       db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_id(db, share_id)
    if row is None or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail={"code": "share_not_found"})
    await shares.revoke(db, share_id)
    return {"share_id": share_id, "status": "revoked"}


@router.get("/shares/{share_id}/stats")
async def share_stats(share_id: str, user: UserRecord = Depends(get_current_user),
                      db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_id(db, share_id)
    if row is None or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail={"code": "share_not_found"})
    return {"share_id": share_id, "view_count": row["view_count"], "expires_at": row["expires_at"],
            "revoked": bool(row["revoked_at"]), "live": shares.is_live(row)}


# --- public -------------------------------------------------------------------

_GONE_HTML = "<!doctype html><meta charset='utf-8'><title>Shoulder Surf</title><p>This shared meeting is no longer available.</p>"


# The two images the share card points at. Served by GP rather than by a
# CDN so the share origin is the only host a recipient's messenger ever
# fetches from, and served by name rather than by a StaticFiles mount so
# there is nothing to walk. Scott, 2026-08-23, on his first real share:
# the iMessage bubble showed a Safari compass where the app icon should
# be, because the page served og:title and og:description and no
# og:image. "Doesn't look very polished or refined."
_SHARE_ASSET_DIR = Path(__file__).resolve().parent.parent / "static" / "share"
_SHARE_ASSETS = {
    "card-1200x630.png": "image/png",   # og:image / twitter:image, the unfurl card
    "icon-512.png": "image/png",        # apple-touch-icon, the compact bubble
}


@public.api_route("/share-assets/{name}", methods=["GET", "HEAD"])
async def share_asset(name: str):
    media = _SHARE_ASSETS.get(name)
    if not media:
        raise HTTPException(status_code=404)
    path = _SHARE_ASSET_DIR / name
    if not path.is_file():
        logger.error("share_asset_missing_on_disk name=%s", name)
        raise HTTPException(status_code=404)
    # Immutable for a day: messengers cache previews at send time anyway,
    # and the name changes if the image does.
    return Response(content=path.read_bytes(), media_type=media,
                    headers={"Cache-Control": "public, max-age=86400"})


@public.api_route("/.well-known/apple-app-site-association", methods=["GET", "HEAD"])
async def aasa(request: Request):
    ids = shares.aasa_app_ids(request.app.state.remote_configs)
    if not ids:
        raise HTTPException(status_code=404)
    body = {"applinks": {"apps": [], "details": [{"appIDs": ids, "components": [{"/": "/s/*"}]}]}}
    return Response(content=json.dumps(body), media_type="application/json")


@public.api_route("/s/{token}/archive", methods=["GET", "HEAD"])
async def share_archive(request: Request, token: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await shares.share_by_token(db, token) if shares.is_token_shaped(token) else None
    if not shares.is_live(row):
        raise HTTPException(status_code=410)
    hdrs = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}
    if request.method == "HEAD":
        # Answer from the row. Reading tens of megabytes off disk to throw
        # the body away is the obvious implementation and the wrong one,
        # and a HEAD is precisely the request that is asking not to be sent
        # the bytes.
        return Response(status_code=200, media_type=row["media_type"],
                        headers={**hdrs, "Content-Length": str(row["size_bytes"])})
    with open(row["storage_path"], "rb") as f:
        data = f.read()
    return Response(content=data, media_type=row["media_type"], headers=hdrs)


@public.api_route("/s/{token}", methods=["GET", "HEAD"])
async def share_page(token: str, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """GET and HEAD, because FastAPI does not add HEAD to a `.get()` route
    the way plain Starlette does, so this answered 405 to every HEAD.

    Found by Bifrost at the edge and confirmed by CQ, 2026-08-22. Most
    unfurlers send GET, and the ones that HEAD first mostly fall back, so
    the symptom is not an error anywhere: it is an iMessage bubble or a
    Slack unfurl that renders EMPTY, with nothing in our logs for a GET
    that never came. It reads as a rendering bug and it is a method bug.

    A HEAD never counts as a view, and that is not an optimisation. A HEAD
    is a probe by definition, so counting it would put the same fiction in
    `view_count` that the preview-fetcher filter exists to keep out, and it
    would arrive through a door that filter does not watch."""
    row = await shares.share_by_token(db, token) if shares.is_token_shaped(token) else None
    is_head = request.method == "HEAD"
    if not shares.is_live(row):
        return HTMLResponse("" if is_head else _GONE_HTML, status_code=410,
                            headers={"X-Robots-Tag": "noindex"})
    if is_head:
        # Same status, same content type, no body and no view. Rendering the
        # page to discard it would also mean unzipping the bundle for a
        # request that asked for headers.
        return Response(status_code=200, media_type="text/html; charset=utf-8",
                        headers={"X-Robots-Tag": "noindex", "Cache-Control": "private, no-store"})
    if not shares.is_preview_fetcher(request.headers.get("User-Agent")):
        await shares.count_view(db, row["id"])
    # The page: card chrome plus the bundle's own report (SS's archive
    # spec 2026-08-22). A bundle that is not a zip, or a zip with odd
    # contents, still gets the card; the bytes are never the page.
    from app.services.share_bundle import list_audio_entries, list_image_counts, list_image_entries, read_bundle, render_share_page
    audio_by_origin: dict[str, list[str]] = {}
    images_by_origin: dict[str, int] = {}
    images_by_origin_names: dict[str, list[str]] = {}
    try:
        with open(row["storage_path"], "rb") as f:
            bundle = read_bundle(f.read())
        audio_by_origin = list_audio_entries(row["storage_path"])
        images_by_origin = list_image_counts(row["storage_path"])
        images_by_origin_names = list_image_entries(row["storage_path"])
    except Exception as e:  # noqa: BLE001  (BadZipFile, OSError, anything)
        logger.warning("share_bundle_unreadable", extra={"share_id": row["id"], "error": type(e).__name__})
        bundle = {"meetings": []}
    settings = shares.share_settings(request.app.state.remote_configs)
    card_title, card_desc = shares.card_text(row)
    if settings.get("dynamic_card"):
        # og:description leads with the card's headline ("4 open items, 2
        # urgent") for clients that show it (Slack, WhatsApp); iMessage
        # ignores it. The title stays og:title only (R1).
        from app.services.share_card import facts_from_share, headline_text, plan_card
        try:
            _desc_lead = headline_text(plan_card(facts_from_share(row, bundle)))
        except Exception:  # noqa: BLE001  (odd bundle: keep the plain description)
            _desc_lead = None
    else:
        _desc_lead = None
    html = render_share_page(
        bundle, card_title=card_title, card_desc=card_desc,
        transcript_included=bool(row["transcript_included"]), expires_at=row["expires_at"],
        app_store_id=settings["app_store_id"],
        og_image_url=(f"{settings['host']}/s/{token}/card.png" if settings.get("dynamic_card") else settings["og_image_url"]),
        icon_url=settings["icon_url"],
        # The canonical URL for THIS share, handed to Apple's banner as
        # app-argument so "Open" lands the app on this meeting rather than
        # on its home screen. Built from the served host, not from the
        # request, so a share opened through any hostname still points the
        # app at the one we publish.
        share_url=f"{settings['host']}/s/{token}",
        audio_by_origin=audio_by_origin, images_by_origin=images_by_origin,
        qr_url=settings["qr_url"], images_by_origin_names=images_by_origin_names,
        desc_lead=_desc_lead)
    return HTMLResponse(html, headers={"X-Robots-Tag": "noindex", "Cache-Control": "private, no-store"})


@public.api_route("/s/{token}/image/{n}", methods=["GET", "HEAD"])
async def share_image(request: Request, token: str, n: int, db: aiosqlite.Connection = Depends(get_db)):
    """One photo shared with the meeting, straight out of the bundle
    (Scott 2026-08-24). n indexes the bundle's image entries in name order
    across meetings; bounded read, never a partial file."""
    from app.services.share_bundle import MAX_ENTRY_BYTES, flat_image_entries, list_image_entries
    row = await shares.share_by_token(db, token) if shares.is_token_shaped(token) else None
    if not shares.is_live(row):
        raise HTTPException(status_code=410)
    entries = flat_image_entries(list_image_entries(row["storage_path"]))
    if n < 0 or n >= len(entries):
        raise HTTPException(status_code=404)
    import zipfile
    try:
        with zipfile.ZipFile(row["storage_path"]) as zf:
            info = zf.getinfo(entries[n][1])
            if info.file_size > MAX_ENTRY_BYTES:
                raise HTTPException(status_code=413)
            data = zf.read(info)
    except (zipfile.BadZipFile, KeyError, OSError):
        raise HTTPException(status_code=404)
    name = entries[n][1].lower()
    media = "image/png" if name.endswith(".png") else "image/jpeg"
    hdrs = {"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex"}
    if request.method == "HEAD":
        return Response(status_code=200, media_type=media, headers={**hdrs, "Content-Length": str(len(data))})
    return Response(content=data, media_type=media, headers=hdrs)


@public.api_route("/s/{token}/card.png", methods=["GET", "HEAD"])
async def share_card(request: Request, token: str, db: aiosqlite.Connection = Depends(get_db)):
    """The link-preview card (ledger 2b), rendered once per share from the
    bundle bytes and the share row, cached as a sidecar beside the archive
    (preview bots fetch og:image several times per unfurl; the edge does
    not cache for us). Dies with the share on purge."""
    from fastapi.responses import FileResponse
    from app.services.share_bundle import list_audio_entries, read_bundle
    from app.services.share_card import facts_from_share, render_card
    row = await shares.share_by_token(db, token) if shares.is_token_shaped(token) else None
    if not shares.is_live(row):
        raise HTTPException(status_code=410)
    from app.services.share_card import sidecar_path
    sidecar = sidecar_path(row["storage_path"])
    if not Path(sidecar).exists():
        try:
            with open(row["storage_path"], "rb") as f:
                bundle = read_bundle(f.read())
            audio = list_audio_entries(row["storage_path"])
        except Exception:  # noqa: BLE001
            bundle, audio = {"meetings": []}, {}
        settings = shares.share_settings(request.app.state.remote_configs)
        facts = facts_from_share(row, bundle, audio_count=sum(len(v) for v in audio.values()),
                                 cta_text=settings.get("card_cta_text"), pill_text=settings.get("card_pill_text"))
        # A translated share quotes its action text from the report's
        # ORIGINAL language (the bundle carries no structured translated
        # report), so translate what the card shows before drawing it.
        # Billed to the share owner, like the page translations.
        from app.services.share_card import translate_card_facts
        facts = await translate_card_facts(
            facts, app_state=request.app.state, db=db,
            user=await shares.owner_user(db, row["user_id"]),
            app_id=row["app_id"], request_id=getattr(request.state, "request_id", None))
        png = render_card(facts)
        tmp = sidecar + ".part"
        with open(tmp, "wb") as f:
            f.write(png)
        os.replace(tmp, sidecar)
    hdrs = {"Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex"}
    return FileResponse(sidecar, media_type="image/png", headers=hdrs, method=request.method)


@public.api_route("/s/{token}/audio/{n}", methods=["GET", "HEAD"])
async def share_audio(request: Request, token: str, n: int, db: aiosqlite.Connection = Depends(get_db)):
    """One audio entry of the shared meeting, as bytes with Range support
    (Scott 2026-08-24: playable on the page). `n` indexes the bundle's
    audio entries in name order across its meetings. Extracted once to a
    sidecar beside the archive; the sidecar dies with the share."""
    from fastapi.responses import FileResponse
    from app.services.share_bundle import extract_audio_sidecar, flat_audio_entries, list_audio_entries
    row = await shares.share_by_token(db, token) if shares.is_token_shaped(token) else None
    if not shares.is_live(row):
        raise HTTPException(status_code=410)
    entries = flat_audio_entries(list_audio_entries(row["storage_path"]))
    if n < 0 or n >= len(entries):
        raise HTTPException(status_code=404)
    sidecar = f"{row['storage_path']}.audio{n}.m4a"
    if not Path(sidecar).exists() and not extract_audio_sidecar(row["storage_path"], entries[n][1], sidecar):
        raise HTTPException(status_code=404)
    hdrs = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex", "Accept-Ranges": "bytes"}
    return FileResponse(sidecar, media_type="audio/mp4", headers=hdrs, method=request.method)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
