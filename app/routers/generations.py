"""Rescue lookup for generation turns (phase 2, handoff Part 4).

GET /v1/generations/{generation_id} — bearer-authenticated, owner-only.
running → honest-progress fields (a relaunched client resumes the TRUE
elapsed time); done → the whole turn (text + generated_files, same entry
shape as the live response); failed → the stored error. Never-arrived,
expired, not-yours, and lost-to-restart are one indistinguishable 404 —
the client's regenerate card is the truthful recovery for all of them.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import generation_turns

router = APIRouter()


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
