"""One page load of the admin dashboard runs refresh() exactly once.

2026-10-31: a single load of the live page fired 22 requests to
/webhooks/admin across 8 endpoints: dashboard?days=7 three times, users?days=7
(431 KB, 2 to 3 seconds) twice, latency-trends and typesafe-health four times
each. Two mechanisms, both in the load path:

(a) login() validated the key by fetching dashboard?days=7, then called
    loadAppFilter() AND refresh(). loadAppFilter() fetched /apps and finished
    by calling applyAppFilter(), which called refresh() again. So the key check
    was a third dashboard fetch and every load refreshed twice.

(b) refresh() ran loadLatency() for the Performance tab even while that tab
    was hidden, and loadLatency() fetches latency-trends then typesafe-health.
    switchTab('latency') already runs loadLatency() every time the tab opens,
    so nothing the hidden tab fetched was ever shown.

The load sequence is EXECUTED in node with fetch, document and refresh()
stubbed, and the calls are counted, because a source-text assertion that
applyAppFilter "does not call refresh" passes just as happily when some other
caller refreshes a second time. The Performance guard is pinned as a
mechanism: refresh() must reach loadLatency() only through the active tab's
loader, and switchTab('latency') must still run it (executed, not grepped).
"""

import json
import re
import shutil
import subprocess

import pytest

HTML = "app/static/admin.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")


def _src():
    return open(HTML).read()


def _extract(name):
    """Pull one top-level function (async or not) out of the page by brace
    matching. The deployment-banner test's extractor starts at `function `
    and would drop an `async` keyword, which turns a body with `await` into a
    SyntaxError in node and a test that is red for the wrong reason."""
    src = _src()
    m = re.search(rf"^(async\s+)?function {re.escape(name)}\(", src, re.M)
    assert m, f"{name}() is not a top-level function in {HTML}"
    start = m.start()
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _tab_loaders():
    src = _src()
    start = src.index("const _TAB_LOADERS = {")
    end = src.index("\n};", start) + 3
    return src[start:end]


def _node(js):
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A document just big enough for login(), loadAppFilter() and applyAppFilter():
# every id resolves to an element with value, style, textContent and innerHTML,
# the admin-key field holds a key, and there are no checked app boxes.
_DOM_STUB = r"""
const BASE = '';
let adminKey = '';
let _APPS = [];
const events = [];
const fetched = [];
const els = {};
function el(id) {
  const e = { id, value: id === 'admin-key' ? 'test-key' : '', style: {}, textContent: '', _html: '' };
  Object.defineProperty(e, 'innerHTML', {
    get() { return this._html; },
    set(v) { this._html = v; events.push('populate:' + id); },
  });
  return e;
}
const document = {
  getElementById: (id) => els[id] || (els[id] = el(id)),
  querySelectorAll: () => [],
};
const localStorage = { setItem() {}, getItem() { return null; }, removeItem() {} };
const APPS_BODY = { default_app: 'shouldersurf',
  apps: [{id: 'shouldersurf', label: 'ShoulderSurf'}, {id: 'unknown', label: 'Unknown / unattributed'}] };
function fakeResponse(url) {
  // Every admin endpoint answers 200 with the apps body; the test is about
  // WHICH urls were asked for and how many times refresh() ran, not about
  // any payload.
  return Promise.resolve({ ok: true, status: 200, json: async () => APPS_BODY });
}
const _origFetch = (url) => { fetched.push('orig:' + url); return fakeResponse(url); };
const fetch = (url) => { fetched.push('wrapped:' + url); return fakeResponse(url); };
let refreshCalls = 0;
function refresh() { refreshCalls++; events.push('refresh'); }
function handleAdminAuthFailure() { events.push('authFailure'); }
"""


def _run_load_sequence():
    js = (
        _DOM_STUB
        + _extract("login") + "\n"
        + _extract("loadAppFilter") + "\n"
        + _extract("applyAppFilter") + "\n"
        + _extract("_checkedApps") + "\n"
        + """
login();
// Promise chains in login() and any fetch inside loadAppFilter() settle in a
// few microtask turns; a macrotask after them sees the whole sequence.
setTimeout(() => {
  console.log(JSON.stringify({
    refreshCalls, fetched, events,
    appRows: (els['app-filter-list'] || {}).innerHTML || '',
    appValue: (els['app'] || {}).value,
    appsLoaded: _APPS.length,
    authError: (els['auth-error'] || {}).textContent,
  }));
}, 50);
"""
    )
    return _node(js)


# ── (a) one refresh per load, and the key check is not a dashboard fetch ─────

def test_a_page_load_runs_refresh_exactly_once():
    r = _run_load_sequence()
    assert r["authError"] == "", r
    assert r["refreshCalls"] == 1, (
        f"refresh() ran {r['refreshCalls']} times on one load; events: {r['events']}. "
        "Two runs is the 2026-10-31 bug: login() refreshed and loadAppFilter() "
        "refreshed again through applyAppFilter()."
    )


def test_the_key_check_does_not_fetch_the_dashboard():
    r = _run_load_sequence()
    dashboard = [u for u in r["fetched"] if "/webhooks/admin/dashboard" in u]
    assert dashboard == [], (
        f"login() fetched {dashboard} to validate the key; refresh() fetches the "
        "same payload moments later, so this was a third dashboard load per visit"
    )


def test_the_apps_body_is_fetched_once_and_seeds_the_filter():
    r = _run_load_sequence()
    apps = [u for u in r["fetched"] if "/webhooks/admin/apps" in u]
    assert len(apps) == 1, f"/admin/apps was fetched {len(apps)} times: {apps}"
    assert r["appsLoaded"] == 2, "the key-check response must populate _APPS"
    assert 'value="shouldersurf"' in r["appRows"], "the filter list must be painted from it"
    assert r["appValue"] == "", "nothing checked means every app"


def test_the_one_refresh_runs_after_the_filter_is_painted():
    # refresh() reads the #app value applyAppFilter() sets, so the order is
    # part of the contract, not a detail.
    r = _run_load_sequence()
    ev = r["events"]
    assert "populate:app-filter-list" in ev and "refresh" in ev, ev
    assert ev.index("populate:app-filter-list") < ev.index("refresh"), ev


def test_a_rejected_key_never_refreshes():
    js = (
        _DOM_STUB.replace("ok: true, status: 200", "ok: false, status: 403")
        + _extract("login") + "\n"
        + _extract("loadAppFilter") + "\n"
        + _extract("applyAppFilter") + "\n"
        + _extract("_checkedApps") + "\n"
        + """
login();
setTimeout(() => console.log(JSON.stringify({
  refreshCalls, events, authError: (els['auth-error'] || {}).textContent })), 50);
"""
    )
    r = _node(js)
    assert r["refreshCalls"] == 0, r
    assert r["authError"] == "Invalid admin key", r
    assert "authFailure" not in r["events"], (
        "login() must use _origFetch so the wrapped 403 handler stays out of it")


def test_a_user_filter_change_still_refreshes():
    # The flag that stops the load-time refresh must not also stop the one a
    # checkbox click asks for.
    js = (
        _DOM_STUB
        + _extract("applyAppFilter") + "\n"
        + _extract("_checkedApps") + "\n"
        + _extract("onAppFilterChange") + "\n"
        + _extract("onAppFilterAll") + "\n"
        + """
onAppFilterChange();
onAppFilterAll(true);
console.log(JSON.stringify({refreshCalls}));
"""
    )
    assert _node(js)["refreshCalls"] == 2


# ── (b) a hidden Performance tab fetches nothing; opening it fetches ─────────

def test_refresh_reaches_load_latency_only_through_the_active_tab_loader():
    # Code lines only: the comment that explains this guard names the
    # function it guards against.
    body = "\n".join(ln for ln in _extract("refresh").split("\n")
                     if not ln.lstrip().startswith("//"))
    assert "loadLatency(" not in body, (
        "refresh() must not call loadLatency() itself; it ran latency-trends "
        "and typesafe-health for a hidden tab on every load (2026-10-31)")
    assert "_TAB_LOADERS[_activeTab]" in body, (
        "the active tab's own loader is how refresh() reloads Performance when "
        "that tab is the one on screen")
    assert re.search(r"latency:\s*\(\)\s*=>\s*loadLatency\(\)", _tab_loaders()), (
        "the Performance tab must stay in _TAB_LOADERS or the global Refresh "
        "button stops refreshing it")


def test_opening_the_performance_tab_runs_its_loader():
    # Executed rather than grepped: the guard above is only safe because the
    # tab switch itself fetches, every time the tab opens.
    r = _switch_tab_harness("""
switchTab('latency');
const fromSwitch = calls.slice();
calls.length = 0;
(_TAB_LOADERS[_activeTab] || (() => {}))();
console.log(JSON.stringify({fromSwitch, fromLoader: calls, activeTab: _activeTab}));
""")
    assert r["activeTab"] == "latency"
    assert r["fromSwitch"] == ["loadLatency"], r
    assert r["fromLoader"] == ["loadLatency"], (
        "with Performance active, refresh()'s tab-loader line must run loadLatency()")


def _switch_tab_harness(tail):
    loader_names = set(re.findall(r"^\s+\w+: \(\) => (\w+)\(\)", _tab_loaders(), re.M))
    loader_names.add("loadRouting")
    stubs = "\n".join(f"function {n}() {{ calls.push('{n}'); }}" for n in sorted(loader_names))
    js = (
        """
const calls = [];
const _liveMeetingsTimer = null;
const _heatMaps = {};
const classes = () => ({ toggle() {}, add() {}, remove() {} });
const document = {
  querySelectorAll: () => [],
  getElementById: () => ({ classList: classes() }),
};
"""
        + stubs + "\n"
        + "let _activeTab = 'overview';\n"
        + _tab_loaders() + "\n"
        + _extract("switchTab") + "\n"
        + tail
    )
    return _node(js)


def test_opening_the_models_tab_fetches_nothing_of_its_own():
    # The Models tab's tables are painted by refresh(). Until 2026-10-31 both
    # the if-chain and _TAB_LOADERS sent it to loadRouting(), which fetched the
    # Model Routing tab's data and painted a hidden grid on every open.
    r = _switch_tab_harness("""
switchTab('models');
const fromSwitch = calls.slice();
calls.length = 0;
(_TAB_LOADERS[_activeTab] || (() => {}))();
console.log(JSON.stringify({fromSwitch, fromLoader: calls}));
""")
    assert r["fromSwitch"] == [], r
    assert r["fromLoader"] == [], r


def test_the_routing_tab_reloads_from_the_global_refresh():
    # Only through _TAB_LOADERS: the first load on open belongs to the
    # switchTab wrapper at the end of the file, and a routing branch in
    # switchTab itself would fetch twice on the first open.
    r = _switch_tab_harness("""
switchTab('routing');
const fromSwitch = calls.slice();
calls.length = 0;
(_TAB_LOADERS[_activeTab] || (() => {}))();
console.log(JSON.stringify({fromSwitch, fromLoader: calls}));
""")
    assert r["fromSwitch"] == [], r
    assert r["fromLoader"] == ["loadRouting"], r


def test_load_path_copy_carries_no_dashes():
    """The load-path comments are copy Scott reads; same rule as the prompts."""
    for name in ("login", "loadAppFilter", "applyAppFilter"):
        body = _extract(name)
        for d in (chr(0x2014), chr(0x2013)):
            assert d not in body, f"{name}() carries a dash"
