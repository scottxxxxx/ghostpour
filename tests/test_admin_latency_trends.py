"""The Performance tab: latency and first-token time over time, by model and
by query type.

Scott, 2026-09-13: "there is a simple view of P99 P95 etc. I want to see our
responses historically and be able to spot trends." Then 2026-09-14: "the
first token time, also shown as a graph, ideally incorporated in the one
showing the total response time over a period of days." /admin/latency-trends
buckets by hour or day; usage_log.ttft_ms exists on streaming calls only
(about 13% of rows) and is NULL everywhere else, so every first-token figure
is computed over the rows that have one and served with a coverage that
counts the NULL rows in its denominator.

Backend half: bucketing, exact nearest-rank percentiles on a known fixture,
error rows out of latency but in error_rate, app and model scoping, zero
guards, the breakdown split, the TTFT and generation fields, the `previous`
window, model shares and per-row trends. Frontend half: the page's series
builders, delta and caption evaluated in node, because a source-text grep
would pass just as happily if the chart plotted p95 where p99 was asked for
or a 0 where a bucket had no first-token time.
"""

import inspect
import json
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest

ADMIN = {"X-Admin-Key": "test-admin-key"}
URL = "/webhooks/admin/latency-trends"
HTML = "app/static/admin.html"


def _insert(db_path, user_id, *, ts, ms, status="success", model="model-a",
            call_type="chat", app_id="shouldersurf", input_tokens=100,
            output_tokens=50, cached_tokens=0, cost=0.01, ttft_ms=None):
    # ttft_ms defaults to None because that is what every non-streaming row
    # carries in production; a test that wants a streaming row says so.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, error_message, call_type, cached_tokens, app_id, ttft_ms)
           VALUES (?, ?, 'anthropic', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, model, input_tokens, output_tokens, cost,
         ts.isoformat(), ms, status, "boom" if status != "success" else None,
         call_type, cached_tokens, app_id, ttft_ms),
    )
    conn.commit()
    conn.close()


def _user(tmp_db_path, uid="lat-user"):
    from tests.conftest import _insert_user
    _insert_user(tmp_db_path, user_id=uid, tier="pro", monthly_limit=5.10)
    return uid


def _get(client, q=""):
    r = client.get(f"{URL}?{q}", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _bucket(payload, key):
    return next(b for b in payload["buckets"] if b["bucket"] == key)


def _now():
    # Two minutes back so a row never lands in an hour bucket the generated
    # range has not reached yet on a slow test box.
    return datetime.now(timezone.utc) - timedelta(minutes=2)


# ── bucketing ─────────────────────────────────────────────────────────────

def test_hour_buckets_put_each_row_in_its_own_hour(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    t1, t2 = now - timedelta(hours=3), now - timedelta(hours=1)
    for _ in range(2):
        _insert(tmp_db_path, uid, ts=t1, ms=100)
    _insert(tmp_db_path, uid, ts=t2, ms=100)

    p = _get(client, "days=7&bucket=hour")
    k1 = t1.isoformat()[:13] + ":00:00"
    k2 = t2.isoformat()[:13] + ":00:00"
    assert _bucket(p, k1)["n"] == 2
    assert _bucket(p, k2)["n"] == 1
    assert sum(b["n"] for b in p["buckets"]) == 3
    # Every hour of the window is present, quiet ones as n=0, so the axis is
    # time and not "hours that happened to have a call".
    assert len(p["buckets"]) >= 7 * 24
    assert all(b["n"] == 0 for b in p["buckets"] if b["bucket"] not in (k1, k2))


def test_day_buckets_split_yesterday_from_today(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    yday = now - timedelta(days=1)
    _insert(tmp_db_path, uid, ts=yday, ms=100)
    _insert(tmp_db_path, uid, ts=now, ms=100)
    _insert(tmp_db_path, uid, ts=now, ms=100)

    p = _get(client, "days=7&bucket=day")
    assert _bucket(p, yday.isoformat()[:10])["n"] == 1
    assert _bucket(p, now.isoformat()[:10])["n"] == 2
    # Midnight `days` ago through today inclusive.
    assert len(p["buckets"]) == 8
    assert p["bucket"] == "day"


def test_rows_outside_the_window_are_not_bucketed(client, tmp_db_path):
    uid = _user(tmp_db_path)
    _insert(tmp_db_path, uid, ts=_now() - timedelta(days=20), ms=100)
    _insert(tmp_db_path, uid, ts=_now(), ms=100)
    assert sum(b["n"] for b in _get(client, "days=7")["buckets"]) == 1
    assert sum(b["n"] for b in _get(client, "days=30")["buckets"]) == 2


# ── percentiles ───────────────────────────────────────────────────────────

def test_nearest_rank_percentiles_are_exact_on_ten_rows(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    # Inserted out of order on purpose: the sort is the endpoint's job.
    for ms in (700, 100, 1000, 300, 500, 900, 200, 800, 400, 600):
        _insert(tmp_db_path, uid, ts=now, ms=ms, output_tokens=55, cost=0.02)
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["n"] == 10
    assert b["p50"] == 500     # rank ceil(5.0) = 5th smallest
    assert b["p95"] == 1000    # rank ceil(9.5) = 10th
    assert b["p99"] == 1000    # rank ceil(9.9) = 10th
    assert b["mean"] == 550
    assert b["max"] == 1000
    assert b["cost_per_call"] == pytest.approx(0.02)
    # 550 output tokens over 5500 ms of wall clock.
    assert b["tokens_per_second"] == pytest.approx(100.0)


def test_p95_and_p99_differ_once_there_are_twenty_rows(client, tmp_db_path):
    # On 10 rows p95 and p99 are the same number, so the ten-row fixture
    # alone could not tell a p95/p99 swap apart. Twenty rows can.
    uid = _user(tmp_db_path)
    now = _now()
    for ms in range(100, 2100, 100):
        _insert(tmp_db_path, uid, ts=now, ms=ms)
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["p50"] == 1000    # rank 10
    assert b["p95"] == 1900    # rank 19
    assert b["p99"] == 2000    # rank ceil(19.8) = 20


def test_the_method_is_stated_in_the_payload(client, tmp_db_path):
    assert _get(client, "days=7")["percentile_method"] == "nearest-rank"


# ── error rows ────────────────────────────────────────────────────────────

def test_error_rows_leave_latency_alone_but_count_in_error_rate(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    for ms in (100, 200, 300, 400):
        _insert(tmp_db_path, uid, ts=now, ms=ms)
    # A slow failure: the time to fail is not latency.
    _insert(tmp_db_path, uid, ts=now, ms=99999, status="error")
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["n"] == 4
    assert b["total"] == 5
    assert b["errors"] == 1
    assert b["max"] == 400
    assert b["p99"] == 400
    assert b["error_rate"] == pytest.approx(0.2)


def test_an_all_error_bucket_reports_itself_instead_of_vanishing(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=5000, status="error")
    _insert(tmp_db_path, uid, ts=now, ms=5000, status="rate_limited")
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["n"] == 0
    assert b["p50"] is None and b["p99"] is None and b["max"] is None
    assert b["error_rate"] == pytest.approx(1.0)


# ── scoping ───────────────────────────────────────────────────────────────

def test_the_app_filter_scopes_rows(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    for _ in range(3):
        _insert(tmp_db_path, uid, ts=now, ms=100, app_id="shouldersurf")
    for _ in range(2):
        _insert(tmp_db_path, uid, ts=now, ms=100, app_id="techrehearsal")

    def _calls(q):
        return sum(b["n"] for b in _get(client, "days=7" + q)["buckets"])

    assert _calls("") == 5
    assert _calls("&app=techrehearsal") == 2
    assert _calls("&app=shouldersurf") == 3
    # The union, not the total: one filtered multi-app view.
    assert _calls("&app=shouldersurf,techrehearsal") == 5
    # The breakdown is scoped the same way.
    bd = _get(client, "days=7&app=techrehearsal")["breakdown"]
    assert [m["n"] for m in bd["by_model"]] == [2]


def test_the_model_filter_scopes_the_series_and_the_call_type_split(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    for ms in (100, 200, 300):
        _insert(tmp_db_path, uid, ts=now, ms=ms, model="model-a", call_type="chat")
    for ms in (5000, 6000):
        _insert(tmp_db_path, uid, ts=now, ms=ms, model="model-b", call_type="report")

    p = _get(client, "days=7&bucket=day&model=model-a")
    b = _bucket(p, now.isoformat()[:10])
    assert b["n"] == 3 and b["max"] == 300
    assert p["filters"]["model"] == "model-a"
    # by_call_type honours the model filter: only model-a's lane appears.
    assert [c["call_type"] for c in p["breakdown"]["by_call_type"]] == ["chat"]
    # by_model does NOT honour its own filter, so the dropdown still lists
    # every model in the window.
    assert sorted(m["model"] for m in p["breakdown"]["by_model"]) == ["model-a", "model-b"]


def test_the_call_type_filter_scopes_the_series_and_the_model_split(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=100, model="model-a", call_type="chat")
    _insert(tmp_db_path, uid, ts=now, ms=9000, model="model-b", call_type="report")
    p = _get(client, "days=7&bucket=day&call_type=report")
    assert _bucket(p, now.isoformat()[:10])["p50"] == 9000
    assert [m["model"] for m in p["breakdown"]["by_model"]] == ["model-b"]
    assert sorted(c["call_type"] for c in p["breakdown"]["by_call_type"]) == ["chat", "report"]


# ── zero guards ───────────────────────────────────────────────────────────

def test_zero_tokens_and_zero_time_do_not_divide(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=0, input_tokens=0, output_tokens=0, cached_tokens=0)
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["n"] == 1
    assert b["tokens_per_second"] is None
    assert b["cache_hit_ratio"] is None
    assert b["p50"] == 0


def test_cache_hit_ratio_is_cached_over_input(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=100, input_tokens=300, cached_tokens=100)
    _insert(tmp_db_path, uid, ts=now, ms=100, input_tokens=100, cached_tokens=0)
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["cache_hit_ratio"] == pytest.approx(0.25)


def test_an_empty_window_is_all_quiet_buckets_not_an_error(client, tmp_db_path):
    p = _get(client, "days=7&bucket=day")
    assert len(p["buckets"]) == 8
    assert all(b["n"] == 0 and b["error_rate"] is None for b in p["buckets"])
    assert p["breakdown"] == {"by_model": [], "by_call_type": []}


# ── breakdown ─────────────────────────────────────────────────────────────

def test_the_breakdown_splits_by_model_and_by_call_type(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=100, model="model-a", call_type="chat")
    _insert(tmp_db_path, uid, ts=now, ms=200, model="model-a", call_type="report")
    _insert(tmp_db_path, uid, ts=now, ms=300, model="model-b", call_type="chat")
    _insert(tmp_db_path, uid, ts=now, ms=400, model="model-b", call_type="chat", status="error")
    bd = _get(client, "days=7")["breakdown"]
    by_model = {m["model"]: m for m in bd["by_model"]}
    by_ct = {c["call_type"]: c for c in bd["by_call_type"]}
    assert by_model["model-a"]["n"] == 2 and by_model["model-a"]["p99"] == 200
    assert by_model["model-b"]["n"] == 1 and by_model["model-b"]["error_rate"] == pytest.approx(0.5)
    assert by_ct["chat"]["n"] == 2 and by_ct["chat"]["p50"] == 100 and by_ct["chat"]["total"] == 3
    assert by_ct["report"]["n"] == 1 and by_ct["report"]["p50"] == 200
    # Busiest first, so the table opens on the lane that matters.
    assert bd["by_call_type"][0]["call_type"] == "chat"


def test_an_untyped_row_is_labelled_not_dropped(client, tmp_db_path):
    uid = _user(tmp_db_path)
    _insert(tmp_db_path, uid, ts=_now(), ms=100, call_type=None)
    bd = _get(client, "days=7")["breakdown"]
    assert [c["call_type"] for c in bd["by_call_type"]] == ["(untyped)"]


# ── contract ──────────────────────────────────────────────────────────────

def test_the_endpoint_needs_the_admin_key(client):
    assert client.get(f"{URL}?days=7", headers={"X-Admin-Key": "nope"}).status_code == 403


def test_an_unknown_bucket_is_a_400(client):
    assert client.get(f"{URL}?days=7&bucket=week", headers=ADMIN).status_code == 400


# ── time to first token ───────────────────────────────────────────────────
# ttft_ms is non-null ONLY on streaming Anthropic calls, about 13% of rows.
# Every figure below is computed over the rows that have one and served
# next to a coverage that counts the NULL rows in its denominator.

def _streaming_day(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    # Three streaming rows and two that never streamed, one day bucket.
    for ms, ttft in ((1000, 200), (2000, 400), (3000, 600), (4000, None), (5000, None)):
        _insert(tmp_db_path, uid, ts=now, ms=ms, ttft_ms=ttft, output_tokens=50)
    return _get(client, "days=7&bucket=day"), now.isoformat()[:10]


def test_ttft_percentiles_run_over_non_null_rows_only(client, tmp_db_path):
    p, day = _streaming_day(client, tmp_db_path)
    b = _bucket(p, day)
    assert b["n"] == 5
    assert b["ttft_n"] == 3
    # rank ceil(0.5 * 3) = 2nd smallest of (200, 400, 600). Had the two
    # NULLs entered the list as zeros it would be 200.
    assert b["ttft_p50"] == 400
    assert b["ttft_p95"] == 600
    assert p["ttft_recorded"] is True


def test_ttft_coverage_counts_the_null_rows_in_its_denominator(client, tmp_db_path):
    p, day = _streaming_day(client, tmp_db_path)
    b = _bucket(p, day)
    # 3 of 5 success rows, not 3 of 3.
    assert b["ttft_coverage"] == pytest.approx(0.6)
    assert b["streaming_share"] == pytest.approx(0.6)
    assert p["current"]["ttft_coverage"] == pytest.approx(0.6)
    assert p["breakdown"]["by_model"][0]["ttft_coverage"] == pytest.approx(0.6)


def test_generation_figures_come_from_the_streaming_rows_alone(client, tmp_db_path):
    p, day = _streaming_day(client, tmp_db_path)
    b = _bucket(p, day)
    # Per-row generation time: 800, 1600, 2400. Nearest rank p50 = 1600.
    assert b["generation_p50_ms"] == 1600
    # p50 of the whole response over the streaming rows: 1000, 2000, 3000.
    assert b["streaming_p50"] == 2000
    # 150 output tokens over 4800 ms of generation.
    assert b["generation_tokens_per_second"] == pytest.approx(31.25)
    # And the total-time figure still covers every success row: 250 tokens
    # over 15000 ms.
    assert b["tokens_per_second"] == pytest.approx(16.67, abs=0.01)


def test_a_median_of_differences_is_not_a_difference_of_medians(client, tmp_db_path):
    # The stacked chart draws streaming_p50 - ttft_p50; the tooltip shows
    # generation_p50_ms. They are different numbers and this fixture keeps
    # them apart so neither can be silently substituted for the other.
    uid = _user(tmp_db_path)
    now = _now()
    for ms, ttft in ((1000, 100), (2000, 900), (3000, 200)):
        _insert(tmp_db_path, uid, ts=now, ms=ms, ttft_ms=ttft)
    b = _bucket(_get(client, "days=7&bucket=day"), now.isoformat()[:10])
    assert b["generation_p50_ms"] == 1100        # of (900, 1100, 2800)
    assert b["streaming_p50"] - b["ttft_p50"] == 1800   # 2000 - 200


def test_no_streaming_rows_means_null_first_token_figures(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    for ms in (100, 200, 300):
        _insert(tmp_db_path, uid, ts=now, ms=ms)
    p = _get(client, "days=7&bucket=day")
    b = _bucket(p, now.isoformat()[:10])
    assert b["n"] == 3 and b["ttft_n"] == 0
    assert b["ttft_coverage"] == 0.0 and b["streaming_share"] == 0.0
    for k in ("ttft_p50", "ttft_p95", "generation_p50_ms",
              "generation_tokens_per_second", "streaming_p50"):
        assert b[k] is None, k
    assert p["ttft_recorded"] is False
    # An empty bucket has no coverage at all, not a coverage of zero.
    empty = next(x for x in p["buckets"] if x["n"] == 0)
    assert empty["ttft_coverage"] is None


def test_ttft_recorded_reads_success_rows_under_the_current_filters(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=1000, ttft_ms=100, model="stream-model", call_type="query")
    _insert(tmp_db_path, uid, ts=now, ms=1000, model="turn-model", call_type="n400_interviewer_turn")
    # A failed stream carries no usable ttft for the aggregates.
    _insert(tmp_db_path, uid, ts=now, ms=1000, ttft_ms=100, status="error", model="turn-model", call_type="n400_interviewer_turn")
    assert _get(client, "days=7")["ttft_recorded"] is True
    assert _get(client, "days=7&model=stream-model")["ttft_recorded"] is True
    # The filtered view has no streaming success row, so the page's empty
    # state is the truth for that view.
    assert _get(client, "days=7&model=turn-model")["ttft_recorded"] is False
    assert _get(client, "days=7&call_type=n400_interviewer_turn")["ttft_recorded"] is False


# ── previous window ───────────────────────────────────────────────────────

def test_previous_is_the_window_immediately_before(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    _insert(tmp_db_path, uid, ts=now, ms=100, ttft_ms=10)
    # Eight days back lands inside the 7 days that end where the selected
    # window begins; twenty days back is outside both.
    _insert(tmp_db_path, uid, ts=now - timedelta(days=8), ms=900, ttft_ms=90)
    _insert(tmp_db_path, uid, ts=now - timedelta(days=20), ms=7000, ttft_ms=700)
    p = _get(client, "days=7&bucket=day")
    assert p["current"]["n"] == 1 and p["current"]["p50"] == 100
    assert p["previous"]["n"] == 1 and p["previous"]["p50"] == 900
    assert p["previous"]["ttft_p50"] == 90
    assert p["previous"]["to"] == p["window"]["from"]
    # Previous rows never leak into the bucketed series.
    assert sum(b["n"] for b in p["buckets"]) == 1
    # The KPI set is complete on both sides.
    for k in ("n", "p50", "p95", "p99", "ttft_p50", "ttft_coverage", "error_rate",
              "tokens_per_second", "cost_per_call", "cache_hit_ratio"):
        assert k in p["current"] and k in p["previous"], k


def test_previous_honours_the_same_filters(client, tmp_db_path):
    uid = _user(tmp_db_path)
    then = _now() - timedelta(days=8)
    _insert(tmp_db_path, uid, ts=then, ms=900, model="model-a", call_type="chat", app_id="shouldersurf")
    _insert(tmp_db_path, uid, ts=then, ms=5000, model="model-b", call_type="report", app_id="shouldersurf")
    _insert(tmp_db_path, uid, ts=then, ms=8000, model="model-a", call_type="chat", app_id="techrehearsal")
    assert _get(client, "days=7&model=model-a")["previous"]["p99"] == 8000
    assert _get(client, "days=7&model=model-b")["previous"]["p50"] == 5000
    assert _get(client, "days=7&call_type=report")["previous"]["p50"] == 5000
    assert _get(client, "days=7&app=techrehearsal")["previous"]["p50"] == 8000
    assert _get(client, "days=7&app=techrehearsal&model=model-b")["previous"]["n"] == 0


# ── model share ───────────────────────────────────────────────────────────

def test_model_shares_sum_to_one_over_successful_calls(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    for model, count in (("model-a", 5), ("model-b", 3), ("model-c", 2)):
        for _ in range(count):
            _insert(tmp_db_path, uid, ts=now, ms=100, model=model)
    # An error row is not a successful call, so it is not a share.
    _insert(tmp_db_path, uid, ts=now, ms=100, model="model-c", status="error")
    p = _get(client, "days=7")
    shares = {m["model"]: m["share"] for m in p["models"]}
    assert shares == {"model-a": pytest.approx(0.5), "model-b": pytest.approx(0.3),
                      "model-c": pytest.approx(0.2)}
    assert sum(m["share"] for m in p["models"]) == pytest.approx(1.0, abs=1e-3)
    assert [m["n"] for m in p["models"]] == [5, 3, 2]
    # Like by_model: the call_type filter narrows it, the model filter does
    # not, so a share of one model at 100% is never drawn.
    assert len(_get(client, "days=7&model=model-a")["models"]) == 3
    assert _get(client, "days=7")["models"][0]["model"] == "model-a"


def test_an_empty_window_has_no_shares_and_no_division(client, tmp_db_path):
    p = _get(client, "days=7")
    assert p["models"] == []
    assert p["current"]["n"] == 0 and p["current"]["p50"] is None
    assert p["previous"]["n"] == 0 and p["previous"]["ttft_coverage"] is None


# ── breakdown trends for the small multiples ──────────────────────────────

def test_each_breakdown_row_carries_a_trend_aligned_to_the_buckets(client, tmp_db_path):
    uid = _user(tmp_db_path)
    now = _now()
    yday = now - timedelta(days=1)
    _insert(tmp_db_path, uid, ts=yday, ms=300, model="model-a")
    _insert(tmp_db_path, uid, ts=now, ms=100, model="model-a")
    _insert(tmp_db_path, uid, ts=now, ms=9000, model="model-b")
    p = _get(client, "days=7&bucket=day")
    keys = [b["bucket"] for b in p["buckets"]]
    a = next(m for m in p["breakdown"]["by_model"] if m["model"] == "model-a")
    b = next(m for m in p["breakdown"]["by_model"] if m["model"] == "model-b")
    assert len(a["trend"]["p50"]) == len(keys) == len(a["trend"]["p95"]) == len(a["trend"]["n"])
    assert a["trend"]["p50"][keys.index(yday.isoformat()[:10])] == 300
    assert a["trend"]["p50"][keys.index(now.isoformat()[:10])] == 100
    assert b["trend"]["p50"][keys.index(now.isoformat()[:10])] == 9000
    assert b["trend"]["p50"][keys.index(yday.isoformat()[:10])] is None
    assert b["trend"]["n"][keys.index(yday.isoformat()[:10])] == 0


def test_the_query_never_selects_metadata():
    # metadata carries raw_request and raw_response; pulling it would
    # multiply the payload for no number on this page.
    from app.routers import webhooks
    src = inspect.getsource(webhooks.latency_trends)
    select = src[src.index("SELECT"):src.index("FROM usage_log")]
    assert "metadata" not in select


def test_the_dashboard_payload_still_carries_latency_percentiles(client, tmp_db_path):
    # Other panels may read it; the new tab does not replace it.
    uid = _user(tmp_db_path)
    _insert(tmp_db_path, uid, ts=_now(), ms=100)
    dash = client.get("/webhooks/admin/dashboard?days=7", headers=ADMIN).json()
    assert dash["latency_percentiles"]["p50"] == 100


# ── frontend: the series builder, evaluated in node ───────────────────────

node = pytest.mark.skipif(shutil.which("node") is None,
                          reason="node is needed to evaluate the page's JS")


def _extract(name):
    """Pull one top-level function out of the page by brace matching."""
    src = open(HTML).read()
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _node(js_expr, *fns):
    js = "\n".join(_extract(f) for f in fns) + f"\nconsole.log(JSON.stringify({js_expr}));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


# Every key holds a different number, so a series that read the wrong key
# produces a different list, not a coincidentally equal one. Bucket 0 has
# first-token data, bucket 1 is quiet, bucket 2 has calls but none streamed,
# and bucket 3 streamed but its streaming_p50 is missing (a shape the
# endpoint never emits, kept so "both exist" is tested and not "one exists").
FIXTURE = [
    {"bucket": "2026-09-13T04:00:00", "n": 4, "p50": 1000, "p95": 2000, "p99": 3000,
     "mean": 5, "max": 6, "error_rate": 0.25, "tokens_per_second": 7.5,
     "ttft_n": 2, "ttft_p50": 300, "ttft_p95": 400, "ttft_coverage": 0.5, "streaming_share": 0.5,
     "streaming_p50": 1500, "generation_p50_ms": 1100, "generation_tokens_per_second": 9.5,
     "cost_per_call": 0.02},
    {"bucket": "2026-09-13T05:00:00", "n": 0, "p50": None, "p95": None, "p99": None,
     "mean": None, "max": None, "error_rate": None, "tokens_per_second": None,
     "ttft_n": 0, "ttft_p50": None, "ttft_p95": None, "ttft_coverage": None, "streaming_share": None,
     "streaming_p50": None, "generation_p50_ms": None, "generation_tokens_per_second": None,
     "cost_per_call": None},
    {"bucket": "2026-09-13T06:00:00", "n": 9, "p50": 10, "p95": 20, "p99": 30,
     "mean": 50, "max": 60, "error_rate": 0.5, "tokens_per_second": 70,
     "ttft_n": 0, "ttft_p50": None, "ttft_p95": None, "ttft_coverage": 0.0, "streaming_share": 0.0,
     "streaming_p50": None, "generation_p50_ms": None, "generation_tokens_per_second": None,
     "cost_per_call": 0.04},
    {"bucket": "2026-09-13T07:00:00", "n": 3, "p50": 800, "p95": 900, "p99": 950,
     "mean": 5, "max": 6, "error_rate": 0.0, "tokens_per_second": 8,
     "ttft_n": 1, "ttft_p50": 250, "ttft_p95": 250, "ttft_coverage": 0.3333, "streaming_share": 0.3333,
     "streaming_p50": None, "generation_p50_ms": None, "generation_tokens_per_second": None,
     "cost_per_call": 0.01},
    # The mirror image: a streaming total with no first-token time. The
    # endpoint never emits this either, but "total minus 0 when TTFT is
    # missing" is only distinguishable from "no band" on exactly this row.
    {"bucket": "2026-09-13T08:00:00", "n": 2, "p50": 900, "p95": 950, "p99": 990,
     "mean": 5, "max": 6, "error_rate": 0.0, "tokens_per_second": 9,
     "ttft_n": 0, "ttft_p50": None, "ttft_p95": None, "ttft_coverage": 0.0, "streaming_share": 0.0,
     "streaming_p50": 700, "generation_p50_ms": None, "generation_tokens_per_second": None,
     "cost_per_call": 0.03},
]


@node
def test_each_series_reads_its_own_key():
    s = _node(f"perfSeries({json.dumps(FIXTURE)})", "perfSeries")
    assert s["p50"] == [1000, None, 10, 800, 900]
    assert s["p95"] == [2000, None, 20, 900, 950]
    assert s["p99"] == [3000, None, 30, 950, 990]
    assert s["n"] == [4, 0, 9, 3, 2]
    assert s["ttft_p50"] == [300, None, None, 250, None]
    assert s["ttft_p95"] == [400, None, None, 250, None]
    assert s["ttft_n"] == [2, 0, 0, 1, 0]
    assert s["tokens_per_second"] == [7.5, None, 70, 8, 9]
    assert s["generation_tokens_per_second"] == [9.5, None, None, None, None]
    assert s["error_rate"] == [25, None, 50, 0, 0]
    assert s["streaming_share"] == [50, None, 0, 33.3, 0]
    assert s["cost_per_call"] == [0.02, None, 0.04, 0.01, 0.03]
    assert s["categories"] == [b["bucket"] for b in FIXTURE]


@node
def test_a_bucket_without_ttft_is_a_gap_not_a_zero():
    # Bucket 2 had nine calls and none streamed. Plotting 0 ms there would
    # draw an instant first token that never happened; null is a gap and
    # the length is preserved so the axis stays time.
    s = _node(f"perfSeries({json.dumps(FIXTURE)})", "perfSeries")
    assert s["ttft_p50"][2] is None and s["ttft_p50"][2] != 0
    assert s["ttft_p50"][1] is None
    assert len(s["ttft_p50"]) == len(FIXTURE)
    assert s["p99"][1] is None
    assert _node("perfSeries([])", "perfSeries")["ttft_p50"] == []


@node
def test_stacked_generation_is_total_minus_ttft_only_where_both_exist():
    st = _node(f"perfStackedSeries({json.dumps(FIXTURE)})", "perfStackedSeries")
    # Bucket 0: streaming_p50 1500 minus ttft_p50 300. NOT p50 (1000) minus
    # ttft, which would be 700 and would mix non-streaming calls into a
    # band labelled generation.
    assert st["generation"][0] == 1200
    assert st["ttft"][0] == 300
    assert st["top"][0] == 1500
    # Bucket 2 has no first-token time: no band, not total minus 0.
    assert st["generation"][2] is None and st["ttft"][2] is None and st["top"][2] is None
    # Bucket 3 has a ttft but no streaming total: still no generation band.
    assert st["ttft"][3] == 250 and st["generation"][3] is None and st["top"][3] is None
    # Bucket 4 has a streaming total but no ttft: no band, NOT 700 minus 0.
    assert st["ttft"][4] is None and st["generation"][4] is None and st["top"][4] is None
    assert st["generation"][1] is None
    assert st["total"] == [1000, None, 10, 800, 900]


@node
def test_kpi_delta_names_a_latency_that_got_worse():
    d = _node("perfDelta(500, 400, true)", "perfDelta")
    assert d["diff"] == 100 and d["direction"] == "up" and d["good"] is False
    assert d["pct"] == 25
    # The same movement on a metric where up is good.
    up_good = _node("perfDelta(500, 400, false)", "perfDelta")
    assert up_good["direction"] == "up" and up_good["good"] is True
    # A latency that improved.
    better = _node("perfDelta(400, 500, true)", "perfDelta")
    assert better["diff"] == -100 and better["direction"] == "down" and better["good"] is True
    assert better["pct"] == -20
    # Neutral metrics, no movement, and missing sides.
    assert _node("perfDelta(5, 4, null)", "perfDelta")["good"] is None
    assert _node("perfDelta(4, 4, true)", "perfDelta")["direction"] == "flat"
    assert _node("perfDelta(4, 4, true)", "perfDelta")["good"] is None
    assert _node("perfDelta(null, 4, true)", "perfDelta") is None
    assert _node("perfDelta(4, null, true)", "perfDelta") is None
    # No ratio to a zero.
    assert _node("perfDelta(4, 0, true)", "perfDelta")["pct"] is None


@node
def test_the_caption_states_the_payloads_coverage():
    payload = {"ttft_recorded": True, "current": {"n": 3170, "ttft_n": 416, "ttft_coverage": 0.1312}}
    cap = _node(f"perfCaption({json.dumps(payload)})", "perfCaption")
    assert "13.1%" in cap
    assert "416 of 3,170" in cap
    assert "total-time" in cap
    # A different coverage produces a different caption, so the percent is
    # read off the payload and not hard-coded.
    payload["current"]["ttft_coverage"] = 0.4
    assert "40.0%" in _node(f"perfCaption({json.dumps(payload)})", "perfCaption")
    # No streaming calls: say so, never print a coverage of the NULLs.
    none = _node(f"perfCaption({json.dumps({'ttft_recorded': False, 'current': {'n': 50, 'ttft_coverage': 0.0}})})", "perfCaption")
    assert "No streaming calls" in none and "%" not in none


ROWS = [
    {"kind": "model", "name": "Zed", "n": 5, "p99": 900, "cost_per_call": None},
    {"kind": "model", "name": "alpha", "n": 50, "p99": 100, "cost_per_call": 0.02},
    {"kind": "call type", "name": "chat", "n": 20, "p99": 500, "cost_per_call": 0.01},
]


@node
def test_sort_by_number_and_by_name():
    fns = ("_latSortVal", "sortLatencyRows")
    desc = _node(f"sortLatencyRows({json.dumps(ROWS)}, 'n', -1).map(r => r.name)", *fns)
    assert desc == ["alpha", "chat", "Zed"]
    # The opposite direction and a different key both reverse it, so a sort
    # that ignored its arguments and always ordered by calls could not pass.
    asc_n = _node(f"sortLatencyRows({json.dumps(ROWS)}, 'n', 1).map(r => r.name)", *fns)
    assert asc_n == ["Zed", "chat", "alpha"]
    desc_p99 = _node(f"sortLatencyRows({json.dumps(ROWS)}, 'p99', -1).map(r => r.name)", *fns)
    assert desc_p99 == ["Zed", "chat", "alpha"]
    asc = _node(f"sortLatencyRows({json.dumps(ROWS)}, 'p99', 1).map(r => r.name)", *fns)
    assert asc == ["alpha", "chat", "Zed"]
    # Case-insensitive on the name column.
    by_name = _node(f"sortLatencyRows({json.dumps(ROWS)}, 'name', 1).map(r => r.name)", *fns)
    assert by_name == ["alpha", "chat", "Zed"]
    # A null metric sits at the bottom of a descending sort, never in the middle.
    cost = _node(f"sortLatencyRows({json.dumps(ROWS)}, 'cost_per_call', -1).map(r => r.name)", *fns)
    assert cost == ["alpha", "chat", "Zed"]


def test_the_page_wires_the_tab_and_keeps_the_panel_wrapper():
    src = open(HTML).read()
    assert "latency: () => loadLatency()" in src, "the tab must reload with the global Refresh"
    assert "panel('latency', () => renderPerformance(data))" in src, (
        "the render must run inside panel() so a ReferenceError is contained and named")
    assert '<div class="tab" data-tab="latency" onclick="switchTab(\'latency\')">Performance</div>' in src
    assert 'id="tab-latency"' in src
    for sid in ("lat-days", "lat-bucket", "lat-model", "lat-call-type", "perf-kpis", "perf-stacked",
                "perf-main-chart", "perf-caption", "perf-model-share", "perf-models-multiples",
                "perf-models-table", "perf-cts-multiples", "perf-cts-table", "perf-chart-errors",
                "perf-chart-tps", "perf-chart-streaming", "perf-chart-cost"):
        assert f'id="{sid}"' in src, f"missing #{sid}"
    # The old single-list latency block must not have come back.
    assert "latencySeries(" not in src


def test_no_dashes_as_punctuation_in_the_new_copy():
    src = open(HTML).read()
    start = src.index("<!-- Performance.")
    end = src.index("<!-- Providers -->")
    tab = src[start:end]
    em, en = "—", "–"
    assert em not in tab and en not in tab
    js = src[src.index("// --- Performance tab:"):src.index("function renderTiers(")]
    assert em not in js and en not in js
    css = src[src.index("/* Performance tab */"):src.index("/* Period selector */")]
    assert em not in css and en not in css
