"""Context Quilt proxy endpoints.

These endpoints proxy requests from the client app to the Context Quilt
service. They are conditionally included in main.py only when CZ_CQ_BASE_URL
is configured. Apps that don't use Context Quilt won't have these routes.
"""

import asyncio
import hashlib
import logging
from typing import Any

import aiosqlite
import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import context_quilt as cq
from app.services.cq_subject import subject_for
from app.services.memory_capture_policy import resolve_memory_capture_verdict
from app.services.memory_capture_quota import (
    decrement_memory_quota,
    read_memory_quota_state,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _report_cq_incident(
    request: Request,
    db: aiosqlite.Connection,
    *,
    kind: str,           # "timeout" or "unreachable"
    request_id: str | None = None,
    error: str | None = None,
) -> None:
    """Report a CQ-side critical-failure incident to the alerting
    service. Swallows all exceptions — alerting must never break the
    request path that triggered it.

    `kind` distinguishes timeout vs unreachable so the dashboard
    history shows which kind of failure has been happening. Subject
    stays "cq" so all CQ failures dedupe into one incident."""
    try:
        from app.services.alerting import report_incident
        settings = request.app.state.settings
        details: dict[str, str] = {"kind": kind}
        if request_id:
            details["request_id"] = request_id
        if error:
            details["error"] = error
        await report_incident(
            db,
            category="cq_unreachable",
            subject="cq",
            details=details,
            from_addr=settings.alert_email_from,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("cq_incident_report_failed reason=%s", str(exc)[:200])


# --- Proxy helper ---


async def _cq_proxy(
    method: str,
    path: str,
    body: dict | None = None,
    query: str | None = None,
) -> JSONResponse:
    """Forward a request to Context Quilt and return its response.

    `query` is the caller's raw query string, forwarded verbatim so CQ
    query features (e.g. ?since=...&delta=true on /v1/quilt) and any
    params CQ adds later pass through without GP knowing about them.
    """
    settings = get_settings()
    if not settings.cq_base_url:
        raise HTTPException(status_code=503, detail="Context Quilt not configured")

    try:
        auth_headers = await cq._get_auth_headers()
        async with httpx.AsyncClient(base_url=settings.cq_base_url, timeout=10.0) as client:
            resp = await client.request(
                method,
                f"{path}?{query}" if query else path,
                json=body,
                headers=auth_headers,
            )
        try:
            content = resp.json()
        except Exception:
            content = {"detail": resp.text or "Context Quilt error"}

        # Don't pass through CQ's 401 as GP's 401 — the user's JWT was valid,
        # CQ's server-to-server auth failed. Map to 502 so the client doesn't
        # think its own token was rejected and trigger a refresh loop.
        if resp.status_code == 401:
            logger.warning("cq_proxy_auth_rejected", extra={"path": path, "detail": str(content)[:200]})
            return JSONResponse(status_code=502, content={
                "detail": {
                    "code": "upstream_auth_error",
                    "upstream": "cq",
                    "message": "Context Quilt rejected server credentials",
                }
            })

        return JSONResponse(status_code=resp.status_code, content=content)
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"detail": "Context Quilt timeout"})
    except Exception as e:
        return JSONResponse(status_code=502, content={"detail": f"Context Quilt unreachable: {e}"})


# --- Transcript capture ---


def _primary_language_tag(header: str | None) -> str | None:
    """First language tag from an Accept-Language header, verbatim.

    "es-US,es-419;q=0.9,es;q=0.8" → "es-US". Unlike config.py's
    _parse_accept_language this keeps the full BCP-47 tag and treats
    English as a real answer — CQ's metadata.language is a writing
    directive, not a locale-variant lookup.
    """
    if not header:
        return None
    first = header.split(",")[0].strip().split(";")[0].strip()
    return first or None


class TranscriptCaptureRequest(BaseModel):
    transcript: str
    # Origin scoping — preferred going forward:
    origin_id: str | None = None
    origin_type: str | None = None
    # Deprecated alias (CQ v1 renamed meeting_id → origin_id + origin_type).
    # Still accepted from clients that haven't migrated; we translate below.
    meeting_id: str | None = None
    project: str | None = None
    project_id: str | None = None
    # Speaker identification (forwarded to CQ /v1/memory metadata).
    # Accept at top level OR inside the metadata dict — clients vary.
    user_identified: bool | None = None
    user_label: str | None = None
    identification_source: str | None = None
    metadata: dict[str, Any] | None = None

    def get_meta(self, key: str, default: Any = None) -> Any:
        """Read a value from metadata, falling back to top-level field."""
        if self.metadata and key in self.metadata:
            return self.metadata[key]
        return getattr(self, key, default)


def _subj(request: Request, user_id: str) -> str:
    """The CQ subject for this user under the calling app.

    Inlined at every outbound call site rather than assigned to a local,
    so a new route cannot forget it and still compile into something that
    writes to the wrong subject. tests/test_cq_subject.py scans this file
    and fails if any outbound path interpolates a raw user_id again.
    """
    return subject_for(getattr(request.state, "app_id", None), user_id)


@router.post("/capture-transcript")
async def capture_transcript(
    body: TranscriptCaptureRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    End-of-session transcript capture for Context Quilt + local storage.

    Called by the client app at session end to send the full raw transcript.
    CQ extracts traits, preferences, and durable facts from the raw dialogue.
    GP also stores the transcript locally for meeting report generation.

    Tier gating: the local transcript write always happens (meeting_reports
    is independent of context_quilt). The CQ extraction is metered:
      Pro    → unconditional capture
      Plus   → recall-only (no capture; recall stays on the chat-flow hook)
      Free   → capture once per month within `free_quota_per_month`; over
               quota, skip capture.
    The free-tier Memory upsell rides the gate/teaser lane with served
    copy (decision 2026-08-11); GP no longer stamps or injects anything.
    See docs/wire-contracts/memory-capture.md for the full matrix.
    """
    # Normalize origin fields — prefer explicit origin_id/origin_type;
    # translate legacy meeting_id if that's what the client sent.
    effective_origin_id = body.origin_id or body.meeting_id
    effective_origin_type = body.origin_type or ("meeting" if body.meeting_id else None)

    # SS sets X-CZ-Recovery on captures that originate from a client-side
    # recovery flow (e.g., "report-404-replay" when the report endpoint
    # 404'd because the original capture never landed). Absent on first-
    # send captures. Logged so dashboards can split capture volume by
    # source and measure how often recovery fires.
    recovery_source = request.headers.get("X-CZ-Recovery")
    if recovery_source:
        logger.info(
            "capture_transcript_recovery",
            extra={
                "recovery_source": recovery_source,
                "user_id": user.id,
                "origin_id": effective_origin_id,
                "origin_type": effective_origin_type,
            },
        )

    # Store transcript locally for report generation. The local meeting_transcripts
    # table still uses meeting_id as its column name; reuse whichever origin id the
    # client provided (for "meeting" origins this is a direct map).
    if effective_origin_id:
        from datetime import datetime, timezone
        import uuid
        await db.execute(
            """INSERT OR REPLACE INTO meeting_transcripts
               (id, user_id, meeting_id, transcript, project, project_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                user.id,
                effective_origin_id,
                body.transcript,
                body.project,
                body.project_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    # Resolve tier-based verdict for the CQ extraction call.
    feature_config = request.app.state.feature_config
    from app.services.entitlements import entitlement_state
    feature_state = entitlement_state(
        request.app.state.remote_configs, user.effective_tier, "context_quilt")
    cq_def = feature_config.features.get("context_quilt")
    free_quota_per_month = cq_def.free_quota_per_month if cq_def else 1

    # Free quota gate (only consulted when feature_state == "disabled").
    quota_state = read_memory_quota_state(user, free_quota_per_month)
    # People is exempt from the free-tier cap (Scott, 2026-08-10), so the
    # People entitlement decides whether a free user's meeting is captured
    # at all. Read from the same matrix as every other gate rather than
    # assumed, so flipping the dashboard row actually closes the door.
    _people_state = entitlement_state(
        request.app.state.remote_configs, user.effective_tier, "people")
    verdict = resolve_memory_capture_verdict(
        feature_state=feature_state,
        has_quota=quota_state.has_quota,
        people_enabled=_people_state != "disabled",
    )

    if verdict.verdict in ("capture", "capture_with_cta"):
        asyncio.create_task(cq.capture(
            app_id=getattr(request.state, "app_id", None),
            user_id=user.id,
            interaction_type="meeting_transcript",
            content=body.transcript,
            origin_id=effective_origin_id,
            origin_type=effective_origin_type,
            project=body.project,
            project_id=body.project_id,
            display_name=user.display_name,
            email=user.email,
            user_identified=body.get_meta("user_identified"),
            user_label=body.get_meta("user_label"),
            identification_source=body.get_meta("identification_source"),
            subscription_tier=user.effective_tier,
            # Client metadata now rides the allowlist rather than being
            # enumerated field by field here AND again inside capture().
            # Adding a key is one edit to CAPTURE_METADATA_ALLOWLIST; before
            # this it was three across two files, and missing one produced a
            # key that was present in the request and read by nothing.
            passthrough=body.metadata,
            # metadata.language arrives from the app (device locale) on
            # builds that send it; older builds fall back to the request's
            # Accept-Language, which reflects the same device setting. Kept
            # explicit because of that server-side fallback: the allowlist
            # forwards what the client sent, this supplies what it did not.
            language=body.get_meta("language")
            or _primary_language_tag(request.headers.get("Accept-Language")),
        ))

    if verdict.verdict == "capture_with_cta":
        # Free-within-quota: count it.
        await decrement_memory_quota(db, user.id)

    # verdict.cta_kind still names the free-Memory copy state, but GP no
    # longer stamps it per meeting or injects a synthetic card into the
    # quilt (retired 2026-08-11). The card had NEVER rendered on any SS
    # build: their PatchType is a closed enum and unknown types are
    # dropped at decode, before any filter. The upsell rides the
    # gate/teaser lane instead, rendered client-side from the served
    # cta_strings in feature_definitions.context_quilt.

    await db.commit()
    return {"status": "queued"}


# --- Quilt management ---


@router.get("/quilt/{user_id}")
async def get_quilt(
    user_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: fetch user's quilt patches from Context Quilt.

    Pure passthrough: CQ's body reaches the client unmodified. Until
    2026-08-11 this route appended a synthetic fact-shaped upsell card
    (category "cta", metadata.is_synthetic) for free users with a
    pending CTA stamp. SS's decode audit showed the card had NEVER
    rendered on any build: their PatchType is a closed enum, and a patch
    with an unknown patch_type fails item decode and is dropped, logged,
    before any rendering code runs. A synthetic object impersonating
    memory data in a data array is also the pattern SS keeps rooting out
    of prompts, and the same instinct applies to arrays. The free-tier
    Memory upsell rides the gate/teaser lane with served copy instead
    (cta_strings in feature_definitions.context_quilt, all locales).
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    # Forward the device's query string verbatim — iOS sends
    # ?since=...&delta=true for delta sync; dropping it made every poll
    # return the full quilt ("+860 updated" on each fetch).
    return await _cq_proxy("GET", f"/v1/quilt/{_subj(request, user_id)}", query=request.url.query or None)


@router.get("/quilt/{user_id}/insights")
async def get_quilt_insights(
    user_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: fetch the user's memory insights (design 10c Memory tab).

    Carried BEFORE CQ's side merges (CQ PR #227), per the standing rule
    uncomplete taught: routes are additive only at the gateway, so GP
    goes first, and a 404 from this path until CQ deploys is upstream's
    answer passing through, not a route-table miss here.

    Response is a small JSON body ({"user_id", "follow_up": {...} or
    null}); CQ owns the shape and it passes through unmodified. Gated
    exactly like the sibling quilt reads: the ownership guard runs
    before any upstream call, and the Memory entitlement stays a render
    boundary, not a read boundary (the quilt accumulates on every tier
    so an upgrade reveals history). Query string forwarded verbatim for
    anything CQ adds later.
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    return await _cq_proxy(
        "GET", f"/v1/quilt/{_subj(request, user_id)}/insights", query=request.url.query or None)


class PatchCreateRequest(BaseModel):
    type: str  # e.g., "person", "fact", "commitment"
    text: str
    owner: str | None = None
    project_id: str | None = None
    connections: list[dict] | None = None  # [{"target_patch_id", "role", "label"}]


@router.post("/quilt/{user_id}/patches")
async def create_quilt_patch(
    request: Request,
    user_id: str,
    body: PatchCreateRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: create a new quilt patch manually."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _cq_proxy("POST", f"/v1/quilt/{_subj(request, user_id)}/patches", payload)


class PatchUpdateRequest(BaseModel):
    fact: str | None = None
    category: str | None = None
    owner: str | None = None
    project_id: str | None = None


@router.patch("/quilt/{user_id}/patches/{patch_id}")
async def update_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    body: PatchUpdateRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: update a quilt patch (text, category, owner, project)."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _cq_proxy("PATCH", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}", payload)


@router.delete("/quilt/{user_id}/patches/{patch_id}")
async def delete_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: delete a quilt patch."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("DELETE", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}")


@router.post("/quilt/{user_id}/patches/{patch_id}/complete")
async def complete_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
    body: dict | None = Body(default=None),
):
    """Proxy: mark a patch completed (SS tap-to-complete).

    Body is an untyped optional dict forwarded verbatim — CQ owns the
    shape. Status codes carry meaning and pass through unchanged:
    200 completed, 400 not a completable type, 404 not found,
    409 already completed (including losing a race to CQ's auto-close).
    The only rewrite in _cq_proxy is CQ's 401 → 502 (server-to-server
    auth failure must not look like a client-token rejection).
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("POST", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}/complete", body)


# --- Ledger triage (2026-08-07, SS Turn 4) ---
#
# SS's ledger flow ends in Done, Still live, or Let it go. `complete` is
# Done and already existed; `vouch` and `shelve` are the other two.
#
# These are real routes with their own ownership guard rather than entries
# on a generic passthrough, which is why CQ flagged them for us while they
# were still a design. Same shape as `complete` throughout: untyped
# optional body forwarded verbatim, CQ owns the contract, status codes pass
# through unchanged so the client sees CQ's answer and not our summary of
# it.


@router.post("/quilt/{user_id}/patches/{patch_id}/uncomplete")
async def uncomplete_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
    body: dict | None = Body(default=None),
):
    """Proxy: undo a completion.

    Missing until 2026-08-10 and found from a device rather than from
    either side's tests. CQ verified it against their own socket, which
    cannot see a route-table miss by construction: their socket answered
    200 while every client got a 404 from us, because we never carried the
    route.

    Worth naming why the additive-vocabulary rule did not cover this. That
    rule works for FIELDS because readers tolerate unknown keys, so a new
    one costs nothing until someone reads it. A ROUTE is the opposite: our
    edge has a table, and a path we do not carry 404s for everyone no
    matter what the origin does. Fields are additive at the reader, routes
    are additive only at the gateway.
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy(
        "POST", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}/uncomplete", body)


@router.post("/quilt/{user_id}/patches/{patch_id}/vouch")
async def vouch_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
    body: dict | None = Body(default=None),
):
    """Proxy: the user says this item is still live.

    "Still live" is not "done" and not "ignore": it is the user asserting an
    item is real and current, which is what stops decay from quietly
    retiring something that still matters."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("POST", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}/vouch", body)


@router.post("/quilt/{user_id}/patches/{patch_id}/shelve")
async def shelve_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
    body: dict | None = Body(default=None),
):
    """Proxy: the user says let this one go.

    Deliberately not a delete. Shelving is reversible (see the DELETE below),
    which is the whole reason it is a separate verb from completion: a user
    dismissing something they were never going to do should not be
    indistinguishable from a user finishing it."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("POST", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}/shelve", body)


@router.delete("/quilt/{user_id}/patches/{patch_id}/shelve")
async def unshelve_quilt_patch(
    request: Request,
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
    body: dict | None = Body(default=None),
):
    """Proxy: undo a shelve."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("DELETE", f"/v1/quilt/{_subj(request, user_id)}/patches/{patch_id}/shelve", body)


# --- Connection management ---


class ConnectionRequest(BaseModel):
    source_patch_id: str
    target_patch_id: str
    # `label`, not `relationship`. CQ's ConnectionCreate has always taken
    # `label` and silently discarded unknown fields, so sending the wrong
    # spelling wrote an unlabelled edge with a 200 back. CQ is changing that
    # to a 422 (2026-08-07), which turns a silent trap into a loud one.
    #
    # It has never fired: zero of 3,382 connections carry a NULL label,
    # because this route has no callers yet. Renamed while that is still
    # true, rather than after a client depends on the wrong name.
    label: str | None = None


@router.post("/quilt/{user_id}/connections")
async def create_connection(
    request: Request,
    user_id: str,
    body: ConnectionRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: create a connection between two patches."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("POST", f"/v1/quilt/{_subj(request, user_id)}/connections", body.model_dump())


@router.delete("/quilt/{user_id}/connections")
async def delete_connection(
    request: Request,
    user_id: str,
    body: ConnectionRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: delete a connection between two patches."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("DELETE", f"/v1/quilt/{_subj(request, user_id)}/connections", body.model_dump())


# --- Origin (meeting / session / note) management ---


class AssignProjectRequest(BaseModel):
    project_id: str
    project: str | None = None  # Display name, optional


@router.post("/origins/{user_id}/{origin_type}/{origin_id}/assign-project")
async def assign_origin_project(
    request: Request,
    user_id: str,
    origin_type: str,
    origin_id: str,
    body: AssignProjectRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: reassign an origin's patches to a different project in Context Quilt.

    An "origin" generalizes CQ v1's input-unit scoping: a meeting, a practice
    session, a typed note, etc. This replaces the old /meetings/... endpoint,
    which is retained below as a deprecated alias.
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's origins")
    payload = {"project_id": body.project_id}
    if body.project is not None:
        payload["project_name"] = body.project
    return await _cq_proxy(
        "POST",
        f"/v1/origins/{_subj(request, user_id)}/{origin_type}/{origin_id}/assign-project",
        payload,
    )


@router.post("/meetings/{user_id}/{meeting_id}/assign-project")
async def assign_meeting_project(
    request: Request,
    user_id: str,
    meeting_id: str,
    body: AssignProjectRequest,
    user: UserRecord = Depends(get_current_user),
):
    """DEPRECATED — use /origins/{user_id}/meeting/{meeting_id}/assign-project.

    Retained for clients that haven't migrated. Translates to the origin-based
    path before forwarding to CQ (the old /v1/meetings endpoint was removed
    server-side in CQ v1).
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's meetings")
    payload = {"project_id": body.project_id}
    if body.project is not None:
        payload["project_name"] = body.project
    return await _cq_proxy(
        "POST",
        f"/v1/origins/{_subj(request, user_id)}/meeting/{meeting_id}/assign-project",
        payload,
    )


# --- Schema discovery ---


@router.get("/schema")
async def get_schema(user: UserRecord = Depends(get_current_user)):
    """Proxy: fetch GP's CQ manifest (types, connection labels, entity types).

    Clients use this to build UI data-driven (e.g., connection picker matrix).
    GP hits CQ's /v1/schema with its own server JWT and returns the result.
    """
    return await _cq_proxy("GET", "/v1/schema")


# --- Speaker rename (post-rename, SS flow: "Speaker 4" → "SriDev") ---


class RenameSpeakerRequest(BaseModel):
    old_name: str
    new_name: str


@router.post("/quilt/{user_id}/rename-speaker")
async def rename_speaker(
    request: Request,
    user_id: str,
    body: RenameSpeakerRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: rename a speaker in a user's quilt (creates the entity + rebuilds Redis index)."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy(
        "POST",
        f"/v1/quilt/{_subj(request, user_id)}/rename-speaker",
        body.model_dump(),
    )


# --- Speaker reassignment (merge speaker labels in specific meetings onto self or another person) ---


class FromLabel(BaseModel):
    label: str
    meeting_id: str


class ReassignSpeakerRequest(BaseModel):
    from_labels: list[FromLabel]
    to_self: bool | None = None
    to_person_id: str | None = None


@router.post("/quilt/{user_id}/reassign-speaker")
async def reassign_speaker(
    request: Request,
    user_id: str,
    body: ReassignSpeakerRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: reassign one or more speaker labels (scoped per-meeting) to the user (self)
    or another person.

    Each from_labels entry is {label, meeting_id} — meeting scoping prevents cross-meeting
    over-reassignment when diarization assigns the same generic label (e.g. "Speaker 3")
    to different real people across sessions.

    Body shape forwarded verbatim to CQ. CQ's response
    `{patches_updated, connections_updated, entities_merged, labels_skipped}` is returned
    unchanged. Validation here ensures exactly one target is set so malformed requests fail
    fast without a CQ round-trip.
    """
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    if not body.from_labels:
        raise HTTPException(status_code=422, detail="from_labels must not be empty")
    if bool(body.to_self) == bool(body.to_person_id):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of to_self=true or to_person_id",
        )
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _cq_proxy(
        "POST",
        f"/v1/quilt/{_subj(request, user_id)}/reassign-speaker",
        payload,
    )


# --- Prewarm ---


@router.post("/quilt/{user_id}/prewarm")
async def prewarm_quilt(
    request: Request,
    user_id: str,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: pre-warm CQ's Redis cache for this user at session start."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    return await _cq_proxy("POST", f"/v1/prewarm?user_id={_subj(request, user_id)}")


# --- Graph visualization ---


@router.get("/quilt/{user_id}/graph")
async def get_quilt_graph(
    user_id: str,
    request: Request,
    format: str = "svg",
    user: UserRecord = Depends(get_current_user),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: fetch user's quilt graph visualization from Context Quilt.

    Sets a 1-hour Cache-Control and a weak ETag based on the content hash so
    clients can issue conditional requests and get a cheap 304 Not Modified.
    """
    request_id = getattr(request.state, "request_id", "unknown")

    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    if format not in ("svg", "png", "html"):
        raise HTTPException(status_code=400, detail="Format must be 'svg', 'png', or 'html'")

    settings = get_settings()
    if not settings.cq_base_url:
        raise HTTPException(status_code=503, detail={
            "code": "service_unavailable",
            "message": "Context Quilt not configured",
            "request_id": request_id,
        })

    try:
        auth_headers = await cq._get_auth_headers()
        async with httpx.AsyncClient(base_url=settings.cq_base_url, timeout=15.0) as client:
            resp = await client.get(
                f"/v1/quilt/{_subj(request, user_id)}/graph",
                params={"format": format},
                headers=auth_headers,
            )
        if resp.status_code != 200:
            try:
                upstream_detail = resp.json().get("detail", resp.text)
            except Exception:
                upstream_detail = resp.text or "Context Quilt error"
            logger.error(
                "quilt_graph_upstream_error",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "upstream_status": resp.status_code,
                    "upstream_detail": str(upstream_detail)[:500],
                },
            )
            raise HTTPException(status_code=resp.status_code, detail={
                "code": "upstream_error",
                "upstream": "cq",
                "message": str(upstream_detail)[:500],
                "request_id": request_id,
            })

        content_types = {"svg": "image/svg+xml", "png": "image/png", "html": "text/html"}
        content_type = content_types.get(format, "application/octet-stream")
        size = len(resp.content)
        logger.info("quilt_graph_proxy", extra={"user_id": user_id, "format": format, "bytes": size})

        # Weak ETag based on content hash — lets clients revalidate cheaply
        etag = f'W/"{hashlib.sha256(resp.content).hexdigest()[:16]}"'

        # Conditional request: client already has this version
        if if_none_match and if_none_match == etag:
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Cache-Control": "private, max-age=3600",
                },
            )

        return Response(
            content=resp.content,
            media_type=content_type,
            headers={
                "Content-Length": str(size),
                "X-Graph-Bytes": str(size),
                "ETag": etag,
                "Cache-Control": "private, max-age=3600",
            },
        )
    except HTTPException:
        raise
    except httpx.TimeoutException:
        logger.error("quilt_graph_timeout", extra={"request_id": request_id, "user_id": user_id})
        await _report_cq_incident(
            request, db, kind="timeout", request_id=request_id,
        )
        raise HTTPException(status_code=504, detail={
            "code": "upstream_timeout",
            "upstream": "cq",
            "message": "Context Quilt timeout",
            "request_id": request_id,
        })
    except Exception as e:
        logger.error("quilt_graph_unreachable", extra={"request_id": request_id, "user_id": user_id, "error": str(e)})
        await _report_cq_incident(
            request, db, kind="unreachable", request_id=request_id,
            error=str(e)[:300],
        )
        raise HTTPException(status_code=502, detail={
            "code": "upstream_unreachable",
            "upstream": "cq",
            "message": f"Context Quilt unreachable: {e}",
            "request_id": request_id,
        })


# --- People (Review's fourth segment) --------------------------------
#
# A Shoulder Surf Person is a projection of a CQ person entity keyed on
# CQ's entity_id, with CQ as the source of truth for identity. GP is the
# gateway: without these routes SS's first People call 404s here, which
# reads on their side as a client bug. CQ flagged that on 2026-08-03.
#
# Query strings are forwarded verbatim (since / confirmed / min_meetings /
# limit ride the list route). `since` is the one worth testing explicitly:
# dropping it does not error, it silently degrades a delta sync into a
# full one, which is the same class of bug that made every quilt poll
# return "+860 updated".
#
# Availability is the `people` entitlement, enabled for every tier today
# (Scott, 2026-08-02). Checked anyway so the dashboard toggle is real
# rather than decorative: flipping the row to disabled has to actually
# close the door, not just hide the tab.

_PEOPLE_FEATURE = "people"


async def _require_people(request: Request, user: UserRecord, user_id: str) -> None:
    """Shared guard: the caller owns this data and the feature is on."""
    if user.id != user_id:
        raise HTTPException(
            status_code=403, detail="Cannot access another user's people")
    from app.services.entitlements import entitlement_state
    configs = getattr(request.app.state, "remote_configs", None)
    if configs is None:
        # Config store unavailable. That is our problem, not the caller's,
        # and People is not a paid gate, so an absent matrix must not read
        # as "disabled" and lock everyone out. Same fail-open reasoning as
        # unrecognized X-App-ID in the config resolver.
        logger.warning("people_entitlement_skipped: remote_configs unavailable")
        return
    # effective_tier, not tier: simulated_tier or tier. Every other
    # entitlement check in this file reads it, and using the raw tier here
    # made People the one feature that ignores admin tier simulation, which
    # is exactly the tool you would reach for to test this gate.
    state = entitlement_state(configs, user.effective_tier, _PEOPLE_FEATURE)
    if state == "disabled":
        raise HTTPException(status_code=403, detail={
            "code": "feature_disabled",
            "feature": _PEOPLE_FEATURE,
            "message": "People is not available on this plan",
        })


@router.get("/people/{user_id}")
async def list_people(
    user_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: list the user's people. Carries since/confirmed/min_meetings/limit."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "GET", f"/v1/people/{_subj(request, user_id)}", query=request.url.query or None)


@router.post("/people/{user_id}/merge")
async def merge_people(
    user_id: str,
    request: Request,
    body: dict,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: merge two people into one entity."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "POST", f"/v1/people/{_subj(request, user_id)}/merge", body=body,
        query=request.url.query or None)


@router.post("/people/{user_id}/keep-separate")
async def keep_people_separate(
    user_id: str,
    request: Request,
    body: dict,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: record that two candidates are NOT the same person."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "POST", f"/v1/people/{_subj(request, user_id)}/keep-separate", body=body,
        query=request.url.query or None)


@router.post("/people/{user_id}/{entity_id}/confirm")
async def confirm_person(
    user_id: str,
    entity_id: str,
    request: Request,
    body: dict | None = None,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: confirm a person's identity."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "POST", f"/v1/people/{_subj(request, user_id)}/{entity_id}/confirm", body=body,
        query=request.url.query or None)


@router.post("/people/{user_id}/{entity_id}/rename")
async def rename_person(
    user_id: str,
    entity_id: str,
    request: Request,
    body: dict | None = None,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: change a person's display name.

    CQ has served this and we did not carry it, which is the uncomplete
    failure shape exactly: every device call 404s here while CQ's own
    socket answers 200. Found while pinning the people surface for
    not-a-person.

    A display-name update, not an identity operation: CQ keeps the
    entity_id, turns the old name into an alias so recall still matches
    it and future transcripts resolve to the same person, rewrites the
    person's active patches to the new name (rides the next delta), and
    vouches for the person, same reasoning as merge. The body is
    {"name": ..., "source": ...} and CQ owns the shape, so it forwards
    verbatim. Status codes pass through unchanged; the one worth knowing
    is 409 NAME_TAKEN, which means the new name already belongs to
    another person. That is a merge question, not something to retry."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "POST", f"/v1/people/{_subj(request, user_id)}/{entity_id}/rename", body=body,
        query=request.url.query or None)


@router.post("/people/{user_id}/{entity_id}/not-a-person")
async def mark_not_a_person(
    user_id: str,
    entity_id: str,
    request: Request,
    body: dict | None = None,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: mark an entity as not a person (ASR-garbage suppression).

    CQ archives the person patch if one exists, drops the entity from
    /v1/people and the recall entity index, and records a suppression
    against the surface form so the next transcript's diarization cannot
    mint the same garbage name back into the roster. Body is optional and
    forwarded verbatim; CQ owns the shape."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "POST", f"/v1/people/{_subj(request, user_id)}/{entity_id}/not-a-person", body=body,
        query=request.url.query or None)


@router.delete("/people/{user_id}/{entity_id}/not-a-person")
async def unmark_not_a_person(
    user_id: str,
    entity_id: str,
    request: Request,
    body: dict | None = None,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: lift a not-a-person suppression.

    Reversible on purpose: ASR garbage and a real person with an
    unfortunate transcription can collide, and an unfixable wrong answer
    is worse than a reversible one. The undo is a DELETE on the same
    path, same shape as shelve/unshelve, so a lift can never be mistaken
    for a repeat suppression."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "DELETE", f"/v1/people/{_subj(request, user_id)}/{entity_id}/not-a-person", body=body,
        query=request.url.query or None)


@router.post("/people/{user_id}")
async def create_person(
    user_id: str,
    request: Request,
    body: dict,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: create a person entity."""
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "POST", f"/v1/people/{_subj(request, user_id)}", body=body,
        query=request.url.query or None)


@router.get("/people/{user_id}/network")
async def get_people_network(
    user_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: fetch the user's relationship network graph (design 13b).

    Carried BEFORE CQ's side deploys (their PR is open, not yet merged),
    per the standing rule uncomplete taught: routes are additive only at
    the gateway, so GP goes first, and a 404 from this path until CQ
    ships is upstream's answer passing through, not a route-table miss
    here.

    Response is a JSON envelope ({"version", "computed_at", "caps",
    "nodes", "edges", "clusters", "positions"}); CQ owns the shape and
    it passes through unmodified. computed_at can be an explicit null
    (not yet computed), which is a real answer, never a key to strip.
    Gated exactly like the sibling people reads: _require_people runs
    before any upstream call, same free People lane, no new entitlement
    mapping. The route takes no query parameters and forwards none.

    Declared before GET /people/{user_id}/{entity_id} so `network` is a
    literal segment, not an entity_id. Today both would build the same
    upstream path by accident; the ordering keeps that an intent, not a
    coincidence, when either route changes.
    """
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "GET", f"/v1/people/{_subj(request, user_id)}/network")


@router.get("/people/{user_id}/{entity_id}")
async def get_person(
    user_id: str,
    entity_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Proxy: fetch one person, with the meetings/commitments attached.

    Declared after the literal-segment POST routes above so `merge` and
    `keep-separate` are never swallowed by `{entity_id}`. They are POSTs
    and this is a GET, so there is no live conflict today; the ordering is
    insurance against someone adding a GET /merge later.
    """
    await _require_people(request, user, user_id)
    return await _cq_proxy(
        "GET", f"/v1/people/{_subj(request, user_id)}/{entity_id}",
        query=request.url.query or None)
