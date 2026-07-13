"""Serve staged generated documents (phase 2a).

GET /v1/generated-files/{file_id} — bearer-authenticated, owner-only. The
URL is a fetch window, not storage: the client downloads on response-land
and persists its own copy, so missing / expired / not-yours are one
indistinguishable 404.
"""

from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserRecord
from app.services import generated_files

router = APIRouter()


@router.get("/generated-files/{file_id}")
async def serve_generated_file(
    file_id: str,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await generated_files.fetch(db, file_id, user.id)
    if row is None or not Path(row["storage_path"]).exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        row["storage_path"],
        media_type=row["media_type"],
        filename=row["name"],
        headers={"Cache-Control": "private, no-store"},
    )
