"""The Latency tab shows percentiles over time, by model and by call type.

Scott, 2026-09-13: "there is a simple view of P99 P95 etc. I want to see our
responses historically and be able to spot trends." The dashboard's
`latency_percentiles` is one sorted list over the whole window, so it cannot
show a trend by construction. /admin/latency-trends buckets by hour or day.

Backend half: bucketing, exact nearest-rank percentiles on a known fixture,
error rows out of latency but in error_rate, app and model scoping, zero
guards, and the breakdown split. Frontend half: the page's series builder
evaluated in node, because a source-text grep would pass just as happily if
the chart plotted p95 where p99 was asked for.
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
            output_tokens=50, cached_tokens=0, cost=0.01):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO usage_log
           (id, user_id, provider, model, input_tokens, output_tokens,
            estimated_cost_usd, request_timestamp, response_time_ms,
            status, error_message, call_type, cached_tokens, app_id)
           VALUES (?, ?, 'anthropic', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, model, input_tokens, output_tokens, cost,
         ts.isoformat(), ms, status, "boom" if status != "success" else None,
         call_type, cached_tokens, app_id),
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


def test_ttft_is_declared_absent_not_approximated(client, tmp_db_path):
    p = _get(client, "days=7")
    assert p["ttft_recorded"] is False
    assert not any("ttft" in k or "first_token" in k for b in p["buckets"] for k in b)


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
# produces a different list, not a coincidentally equal one.
FIXTURE = [
    {"bucket": "2026-09-13T04:00:00", "n": 4, "p50": 1, "p95": 2, "p99": 3,
     "mean": 5, "max": 6, "error_rate": 0.25, "tokens_per_second": 7.5},
    {"bucket": "2026-09-13T05:00:00", "n": 0, "p50": None, "p95": None, "p99": None,
     "mean": None, "max": None, "error_rate": None, "tokens_per_second": None},
    {"bucket": "2026-09-13T06:00:00", "n": 9, "p50": 10, "p95": 20, "p99": 30,
     "mean": 50, "max": 60, "error_rate": 0.5, "tokens_per_second": 70},
]


@node
def test_each_series_reads_its_own_key():
    s = _node(f"latencySeries({json.dumps(FIXTURE)})", "latencySeries")
    assert s["p50"] == [1, None, 10]
    assert s["p95"] == [2, None, 20]
    assert s["p99"] == [3, None, 30]
    assert s["n"] == [4, 0, 9]
    assert s["tokens_per_second"] == [7.5, None, 70]
    assert s["error_rate"] == [25, None, 50]
    assert s["categories"] == [b["bucket"] for b in FIXTURE]


@node
def test_a_quiet_bucket_is_a_gap_not_a_zero():
    # Plotting 0 ms for an hour with no calls would draw a cliff that never
    # happened; null is a gap. Length is preserved so the axis stays time.
    s = _node(f"latencySeries({json.dumps(FIXTURE)})", "latencySeries")
    assert s["p99"][1] is None
    assert len(s["p99"]) == len(FIXTURE)
    assert _node("latencySeries([])", "latencySeries")["p99"] == []


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
    assert "panel('latency', () => renderLatencyTrends(data))" in src, (
        "the render must run inside panel() so a ReferenceError is contained and named")
    assert "Time to first token is not yet recorded; tokens per second is total-time throughput." in src
    for sid in ("lat-days", "lat-bucket", "lat-model", "lat-call-type", "latency-trend-chart",
                "latency-error-chart", "latency-tps-chart", "latency-breakdown-table"):
        assert f'id="{sid}"' in src, f"missing #{sid}"


def test_no_dashes_as_punctuation_in_the_new_copy():
    src = open(HTML).read()
    start = src.index("<!-- Latency -->")
    end = src.index("<!-- Providers -->")
    tab = src[start:end]
    em, en = "—", "–"
    assert em not in tab and en not in tab
    js = src[src.index("// --- Latency tab: percentiles over time"):src.index("function renderTiers(")]
    assert em not in js and en not in js
