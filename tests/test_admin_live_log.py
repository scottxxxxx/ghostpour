"""The Live Log poll must stop when the tab is not on screen, and a row the
operator opened must stay open across polls.

2026-10-31, measured on the live page: loadLiveLog armed
setInterval(loadLiveLog, 5000) and nothing cleared it on a tab switch
(switchTab clears only the live-meetings timer). After one visit to Live Log,
sitting on Overview, _livelogTimer was still set and two more live-log
fetches landed in 11 seconds at 294 KB each (live-log?limit=50 returns
294 KB for 50 rows). The poll also kept running while the browser tab was
hidden. Separately, every poll rebuilt the list with innerHTML, so a request
the operator had expanded snapped shut within 5 seconds.

The functions are evaluated in node rather than grepped for, because a
source-text assertion cannot tell whether the guard is on the path the
interval actually takes. The harness stubs fetch as a counter and the timer
API as a recorder, so the assertions are on what the function DID.
"""

import json
import shutil
import subprocess

import pytest

HTML = "app/static/admin.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")


def _src():
    return open(HTML).read()


def _extract(name):
    """Pull one top-level function out of the page by brace matching."""
    src = _src()
    start = src.index(f"function {name}(")
    if src[start - 6:start] == "async ":
        start -= 6  # loadLiveLog is `async function`; the keyword must travel with it
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _node(js):
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ── the poll ──────────────────────────────────────────────────────────────

# A browser-shaped harness: fetch counts, setInterval records the callback it
# was handed (so the test can fire a tick itself), clearInterval records what
# it was asked to clear. _livelogTimer starts as 7, a timer left armed by a
# previous visit, which is the state measured on 2026-10-31.
_HARNESS = """
let fetchCalls = 0;
let cleared = [];
let armed = [];
let nextTimer = 100;
globalThis.fetch = () => { fetchCalls++; return Promise.resolve({json: () => Promise.resolve({entries: []})}); };
globalThis.setInterval = (fn, ms) => { const t = nextTimer++; armed.push({t, fn, ms}); return t; };
globalThis.clearInterval = (t) => { cleared.push(t); };
globalThis.document = {hidden: %(hidden)s, getElementById: () => ({checked: true, value: '', innerHTML: ''})};
globalThis.BASE = '';
globalThis.adminKey = 'k';
globalThis.filterLiveLog = () => {};
let _livelogTimer = 7;
let _activeTab = %(tab)s;
"""


def _poll(tab, hidden=False, then=""):
    js = _HARNESS % {"tab": json.dumps(tab), "hidden": "true" if hidden else "false"}
    js += _extract("loadLiveLog")
    js += """
(async () => {
  await loadLiveLog();
  %s
  console.log(JSON.stringify({fetchCalls, cleared, armed: armed.map(a => a.t), timer: _livelogTimer}));
})();
""" % then
    return _node(js)


def test_the_poll_does_not_fetch_from_another_tab_and_stands_its_timer_down():
    r = _poll("overview")
    assert r["fetchCalls"] == 0, (
        "loadLiveLog fetched 294 KB while the Overview tab was on screen; "
        f"fetch was called {r['fetchCalls']} time(s)")
    assert 7 in r["cleared"], "the timer left by the previous visit must be cleared"
    assert r["timer"] is None, f"_livelogTimer must be nulled, got {r['timer']!r}"
    assert r["armed"] == [], "no new interval may be armed from another tab"


def test_the_poll_does_not_fetch_while_the_browser_tab_is_hidden():
    r = _poll("livelog", hidden=True)
    assert r["fetchCalls"] == 0, "a hidden document must not be polled"
    assert r["timer"] is None
    assert r["armed"] == []


def test_the_poll_fetches_and_arms_on_its_own_visible_tab():
    # The positive control: a harness in which fetch is never reached would
    # pass the two tests above for the wrong reason.
    r = _poll("livelog")
    assert r["fetchCalls"] == 1
    assert len(r["armed"]) == 1
    assert r["timer"] == r["armed"][0]


def test_a_tick_after_leaving_the_tab_clears_the_interval_itself():
    # switchTab belongs to another region and does not know this timer, so the
    # interval callback has to be the thing that stops it: arm on livelog,
    # move to overview, fire the tick the page would have fired.
    r = _poll("livelog", then="_activeTab = 'overview'; await armed[0].fn();")
    assert r["fetchCalls"] == 1, "the tick from another tab must not fetch"
    assert r["armed"][0] in r["cleared"], "the tick must clear its own interval"
    assert r["timer"] is None


# ── expanded rows survive a re-render ─────────────────────────────────────

def _entry(rid, path="/webhooks/admin/dashboard"):
    return {
        "request_id": rid, "timestamp": "2026-10-31T06:00:00+00:00", "method": "GET",
        "path": path, "query": "", "status": 200, "latency_ms": 12, "client_ip": "10.0.0.7",
        "request": {"headers": {}, "body": None}, "response": {"headers": {}, "body": None},
    }


def _row(html, rid):
    """The rendered block for one request_id, or None if rows are not keyed."""
    marker = f'data-rid="{rid}"'
    if marker not in html:
        return None
    start = html.index(marker)
    nxt = html.find("data-rid=", start + len(marker))
    return html[start:nxt if nxt != -1 else len(html)]


def _extract_if_present(name):
    # So a page without the helper fails on the behaviour assertion below,
    # not on a missing-function lookup that reads as a broken test.
    return _extract(name) + "\n" if f"function {name}(" in _src() else ""


def _render_twice(expanded_ids, first, second):
    js = _extract("esc") + "\n" + _extract_if_present("livelogRowId")
    js += _extract("renderLiveLogEntries")
    js += """
const expanded = new Set(%s);
const a = renderLiveLogEntries(%s, expanded);
const b = renderLiveLogEntries(%s, expanded);
console.log(JSON.stringify({a, b}));
""" % (json.dumps(expanded_ids), json.dumps(first), json.dumps(second))
    return _node(js)


def test_an_expanded_row_is_still_expanded_after_the_next_poll():
    rows = [_entry("aaaa00000001"), _entry("bbbb00000002")]
    # the next poll puts a newer request on top, so the open row moves down
    r = _render_twice(["aaaa00000001"], rows, [_entry("cccc00000003")] + rows)
    for html in (r["a"], r["b"]):
        open_row = _row(html, "aaaa00000001")
        assert open_row is not None, "rows must be keyed by request_id (data-rid) or nothing can be re-applied"
        assert "display:block" in open_row, "the row the operator opened snapped shut on re-render"
        for other in ("bbbb00000002", "cccc00000003"):
            closed = _row(html, other)
            if closed is not None:
                assert "display:none" in closed and "display:block" not in closed, (
                    f"{other} was never opened and must render closed")


def test_the_click_handler_records_the_row_in_the_expanded_set():
    # The round trip the operator actually takes: click opens and records,
    # the next render honours it, a second click closes and forgets.
    js = _extract("esc") + "\n" + _extract("livelogRowId") + "\n" + _extract("toggleLiveLogRow") + "\n"
    js += _extract("renderLiveLogEntries")
    js += """
const _livelogExpanded = new Set();
const head = {parentElement: {dataset: {rid: 'aaaa00000001'}}, nextElementSibling: {style: {display: 'none'}}};
toggleLiveLogRow(head);
const afterOpen = renderLiveLogEntries(%s, _livelogExpanded);
toggleLiveLogRow(head);
const afterClose = renderLiveLogEntries(%s, _livelogExpanded);
console.log(JSON.stringify({afterOpen, afterClose, detail: head.nextElementSibling.style.display}));
""" % (json.dumps([_entry("aaaa00000001")]), json.dumps([_entry("aaaa00000001")]))
    r = _node(js)
    assert "display:block" in _row(r["afterOpen"], "aaaa00000001")
    assert "display:none" in _row(r["afterClose"], "aaaa00000001")
    assert r["detail"] == "none"


# ── supplements: the wiring the node harness cannot see ───────────────────

def test_the_page_resumes_polling_when_the_document_becomes_visible():
    src = _src()
    region = src[src.index("// --- Live Log ---"):src.index("// --- Model Routing ---")]
    assert "addEventListener('visibilitychange'" in region, (
        "a hidden tab stops the poll, so something has to restart it on return")
    assert "window.scrollY" in region, "the scroll offset must be put back after the innerHTML swap"


def test_the_live_log_region_carries_no_dashes():
    src = _src()
    region = src[src.index("// --- Live Log ---"):src.index("// --- Model Routing ---")]
    for d in ("—", "–"):
        assert d not in region
