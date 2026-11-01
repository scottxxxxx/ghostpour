"""The admin dashboard says when it is loading and when a fetch failed.

Audit of 2026-10-31, confirmed on the live page 2026-11-01: on a cold server
the Overview sat empty for 25 to 45 seconds, headings and no numbers and
nothing on screen saying loading. refresh() awaited Promise.all over
dashboard, users and tiers with no r.ok check, so one 500 threw out of the
await and left every number silently stale; the only sign of success the
page had was the "Updated hh:mm" subtitle. The same `.then(r => r.json())`
shape in the user detail, configs, campaigns and routing loaders painted an
error body as data, and loadProviders, loadEmail, loadErrors,
loadUsageByPlace and the cert-pin banner did `if (!resp.ok) return;`, which
makes a 502 look exactly like a tab with no data. loadAlerts checked nothing.

The fix is one fetch helper (adminFetch: key header, ok check, an Error that
carries status and the server's detail) and one state helper (panelState:
a Loading line that dims the panel, an error line with the status, the
detail and a Retry control). refresh() uses allSettled so a failed payload
marks only the panels it feeds and the others still paint.

Everything behavioural here is EXECUTED in node with fetch and document
stubbed, reusing the extractor and the shape of the stubs in
tests/test_admin_load_path.py, because "the error line shows" and "the
subtitle did not move" are things that happen, not strings in a file. The
source checks at the end are the supplement: a migrated loader must not
grow the silent return back.
"""

import json
import re
import shutil

import pytest

from tests.test_admin_load_path import _extract, _node, _view_state_consts

HTML = "app/static/admin.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")


def _src():
    return open(HTML).read()


# A document where every id and selector resolves to an element that records
# its innerHTML, textContent, style and class list. The period select reads
# 7. Fetch answers per path from RESPONSES; a path listed in PENDING answers
# only once release() is called, which is how a test looks at the page while
# the fetch is still in flight.
_STUB = r"""
const BASE = '';
let adminKey = 'test-key';
let _activeTab = 'overview';
let _tabFresh = {};
let _dashInFlight = false;
let _panelErrors = [];
const _panelRetry = {};
let _openDetailUserId = null;
let _openDetailEmail = null;
let currentUserTier = '', currentUserId = '', simulationState = null;
const _telCharts = {};
const _telForeground = '#fff';
const calls = [];
const fetched = [];
const els = {};
function classes() {
  const set = new Set();
  return { set, toggle(c, on) { if (on) set.add(c); else set.delete(c); }, add(c) { set.add(c); },
           remove(c) { set.delete(c); }, has(c) { return set.has(c); } };
}
function el(id) {
  const e = { id, value: id === 'period' ? '7' : '', style: {}, textContent: id === 'dash-subtitle' ? 'Admin Dashboard' : '',
              innerHTML: '', className: '', classList: classes() };
  return e;
}
const document = {
  getElementById: (id) => els[id] || (els[id] = el(id)),
  querySelector: (sel) => els[sel] || (els[sel] = el(sel)),
  querySelectorAll: () => [],
};
const window = { addEventListener() {}, _userTableState: null };
const RESPONSES = __RESPONSES__;
const PENDING = __PENDING__;
const released = [];
function release() { released.forEach(fn => fn()); released.length = 0; }
function answer(url) {
  const path = url.split('?')[0];
  const r = RESPONSES[path] || { ok: true, status: 200, body: {} };
  return { ok: r.ok, status: r.status, json: async () => r.body };
}
const fetch = (url, init) => {
  fetched.push({ url, key: init && init.headers && init.headers['X-Admin-Key'] });
  const path = url.split('?')[0];
  if (RESPONSES[path] && RESPONSES[path].reject) return Promise.reject(new Error('network down'));
  if (PENDING.includes(path)) return new Promise(res => released.push(() => res(answer(url))));
  return Promise.resolve(answer(url));
};
const settle = () => new Promise(r => setTimeout(r, 10));
let unhandled = 0;
// Counted, not swallowed silently: a rejection out of the harness's own
// tail would otherwise exit 0 with nothing on stdout and read as a hang.
process.on('unhandledRejection', (e) => { unhandled++; console.error('unhandled:', e && e.stack || e); });
// Stubs for what refresh() calls around the group fetch.
function _markAllStale() {}
function _writeViewHash() {}
function refreshCertPinBanner() { calls.push('refreshCertPinBanner'); }
function appQuery() { return ''; }
const _TAB_LOADERS = {};
const card = (label, value, color, sub) => `<div class="card">${label}=${value}</div>`;
const fmt = x => String(x);
const fmtCost = x => '$' + Number(x || 0).toFixed(2);
const fmtMs = x => String(x) + 'ms';
const esc = s => String(s);
function renderTiers(t) { calls.push('renderTiers:' + Object.keys(t).join(',')); }
function updateSimBanner() { calls.push('updateSimBanner'); }
function renderUserRows() { calls.push('renderUserRows'); }
function renderUserAnalytics() { calls.push('renderUserAnalytics'); }
function _renderChart(id) { calls.push('chart:' + id); }
function renderHeatMap(id) { calls.push('map:' + id); }
function loadUsageByPlace() { calls.push('loadUsageByPlace'); }
function showUserDetail() { calls.push('showUserDetail'); }
"""

DASH = {
    "usage": {"total_requests": 10, "successful": 9, "errors": 1, "total_tokens": 100,
              "total_input_tokens": 60, "total_output_tokens": 40, "total_cost_usd": 1.5,
              "avg_latency_ms": 100, "min_latency_ms": 10, "max_latency_ms": 500},
    "today": {"requests": 3, "tokens": 30, "cost_usd": 0.3},
    "users": {"total": 5, "active": 2, "by_tier": {"free": 3}},
    "by_model": [], "by_call_type": [], "by_scenario": [], "allocation_alerts": [],
    "trials": {}, "cache_savings": {}, "daily_usage": [],
}
USERS = {"users": [{"id": "u1", "tier": "admin", "email": "a@b"}], "anonymous_devices": []}
TIERS = {"tiers": {"free": {}, "pro": {}}}


def _ok(body):
    return {"ok": True, "status": 200, "body": body}


def _group_responses(**overrides):
    r = {
        "/webhooks/admin/dashboard": _ok(DASH),
        "/webhooks/admin/users": _ok(USERS),
        "/webhooks/admin/tiers": _ok(TIERS),
    }
    r.update(overrides)
    return r


def _state_js():
    return """JSON.stringify({
  calls, unhandled,
  fetched: fetched.map(f => f.url), keys: fetched.map(f => f.key),
  subtitle: document.getElementById('dash-subtitle').textContent,
  today: (els['today-cards'] || {}).innerHTML || '',
  fresh: _tabFresh, inFlight: _dashInFlight,
  panels: Object.fromEntries(['tab-overview', 'tab-models', 'tab-users', 'tab-tiers', 'tab-providers'].map(id => [id, {
    loading: !!(els[id] && els[id].classList.has('panel-loading')),
    display: (els[id + '-state'] || {style: {}}).style.display,
    cls: (els[id + '-state'] || {}).className,
    text: (els[id + '-state-text'] || {}).textContent,
    retry: (els[id + '-state-retry'] || {style: {}}).style.display,
  }])),
  outcome,
})"""


def _refresh_harness(responses, then, pending=()):
    js = (_STUB.replace("__RESPONSES__", json.dumps(responses)).replace("__PENDING__", json.dumps(list(pending)))
          + _view_state_consts() + "\n")
    for name in ("adminFetch", "panelState", "panelRetry", "panelErrorText", "panel", "refresh"):
        js += _extract(name) + "\n"
    js += """
let outcome = 'pending';
(async () => {
%s
console.log(%s);
})();
""" % (then, _state_js())
    return _node(js)


# ── (a) a failed users fetch: Overview still paints, Users says 500 ─────────

def _users_500():
    return _refresh_harness(
        _group_responses(**{"/webhooks/admin/users": {"ok": False, "status": 500, "body": {"detail": "db locked"}}}),
        """
await refresh().then(() => { outcome = 'resolved'; }, e => { outcome = 'threw: ' + e; });
await settle();
""")


def test_a_users_500_still_renders_the_dashboard_numbers():
    r = _users_500()
    assert r["outcome"] == "resolved", (
        f"refresh() {r['outcome']}; one failed group fetch must not escape the loader")
    assert "Requests Today=3" in r["today"], (
        f"the dashboard payload arrived and was not painted: today-cards is {r['today']!r} "
        "(before 2026-11-01 one 500 in Promise.all blanked every panel)")
    assert "renderTiers:free,pro" in r["calls"], "the tiers payload arrived and must paint"


def test_a_users_500_marks_only_the_users_panel():
    r = _users_500()
    users = r["panels"]["tab-users"]
    assert users["display"] == "block" and "error" in (users["cls"] or ""), (
        f"the Users panel shows no error line after its fetch returned 500: {users}")
    assert "500" in users["text"], f"the error line must carry the status: {users['text']!r}"
    assert "db locked" in users["text"], f"and the server's detail: {users['text']!r}"
    assert users["retry"] == "", "a failed panel offers Retry"
    for ok_panel in ("tab-overview", "tab-models", "tab-tiers"):
        p = r["panels"][ok_panel]
        assert p["display"] == "none" and not p["loading"], f"{ok_panel} fetched fine and must read ready: {p}"


def test_a_users_500_does_not_move_the_updated_subtitle():
    r = _users_500()
    assert r["subtitle"] == "Admin Dashboard", (
        f"the subtitle moved to {r['subtitle']!r} although the users fetch failed; it "
        "was the one sign of success the page had and must only move on a full group")
    assert r["fresh"].get("dashboard") is not True, "a partial group stays stale so the next open retries"
    assert r["unhandled"] == 0, "no rejection may escape refresh()"


def test_a_full_group_moves_the_subtitle_and_clears_every_state():
    # Positive control for the three above.
    r = _refresh_harness(_group_responses(), """
await refresh().then(() => { outcome = 'resolved'; }, e => { outcome = 'threw: ' + e; });
await settle();
""")
    assert r["outcome"] == "resolved"
    assert r["subtitle"].startswith("Updated "), r["subtitle"]
    assert r["fresh"]["dashboard"] is True
    for pid in ("tab-overview", "tab-models", "tab-users", "tab-tiers"):
        assert r["panels"][pid]["display"] == "none" and not r["panels"][pid]["loading"], r["panels"][pid]
    assert all(k == "test-key" for k in r["keys"]), f"adminFetch must send the admin key: {r['keys']}"
    assert r["inFlight"] is False


def test_a_dashboard_network_error_marks_overview_and_models_and_names_it():
    r = _refresh_harness(_group_responses(**{"/webhooks/admin/dashboard": {"reject": True}}), """
await refresh().then(() => { outcome = 'resolved'; }, e => { outcome = 'threw: ' + e; });
await settle();
""")
    assert r["outcome"] == "resolved"
    for pid in ("tab-overview", "tab-models"):
        p = r["panels"][pid]
        assert p["display"] == "block" and "error" in p["cls"], f"{pid}: {p}"
        assert "network down" in p["text"], p["text"]
    assert r["panels"]["tab-users"]["display"] == "none"
    assert "renderUserRows" in r["calls"], "the users payload arrived and must paint"
    assert r["subtitle"] == "Admin Dashboard"


# ── (b) while the group fetch is pending, Overview says it is loading ───────

def test_overview_is_in_the_loading_state_while_the_group_fetch_is_pending():
    r = _refresh_harness(_group_responses(), """
const p = refresh().then(() => { outcome = 'resolved'; }, e => { outcome = 'threw: ' + e; });
await settle();
const during = JSON.parse(JSON.stringify({
  loading: els['tab-overview'].classList.has('panel-loading'),
  display: els['tab-overview-state'].style.display,
  text: els['tab-overview-state-text'].textContent,
  cls: els['tab-overview-state'].className,
  subtitle: document.getElementById('dash-subtitle').textContent,
  outcome,
}));
release();
await p;
await settle();
calls.push('during:' + JSON.stringify(during));
""", pending=["/webhooks/admin/dashboard", "/webhooks/admin/users", "/webhooks/admin/tiers"])
    during = json.loads([c for c in r["calls"] if c.startswith("during:")][0][len("during:"):])
    assert during["outcome"] == "pending", "the fetch stub must still be holding the group"
    assert during["loading"] is True, (
        f"the Overview panel is not dimmed while its fetch is in flight: {during} "
        "(cold server, 25 to 45 s of headings over nothing, 2026-11-01)")
    assert during["display"] == "block" and during["text"] == "Loading...", during
    assert "loading" in during["cls"], during
    assert during["subtitle"] == "Admin Dashboard", "nothing has succeeded yet"
    # And after the answers land: ready, subtitle moved.
    assert r["outcome"] == "resolved"
    after = r["panels"]["tab-overview"]
    assert after["loading"] is False and after["display"] == "none", after
    assert r["subtitle"].startswith("Updated ")


# ── (c) a tab loader that answers 502 says so instead of returning ──────────

def _providers_harness(responses, then):
    js = _STUB.replace("__RESPONSES__", json.dumps(responses)).replace("__PENDING__", "[]")
    for name in ("adminFetch", "panelState", "panelRetry", "panelErrorText", "withPanelState",
                 "loadProviders", "_loadProviders"):
        js += _extract(name) + "\n"
    js += """
let outcome = 'pending';
(async () => {
%s
console.log(JSON.stringify({
  fetched: fetched.map(f => f.url), unhandled, outcome,
  cards: els['provider-cards'] ? els['provider-cards'].innerHTML : null,
  loading: els['tab-providers'].classList.has('panel-loading'),
  display: els['tab-providers-state'].style.display,
  cls: els['tab-providers-state'].className,
  text: els['tab-providers-state-text'].textContent,
  retry: els['tab-providers-state-retry'].style.display,
}));
})();
""" % then
    return _node(js)


def test_load_providers_on_a_502_shows_an_inline_error_line():
    r = _providers_harness(
        {"/webhooks/admin/provider-status": {"ok": False, "status": 502, "body": {"detail": "upstream down"}}},
        "await loadProviders().then(() => { outcome = 'resolved'; }, e => { outcome = 'threw: ' + e; });")
    assert r["outcome"] == "resolved", r["outcome"]
    assert r["display"] == "block" and "error" in r["cls"], (
        f"loadProviders returned silently on a 502; the panel shows no error line: {r}")
    assert "502" in r["text"] and "upstream down" in r["text"], r["text"]
    assert r["retry"] == "", "the error line offers Retry"
    assert r["loading"] is False, "the panel is no longer dimmed once the answer is in"
    assert not r["cards"], "nothing was painted from an error body"
    assert r["unhandled"] == 0


def test_retry_re_runs_the_loader():
    # (d) The first answer is a 502; Retry must fetch again, and when the second
    # answer is good the panel paints and the error line clears.
    r = _providers_harness(
        {"/webhooks/admin/provider-status": {"ok": False, "status": 502, "body": {"detail": "upstream down"}}},
        """
await loadProviders();
const afterFail = fetched.length;
RESPONSES['/webhooks/admin/provider-status'] = { ok: true, status: 200,
  body: { providers: { anthropic: { status: 'ok', display_name: 'Anthropic', key_masked: 'sk-...', console_url: 'x' } } } };
panelRetry('tab-providers');
await settle();
outcome = 'fetches:' + afterFail + '->' + fetched.length;
""")
    assert r["outcome"] == "fetches:1->2", (
        f"Retry did not re-run loadProviders: {r['outcome']} (fetched {r['fetched']})")
    assert r["display"] == "none", f"the error line must clear once the retry succeeds: {r}"
    assert r["cards"] and "Anthropic" in r["cards"], "the retry's payload paints the cards"


def test_retry_with_nothing_registered_is_a_no_op():
    js = _STUB.replace("__RESPONSES__", "{}").replace("__PENDING__", "[]")
    js += _extract("panelRetry") + "\npanelRetry('tab-nowhere');\nconsole.log(JSON.stringify({ok: true}));\n"
    assert _node(js)["ok"] is True


# ── source: the migrated loaders stay migrated ──────────────────────────────

MIGRATED = (
    "refresh", "_loadUsageByPlace", "refreshCertPinBanner", "_showUserDetail", "loadSearchActivity",
    "loadQueryLog", "_loadProviders", "_loadEmail", "_loadErrors", "_loadConfigs", "loadRegistry",
    "_loadCampaigns", "_loadRouting", "_loadAlerts", "loadAlertCategories",
)


@pytest.mark.parametrize("name", MIGRATED)
def test_a_migrated_loader_fetches_through_admin_fetch_only(name):
    body = _extract(name)
    code = "\n".join(ln for ln in body.split("\n") if not ln.lstrip().startswith("//"))
    assert "adminFetch(" in code, f"{name}() no longer goes through adminFetch"
    assert "if (!resp.ok) return;" not in code and "if (!r.ok) return;" not in code, (
        f"{name}() grew the silent return back; a 502 then looks like a tab with no data")
    assert ".then(r => r.json())" not in code, (
        f"{name}() reads the body without checking the status again")
    assert "'X-Admin-Key'" not in code, f"{name}() hand-rolls the header adminFetch adds"


def test_refresh_settles_the_group_rather_than_racing_it():
    code = "\n".join(ln for ln in _extract("refresh").split("\n") if not ln.lstrip().startswith("//"))
    assert "Promise.allSettled(" in code, "one failed group fetch must not throw out of the await"
    assert "Promise.all(" not in code


# The public loaders stay under their own tab's state. Their names are what
# _TAB_LOADERS and the onclick handlers call, so the wrapper carries the
# name and the body moved to the underscore twin.
WRAPPED = {
    "loadProviders": "tab-providers", "loadEmail": "tab-email", "loadErrors": "tab-errors",
    "loadConfigs": "tab-configs", "loadCampaigns": "tab-campaigns", "loadRouting": "tab-routing",
    "loadAlerts": "tab-alerts", "loadUsageByPlace": "usage-by-place", "showUserDetail": "user-detail",
}


@pytest.mark.parametrize("name,panel_id", sorted(WRAPPED.items()))
def test_a_public_loader_runs_under_its_panel_state(name, panel_id):
    body = _extract(name)
    assert f"withPanelState('{panel_id}'" in body, f"{name}() is not wrapped in its panel's state"
    assert f"_{name}" in body, f"{name}() must hand the fetching to _{name}()"


def test_every_wired_panel_carries_its_state_markup():
    src = _src()
    wired = set(WRAPPED.values()) | {"tab-overview", "tab-models", "tab-users", "tab-tiers"}
    for pid in sorted(wired):
        for suffix in ("-state", "-state-text", "-state-retry"):
            assert f'id="{pid}{suffix}"' in src, f"{pid} has no {suffix} element for panelState to find"
        assert f"onclick=\"panelRetry('{pid}')\"" in src, f"{pid}'s Retry button does not call panelRetry"


def test_dash_tabs_are_exactly_the_panels_refresh_marks():
    # refresh() sets loading on _DASH_TABS and marks overview, models, users
    # and tiers from the three payloads; a fifth group tab would load forever.
    m = re.search(r"^const _DASH_TABS = \[(.*)\];$", _src(), re.M)
    assert m, "_DASH_TABS must stay a one-line const"
    tabs = set(re.findall(r"'(\w+)'", m.group(1)))
    assert tabs == {"overview", "models", "users", "tiers"}, tabs
    code = _extract("refresh")
    for pid in ("tab-overview", "tab-models", "tab-users", "tab-tiers"):
        assert f"'{pid}'" in code, f"refresh() never marks {pid}"


def test_helpers_are_top_level_declarations():
    src = _src()
    for name in ("adminFetch", "panelState", "panelRetry", "panelErrorText", "withPanelState"):
        assert re.search(rf"^(async )?function {name}\(", src, re.M), (
            f"{name}() must be a hoisted top-level declaration like panel() and esc()")


def test_states_copy_carries_no_dashes():
    """Same standing rule as the rest of the page and this file. Only the
    regions this change wrote: the helpers block, the CSS, the public
    wrappers, the head of refresh() (the renderers below `const days` belong
    to other regions and predate the rule, as test_admin_view_state notes),
    the loaders whose fetch lines were rewritten, and this file."""
    src = _src()
    regions = [src[src.index("// --- Loading and error states"):src.index("// Which deployment is this page driving?")]]
    regions.append(src[src.index(".panel-state {"):src.index(".panel-loading")])
    regions += [_extract(n) for n in tuple(WRAPPED) + ("panelState", "panelRetry", "panelErrorText", "withPanelState", "adminFetch")]
    regions += [_extract(n) for n in ("refreshCertPinBanner", "_loadUsageByPlace", "_loadProviders", "_loadEmail",
                                      "_loadErrors", "_loadAlerts", "loadAlertCategories", "_loadRouting",
                                      "_loadCampaigns", "loadRegistry")]
    head = _extract("refresh")
    regions.append(head[:head.index("const u = dash.usage")])
    regions.append(open(__file__).read())
    for region in regions:
        for d in (chr(0x2014), chr(0x2013)):
            assert d not in region, region[:80]


def test_a_loading_or_error_line_is_actually_visible():
    # The base CSS rule hides .panel-state; the inline style is what shows it.
    # An empty inline value ('') falls back to the hidden rule, which is what
    # shipped in #1179: the panel dimmed but no line ever appeared (build
    # 81c6986 on the live page, 2026-11-01). So the inline value must be a
    # visible one while loading or in error, and the CSS must hide by default.
    src = _src()
    assert re.search(r"\.panel-state\s*\{[^}]*display:\s*none", src), "the base rule hides the line"
    assert "line.style.display = state === 'ready' ? 'none' : 'block';" in src
    assert "line.style.display = state === 'ready' ? 'none' : '';" not in src
