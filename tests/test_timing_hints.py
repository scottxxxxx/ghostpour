"""GET /v1/timing-hints — per-call_type expected-duration hints.

Powers honest, expectation-shaped progress UI. Asserts the percentile shape,
the min-sample floor, per-app scoping, that no per-model timing leaks, and
auth.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def _clear_hints_cache():
    """The hints cache is keyed only by app_id, so it would bleed a prior
    test's DB into the next. Clear it around every test."""
    from app.routers.chat import _timing_hints_cache

    _timing_hints_cache.clear()
    yield
    _timing_hints_cache.clear()


def _insert_usage(
    db_path: str,
    user_id: str,
    app_id: str,
    call_type: str,
    response_time_ms: int,
    *,
    output_tokens: int = 3000,
    status: str = "success",
    model: str = "claude-sonnet-4-6",
):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, call_type, app_id)
           VALUES (?, ?, 'anthropic', ?, 100, ?, 0.01, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            model,
            output_tokens,
            now,
            response_time_ms,
            status,
            call_type,
            app_id,
        ),
    )
    conn.commit()
    conn.close()


def test_timing_hints_percentile_shape(client, tmp_db_path, pro_user):
    uid = pro_user["user_id"]
    # 10 known samples: 1000..10000ms, output 3000 tokens each.
    for ms in range(1000, 10001, 1000):
        _insert_usage(tmp_db_path, uid, "techrehearsal", "tr_parse_jd", ms)

    resp = client.get(
        "/v1/timing-hints",
        headers={**pro_user["headers"], "X-App-ID": "techrehearsal"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["window_days"] == 30
    h = data["hints"]["tr_parse_jd"]
    assert h["samples"] == 10
    # nearest-rank: p50 -> idx int(10*0.5)=5 -> 6000; p90 -> idx 9 -> 10000.
    assert h["p50_ms"] == 6000
    assert h["p90_ms"] == 10000
    assert h["p50_ms"] <= h["p90_ms"]
    assert h["p50_output_tokens"] == 3000


def test_timing_hints_min_sample_floor(client, tmp_db_path, pro_user):
    uid = pro_user["user_id"]
    # Only 3 samples — below the floor — so the call_type is omitted.
    for ms in (1000, 2000, 3000):
        _insert_usage(tmp_db_path, uid, "techrehearsal", "tr_parse_resume", ms)

    data = client.get(
        "/v1/timing-hints",
        headers={**pro_user["headers"], "X-App-ID": "techrehearsal"},
    ).json()
    assert "tr_parse_resume" not in data["hints"]


def test_timing_hints_scoped_to_caller_app(client, tmp_db_path, pro_user):
    uid = pro_user["user_id"]
    for ms in range(1000, 6001, 1000):  # 6 TR rows
        _insert_usage(tmp_db_path, uid, "techrehearsal", "tr_parse_jd", ms)
    for ms in range(1000, 6001, 1000):  # 6 SS rows
        _insert_usage(tmp_db_path, uid, "shouldersurf", "summary", ms)

    tr = client.get(
        "/v1/timing-hints",
        headers={**pro_user["headers"], "X-App-ID": "techrehearsal"},
    ).json()
    assert "tr_parse_jd" in tr["hints"]
    assert "summary" not in tr["hints"]  # other app's call_type stays out


def test_timing_hints_never_leaks_per_model(client, tmp_db_path, pro_user):
    uid = pro_user["user_id"]
    # Same call_type served by two different models; the hint must be a
    # single aggregate with no model dimension anywhere in the payload.
    for ms in range(1000, 4001, 1000):
        _insert_usage(tmp_db_path, uid, "techrehearsal", "tr_parse_jd", ms,
                      model="claude-sonnet-4-6")
    for ms in range(1000, 4001, 1000):
        _insert_usage(tmp_db_path, uid, "techrehearsal", "tr_parse_jd", ms,
                      model="claude-haiku-4-5")

    resp = client.get(
        "/v1/timing-hints",
        headers={**pro_user["headers"], "X-App-ID": "techrehearsal"},
    )
    assert "model" not in resp.text.lower()
    h = resp.json()["hints"]["tr_parse_jd"]
    assert set(h.keys()) == {"p50_ms", "p90_ms", "p50_output_tokens", "samples"}
    assert h["samples"] == 8  # both models folded into one bucket


def test_timing_hints_requires_auth(client):
    resp = client.get("/v1/timing-hints", headers={"X-App-ID": "techrehearsal"})
    assert resp.status_code in (401, 403)
