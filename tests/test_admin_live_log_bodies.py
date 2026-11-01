"""The Live Log list carries no bodies; a row's bodies are fetched once on expand.

2026-10-31, measured on the live page: GET /webhooks/admin/live-log?limit=50
returned 291 KB for 50 rows, 239 KB of it the `response` field and 34 KB the
`request` field, and the Live Log tab polled it every 5 seconds to draw a list
that shows neither. The per-entry route live-log/{request_id} already served
one entry in full and nothing called it.

Two halves, each proved on its own side (rule 5):

  server: the list rows carry no `request` and no `response` key, only the
          metadata plus has_request / has_response / captured_bytes, and the
          detail route still returns both bodies for the same entry;
  client: toggleLiveLogRow fetches the detail URL exactly once for two expands
          of the same row across a re-render, and rendering the list fetches
          nothing. Evaluated in node, the harness of test_admin_live_log.py,
          because a source-text assertion cannot tell whether the cache is on
          the path the click actually takes.
"""

import json
import shutil
import subprocess

import pytest

from app.middleware import request_logging as rl

ADMIN = {"X-Admin-Key": "test-admin-key"}
HTML = "app/static/admin.html"


# ── server ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_buffer():
    rl._LOG_BUFFER.clear()
    rl._BUFFER_BYTES = 0
    yield
    rl._LOG_BUFFER.clear()
    rl._BUFFER_BYTES = 0


def _push(rid, resp_body, req_body=None):
    """One entry in the recorder's own shape (StreamingBypassMiddleware's
    _buffer_append call), sized the way the recorder sizes it."""
    req_str = json.dumps(req_body) if req_body is not None else ""
    resp_str = json.dumps(resp_body)
    rl._buffer_append({
        "timestamp": "2026-10-31T06:00:00+00:00",
        "request_id": rid,
        "app_id": "shouldersurf",
        "method": "POST",
        "path": "/v1/chat",
        "query": None,
        "status": 200,
        "latency_ms": 12,
        "client_ip": "10.0.0.7",
        "user_agent": "ShoulderSurf/1.18",
        "request": {"headers": {"content-type": "application/json"},
                    "body": req_body},
        "response": {"headers": {"content-type": "application/json"},
                     "body": resp_body},
    }, len(req_str) + len(resp_str))


def test_the_list_carries_no_bodies_and_the_detail_still_does(client):
    big = {"text": "x" * 2048}            # the 2 KB response the list used to ship
    _push("aaaa00000001", big, req_body={"message": "hi"})

    rows = client.get("/webhooks/admin/live-log?limit=50", headers=ADMIN).json()["entries"]
    assert len(rows) == 1
    row = rows[0]
    assert "response" not in row, (
        f"the list row still ships the response body ({len(json.dumps(row))} bytes); "
        "this is the 239 KB of 291 KB measured on 2026-10-31")
    assert "request" not in row, "the list row still ships the request body"
    assert "x" * 100 not in json.dumps(row), "the 2 KB payload leaked into the row under another key"

    # Everything small survives, and the client gets a hint per side.
    for key in ("request_id", "timestamp", "method", "path", "query", "status",
                "latency_ms", "client_ip", "user_agent", "app_id"):
        assert key in row, f"list row lost {key}"
    assert row["has_request"] is True
    assert row["has_response"] is True
    assert row["captured_bytes"] == len(json.dumps({"message": "hi"})) + len(json.dumps(big))
    assert rl._SIZE_KEY not in row

    full = client.get("/webhooks/admin/live-log/aaaa00000001", headers=ADMIN).json()
    assert full["response"]["body"] == big
    assert full["request"]["body"] == {"message": "hi"}
    assert full["request_id"] == "aaaa00000001"


def test_has_flags_say_which_side_had_a_body(client):
    _push("bbbb00000002", {"ok": True}, req_body=None)
    row = client.get("/webhooks/admin/live-log", headers=ADMIN).json()["entries"][0]
    assert row["has_request"] is False
    assert row["has_response"] is True


def test_the_list_is_newest_first_and_honours_limit(client):
    for i in range(3):
        _push(f"cccc0000000{i}", {"i": i})
    rows = client.get("/webhooks/admin/live-log?limit=2", headers=ADMIN).json()["entries"]
    assert [r["request_id"] for r in rows] == ["cccc00000002", "cccc00000001"]


def test_a_detail_fetch_does_not_log_itself(client):
    """The dashboard now hits live-log/{id} on every expand. Without a prefix
    skip each expand writes a row into the log it is reading."""
    _push("dddd00000004", {"ok": True})
    client.get("/webhooks/admin/live-log/dddd00000004", headers=ADMIN)
    client.get("/webhooks/admin/live-log/nope", headers=ADMIN)
    paths = [e["path"] for e in rl._LOG_BUFFER]
    assert paths == ["/v1/chat"], f"admin detail fetches reached the buffer: {paths}"


# ── client ────────────────────────────────────────────────────────────────

node = pytest.mark.skipif(shutil.which("node") is None,
                          reason="node is needed to evaluate the page's JS")


def _src():
    return open(HTML).read()


def _extract(name):
    """Pull one top-level function out of the page by brace matching."""
    src = _src()
    start = src.index(f"function {name}(")
    if src[start - 6:start] == "async ":
        start -= 6
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _extract_if_present(name):
    # So a page without the helper fails on the behaviour assertion, not on
    # a missing-function lookup that reads as a broken test.
    return _extract(name) + "\n" if f"function {name}(" in _src() else ""


def _node(js):
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _row(rid):
    """A list row as the fixed server now sends it: no bodies."""
    return {"request_id": rid, "timestamp": "2026-10-31T06:00:00+00:00", "method": "POST",
            "path": "/v1/chat", "query": None, "status": 200, "latency_ms": 12,
            "client_ip": "10.0.0.7", "user_agent": "ShoulderSurf/1.18", "app_id": "shouldersurf",
            "has_request": True, "has_response": True, "captured_bytes": 2100}


def _detail(rid):
    return {**_row(rid),
            "request": {"headers": {"content-type": "application/json"}, "body": {"message": "hi"}},
            "response": {"headers": {"content-type": "application/json"}, "body": {"text": "DETAIL-BODY-MARKER"}}}


# fetch records every URL it was asked for and answers with the detail entry.
# document.querySelector returns the pane the test registers, so the paint
# after the fetch lands somewhere the test can read.
_HARNESS = """
const fetched = [];
const DETAIL = %(detail)s;
globalThis.fetch = (url) => { fetched.push(url); return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(DETAIL)}); };
globalThis.BASE = '';
globalThis.adminKey = 'k';
let pane = {innerHTML: '', style: {display: 'none'}};
globalThis.document = {querySelector: () => pane};
"""


def _livelog_js():
    return (_extract("esc") + "\n" + _extract("livelogRowId") + "\n"
            + _extract("toggleLiveLogRow") + "\n" + _extract_if_present("loadLiveLogDetail")
            + _extract_if_present("renderLiveLogDetail") + _extract("renderLiveLogEntries") + "\n")


@node
def test_two_expands_across_a_rerender_fetch_the_detail_exactly_once():
    rid = "aaaa00000001"
    js = _HARNESS % {"detail": json.dumps(_detail(rid))} + _livelog_js()
    js += """
const _livelogExpanded = new Set();
const _livelogDetails = new Map();
const head = {parentElement: {dataset: {rid: %(rid)s, detail: '1'}}, nextElementSibling: pane};
(async () => {
  toggleLiveLogRow(head);                      // open: must fetch
  const whileLoading = pane.innerHTML;
  await new Promise(r => setTimeout(r, 0));    // let the fetch resolve and paint
  const afterFetch = pane.innerHTML;
  const rerender = renderLiveLogEntries([%(row)s], _livelogExpanded, _livelogDetails);
  toggleLiveLogRow(head);                      // close
  toggleLiveLogRow(head);                      // open again: must NOT fetch
  await new Promise(r => setTimeout(r, 0));
  console.log(JSON.stringify({fetched, whileLoading, afterFetch, rerender, cached: _livelogDetails.has(%(rid)s)}));
})();
""" % {"rid": json.dumps(rid), "row": json.dumps(_row(rid))}
    r = _node(js)
    assert r["fetched"] == [f"/webhooks/admin/live-log/{rid}"], (
        f"expected exactly one detail fetch for two expands of one row, got {r['fetched']}")
    assert "DETAIL-BODY-MARKER" not in r["whileLoading"]
    assert "oading" in r["whileLoading"], "the pane must say it is loading while the detail is out"
    assert "DETAIL-BODY-MARKER" in r["afterFetch"], "the fetched response body never reached the pane"
    # The poll's re-render draws the open row from the cache, not from the list row.
    assert "DETAIL-BODY-MARKER" in r["rerender"], (
        "a row open across a poll must be re-rendered from the cached detail")
    assert r["cached"] is True


@node
def test_rendering_the_list_fetches_no_detail():
    rows = [_row("aaaa00000001"), _row("bbbb00000002"), _row("cccc00000003")]
    js = _HARNESS % {"detail": "{}"} + _livelog_js()
    js += """
const html = renderLiveLogEntries(%s, new Set(['bbbb00000002']), new Map());
console.log(JSON.stringify({fetched, html}));
""" % json.dumps(rows)
    r = _node(js)
    assert r["fetched"] == [], f"the list render must not fetch; it fetched {r['fetched']}"
    # A list row has no bodies, so an open row that has not been fetched yet
    # shows the loading text rather than throwing on e.request.body.
    assert 'data-rid="bbbb00000002"' in r["html"]
    assert "oading" in r["html"]


@node
def test_a_failed_detail_fetch_shows_an_error_line_and_a_later_click_retries():
    rid = "aaaa00000001"
    js = _HARNESS % {"detail": "{}"} + _livelog_js()
    js += """
globalThis.fetch = (url) => { fetched.push(url); return Promise.resolve({ok: false, status: 404, json: () => Promise.resolve({detail: 'gone'})}); };
const _livelogExpanded = new Set();
const _livelogDetails = new Map();
const head = {parentElement: {dataset: {rid: %(rid)s, detail: '1'}}, nextElementSibling: pane};
(async () => {
  toggleLiveLogRow(head);
  await new Promise(r => setTimeout(r, 0));
  const afterError = pane.innerHTML;
  toggleLiveLogRow(head);  // close
  toggleLiveLogRow(head);  // open: an error is retried
  await new Promise(r => setTimeout(r, 0));
  console.log(JSON.stringify({fetched, afterError}));
})();
""" % {"rid": json.dumps(rid)}
    r = _node(js)
    assert "404" in r["afterError"], f"the pane must say the fetch failed, got {r['afterError']!r}"
    assert len(r["fetched"]) == 2, "a failed fetch must not be cached as if it had succeeded"


@node
def test_a_row_without_a_request_id_says_no_detail_and_fetches_nothing():
    row = {k: v for k, v in _row("x").items() if k != "request_id"}
    js = _HARNESS % {"detail": "{}"} + _livelog_js()
    js += """
const _livelogExpanded = new Set();
const _livelogDetails = new Map();
const row = %s;
const html = renderLiveLogEntries([row], new Set([livelogRowId(row)]), _livelogDetails);
const head = {parentElement: {dataset: {rid: livelogRowId(row)}}, nextElementSibling: pane};
toggleLiveLogRow(head);
console.log(JSON.stringify({fetched, html}));
""" % json.dumps(row)
    r = _node(js)
    assert r["fetched"] == []
    assert "No detail available" in r["html"]
    assert 'data-detail' not in r["html"]


def test_the_live_log_regions_carry_no_dashes():
    # The em and en dash as code points, so this file itself stays clean.
    dashes = (chr(0x2014), chr(0x2013))
    src = _src()
    regions = {
        "admin.html live log": src[src.index("// --- Live Log ---"):src.index("// --- Model Routing ---")],
        "admin.html live log panel": src[src.index('id="tab-livelog"'):src.index('id="tab-errors"')],
        "this test": open(__file__).read(),
    }
    wh = open("app/routers/webhooks.py").read()
    regions["webhooks live log"] = wh[wh.index("# --- Live Request Log ---"):wh.index("# --- Remote Config Management ---")]
    # Both server files predate the rule and carry dashes in older comments;
    # only the text this change added is held to it.
    rl_src = open("app/middleware/request_logging.py").read()
    regions["request_logging skip prefixes"] = rl_src[rl_src.index("_SKIP_PREFIXES"):rl_src.index("_MAX_BODY_LOG = ")]
    regions["request_logging summaries"] = rl_src[rl_src.index("_BODY_KEYS"):rl_src.index("class StreamingBypassMiddleware")]
    for name, text in regions.items():
        for d in dashes:
            assert d not in text, f"{name} carries a dash"
