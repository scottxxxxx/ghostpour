"""usage_log.generated_count and documents_count, written by the real writer.

The admin users list sums these two counts per user (2026-11-01). Until
then it dug them out of metadata with json_extract, four passes over a
24 KB raw_request blob per row. They are plain columns now, so the roll-up
can run inside idx_usage_user_rollup, and that only holds if the ONE writer
of usage_log rows (UsageTracker.log_usage) sets them from the same dict
that becomes metadata. A column the writer forgot would read as 0 on the
dashboard with no error anywhere, which is the failure shape CLAUDE.md rule
8 describes, so this test inserts through log_usage itself and reads the
columns back next to the JSON they must agree with.
"""

import asyncio
import json
import sqlite3

import aiosqlite

from app.models.chat import ChatRequest, ChatResponse
from app.services.usage_tracker import UsageTracker
from tests.conftest import _insert_user, chat_request


def _log(db_path, user_id, request_meta, response):
    async def go():
        async with aiosqlite.connect(db_path) as db:
            await UsageTracker().log_usage(
                db, user_id, ChatRequest(**chat_request(metadata=request_meta)),
                response, response_time_ms=120, app_id="shouldersurf")
            await db.commit()
    asyncio.run(go())


def _rows(db_path, user_id):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT generated_count, documents_count, metadata FROM usage_log "
        "WHERE user_id = ? ORDER BY request_timestamp", (user_id,)).fetchall()
    conn.close()
    return rows


def test_log_usage_writes_the_counts_beside_metadata(client, tmp_db_path):
    _insert_user(tmp_db_path, user_id="writer-user")
    response = ChatResponse(text="ok", input_tokens=10, output_tokens=5,
                            model="m", provider="anthropic",
                            usage={"input_tokens": 10, "output_tokens": 5})

    # Both counts present: the columns equal the JSON.
    _log(tmp_db_path, "writer-user",
         {"generated_count": 3, "generated_bytes": 900,
          "document_count": 2, "document_bytes": 4000}, response)
    # Neither present but metadata exists (usage dict): 0, not NULL, so
    # the one-time backfill's `IS NULL` guard never sees this row again.
    _log(tmp_db_path, "writer-user", None, response)
    # No response, no meta: no metadata at all, and the columns are NULL
    # exactly as the backfill leaves such rows.
    _log(tmp_db_path, "writer-user", None, None)

    rows = _rows(tmp_db_path, "writer-user")
    assert [(g, d) for g, d, _ in rows] == [(3, 2), (0, 0), (None, None)]

    meta = json.loads(rows[0][2])
    assert meta["generated"]["count"] == rows[0][0]
    assert meta["documents"]["count"] == rows[0][1]
    assert "generated" not in json.loads(rows[1][2])
    assert rows[2][2] is None
