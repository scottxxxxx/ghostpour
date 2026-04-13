"""Context Quilt proxy endpoints.

These endpoints proxy requests from the client app to the Context Quilt
service. They are conditionally included in main.py only when CZ_CQ_BASE_URL
is configured. Apps that don't use Context Quilt won't have these routes.
"""

import asyncio
import hashlib
import logging

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import context_quilt as cq

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Proxy helper ---


async def _cq_proxy(method: str, path: str, body: dict | None = None) -> JSONResponse:
    """Forward a request to Context Quilt and return its response."""
    settings = get_settings()
    if not settings.cq_base_url:
        raise HTTPException(status_code=503, detail="Context Quilt not configured")

    try:
        auth_headers = await cq._get_auth_headers()
        async with httpx.AsyncClient(base_url=settings.cq_base_url, timeout=10.0) as client:
            resp = await client.request(
                method,
                path,
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


class TranscriptCaptureRequest(BaseModel):
    transcript: str
    meeting_id: str | None = None
    project: str | None = None
    project_id: str | None = None


@router.post("/capture-transcript")
async def capture_transcript(
    body: TranscriptCaptureRequest,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    End-of-session transcript capture for Context Quilt + local storage.

    Called by the client app at session end to send the full raw transcript.
    CQ extracts traits, preferences, and durable facts from the raw dialogue.
    GP also stores the transcript locally for meeting report generation.
    """
    # Store transcript locally for report generation
    if body.meeting_id:
        from datetime import datetime, timezone
        import uuid
        await db.execute(
            """INSERT OR REPLACE INTO meeting_transcripts
               (id, user_id, meeting_id, transcript, project, project_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                user.id,
                body.meeting_id,
                body.transcript,
                body.project,
                body.project_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    # Forward to CQ for knowledge extraction
    asyncio.create_task(cq.capture(
        user_id=user.id,
        interaction_type="meeting_transcript",
        content=body.transcript,
        meeting_id=body.meeting_id,
        project=body.project,
        project_id=body.project_id,
        display_name=user.display_name,
        email=user.email,
    ))
    return {"status": "queued"}


# --- Quilt management ---


@router.get("/quilt/{user_id}")
async def get_quilt(
    user_id: str,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: fetch user's quilt patches from Context Quilt."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    return await _cq_proxy("GET", f"/v1/quilt/{user_id}")


class PatchCreateRequest(BaseModel):
    type: str  # e.g., "person", "fact", "commitment"
    text: str
    owner: str | None = None
    project_id: str | None = None
    connections: list[dict] | None = None  # [{"target_patch_id", "role", "label"}]


@router.post("/quilt/{user_id}/patches")
async def create_quilt_patch(
    user_id: str,
    body: PatchCreateRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: create a new quilt patch manually."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _cq_proxy("POST", f"/v1/quilt/{user_id}/patches", payload)


class PatchUpdateRequest(BaseModel):
    fact: str | None = None
    category: str | None = None
    owner: str | None = None
    project_id: str | None = None


@router.patch("/quilt/{user_id}/patches/{patch_id}")
async def update_quilt_patch(
    user_id: str,
    patch_id: str,
    body: PatchUpdateRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: update a quilt patch (text, category, owner, project)."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _cq_proxy("PATCH", f"/v1/quilt/{user_id}/patches/{patch_id}", payload)


@router.delete("/quilt/{user_id}/patches/{patch_id}")
async def delete_quilt_patch(
    user_id: str,
    patch_id: str,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: delete a quilt patch."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("DELETE", f"/v1/quilt/{user_id}/patches/{patch_id}")


# --- Connection management ---


class ConnectionRequest(BaseModel):
    source_patch_id: str
    target_patch_id: str
    relationship: str | None = None


@router.post("/quilt/{user_id}/connections")
async def create_connection(
    user_id: str,
    body: ConnectionRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: create a connection between two patches."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("POST", f"/v1/quilt/{user_id}/connections", body.model_dump())


@router.delete("/quilt/{user_id}/connections")
async def delete_connection(
    user_id: str,
    body: ConnectionRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: delete a connection between two patches."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's quilt")
    return await _cq_proxy("DELETE", f"/v1/quilt/{user_id}/connections", body.model_dump())


# --- Meeting management ---


class AssignProjectRequest(BaseModel):
    project_id: str
    project: str | None = None  # Display name, optional


@router.post("/meetings/{user_id}/{meeting_id}/assign-project")
async def assign_meeting_project(
    user_id: str,
    meeting_id: str,
    body: AssignProjectRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: reassign a meeting's patches to a different project in Context Quilt."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify another user's meetings")
    payload = {"project_id": body.project_id}
    if body.project is not None:
        payload["project_name"] = body.project
    return await _cq_proxy(
        "POST",
        f"/v1/meetings/{user_id}/{meeting_id}/assign-project",
        payload,
    )


# --- Prewarm ---


@router.post("/quilt/{user_id}/prewarm")
async def prewarm_quilt(
    user_id: str,
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: pre-warm CQ's Redis cache for this user at session start."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    return await _cq_proxy("POST", f"/v1/prewarm?user_id={user_id}")


# --- Graph visualization ---


@router.get("/quilt/{user_id}/graph")
async def get_quilt_graph(
    user_id: str,
    request: Request,
    format: str = "svg",
    user: UserRecord = Depends(get_current_user),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
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
                f"/v1/quilt/{user_id}/graph",
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
        raise HTTPException(status_code=504, detail={
            "code": "upstream_timeout",
            "upstream": "cq",
            "message": "Context Quilt timeout",
            "request_id": request_id,
        })
    except Exception as e:
        logger.error("quilt_graph_unreachable", extra={"request_id": request_id, "user_id": user_id, "error": str(e)})
        raise HTTPException(status_code=502, detail={
            "code": "upstream_unreachable",
            "upstream": "cq",
            "message": f"Context Quilt unreachable: {e}",
            "request_id": request_id,
        })
