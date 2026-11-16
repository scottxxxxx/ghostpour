"""A dated hold keeps one app's usage_log rows whole past both retention
windows (Scott, 2026-11-16: Steven's N-400 run is the only N-400 session from
a production install). Rows outside the window, or of another app, still go."""
import json

import pytest

from app.services import retention
from tests.test_usage_log_expiry import ANON, _ids, _iso, _row, db  # noqa: F401


def _hold(lo, hi, app="n400"):
    return ((app, _iso(hi)[:19], _iso(lo)[:19]),)


@pytest.mark.asyncio
async def test_held_rows_are_not_expired_and_not_carried(db):  # noqa: F811
    await _row(db, "held", ANON, "n400", 100, 2.0)
    await _row(db, "gone", ANON, "n400", 130, 1.0)
    await _row(db, "other_app", ANON, "i765", 100, 1.0)
    await db.commit()
    n = await retention.expire_old_usage_rows(db, holds=_hold(95, 105))
    assert n == 2 and await _ids(db) == {"held"}
    carry = (await (await db.execute(
        "SELECT lifetime_usd FROM anonymous_spend_carry WHERE app_id = 'n400'")).fetchone())[0]
    assert carry == pytest.approx(1.0)  # only the expired row, never the held one


def test_the_steven_hold_is_configured():
    assert ("n400", "2026-11-09T00:00:00", "2026-11-17T00:00:00") in retention.HOLD_USAGE_WINDOWS


@pytest.mark.asyncio
async def test_held_rows_keep_raw_request(db):  # noqa: F811
    meta = json.dumps({"raw_request": {"x": 1}, "raw_response": "r"})
    for rid, days in (("held", 40), ("stripped", 60)):
        await _row(db, rid, ANON, "n400", days, 0.1)
        await db.execute("UPDATE usage_log SET metadata = ? WHERE id = ?", (meta, rid))
    await db.commit()
    await retention.strip_old_raw_requests(db, holds=_hold(35, 45))
    got = {r[0]: json.loads(r[1]) for r in await (await db.execute(
        "SELECT id, metadata FROM usage_log")).fetchall()}
    assert "raw_request" in got["held"] and "raw_request" not in got["stripped"]
