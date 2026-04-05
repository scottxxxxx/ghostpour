"""Context Quilt proxy endpoints.

These endpoints proxy requests from the client app to the Context Quilt
service. They are conditionally included in main.py only when CZ_CQ_BASE_URL
is configured. Apps that don't use Context Quilt won't have these routes.
"""

import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.config import get_settings
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
):
    """
    End-of-session transcript capture for Context Quilt.

    Called by the client app at session end to send the full raw transcript.
    CQ extracts traits, preferences, and durable facts from the raw dialogue
    that would otherwise be lost in per-query summarization.
    """
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
    format: str = "svg",
    user: UserRecord = Depends(get_current_user),
):
    """Proxy: fetch user's quilt graph visualization from Context Quilt."""
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's quilt")
    if format not in ("svg", "png", "html"):
        raise HTTPException(status_code=400, detail="Format must be 'svg', 'png', or 'html'")

    settings = get_settings()
    if not settings.cq_base_url:
        raise HTTPException(status_code=503, detail="Context Quilt not configured")

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
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text or "Context Quilt error"
            raise HTTPException(status_code=resp.status_code, detail=detail)

        content_types = {"svg": "image/svg+xml", "png": "image/png", "html": "text/html"}
        content_type = content_types.get(format, "application/octet-stream")
        size = len(resp.content)
        logger.info("quilt_graph_proxy", extra={"user_id": user_id, "format": format, "bytes": size})
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={"Content-Length": str(size), "X-Graph-Bytes": str(size)},
        )
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Context Quilt timeout")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Context Quilt unreachable: {e}")
