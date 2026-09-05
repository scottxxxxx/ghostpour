"""Rescue lookup for generation turns (phase 2, handoff Part 4).

GET /v1/generations/{generation_id} — bearer-authenticated, owner-only.
running → honest-progress fields (a relaunched client resumes the TRUE
elapsed time); done → the whole turn (text + generated_files, same entry
shape as the live response); failed → the stored error. Never-arrived,
expired, not-yours, and lost-to-restart are one indistinguishable 404 —
the client's regenerate card is the truthful recovery for all of them.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import generation_turns

router = APIRouter()


@router.get("/generations")
async def list_generations(
    project_id: str | None = Query(default=None),
    meeting_id: str | None = Query(default=None),
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Discovery (SS contract 2026-09-05): every non-expired turn of the
    caller in a project or meeting scope, newest first, each entry in the
    single GET's shape plus where it belongs and acked_at. Empty list for
    a scope with nothing, never 404. At least one scope is required so a
    bare call cannot list everything."""
    if not project_id and not meeting_id:
        raise HTTPException(status_code=400, detail="project_id or meeting_id is required")
    entries = await generation_turns.list_for_scope(
        db, user.id, project_id=project_id or None, meeting_id=meeting_id or None)
    return JSONResponse({"generations": entries}, headers={"Cache-Control": "private, no-store"})


@router.post("/generations/{generation_id}/ack")
async def ack_generation(
    generation_id: str,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Mark a presented turn so it is not listed as new again. Idempotent:
    a second ack returns the same acked_at. 404 for anything that is not
    the caller's live terminal row."""
    entry = await generation_turns.ack(db, user.id, generation_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(entry, headers={"Cache-Control": "private, no-store"})


@router.get("/generations/{generation_id}")
async def lookup_generation(
    generation_id: str,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    running = generation_turns.running_info(user.id, generation_id)
    if running is not None:
        return JSONResponse(running, headers={"Cache-Control": "private, no-store"})
    terminal = await generation_turns.lookup_terminal(db, user.id, generation_id)
    if terminal is not None:
        return JSONResponse(terminal, headers={"Cache-Control": "private, no-store"})
    raise HTTPException(status_code=404, detail="not found")
