"""The admin dashboard's view survives a reload, and a filter change fetches
only the tab on screen.

Audit and live page of 2026-10-31. Nothing persisted across a reload: the
active tab, the period select and the app filter all reset to Overview /
7 days / All apps. And a period or app change ran refresh(), which fetched
the dashboard group (dashboard, users, tiers) AND the active tab's loader,
6 to 8 requests whatever tab was open, while switchTab re-fetched 13 tabs on
every open with no notion of staleness.

The fix mirrors tab, period and app into location.hash
(#tab=users&days=30&app=ss,n400), restores them in login() before its one
refresh(), and keeps a per-tab freshness map so a filter change marks every
tab stale and refreshes only the active one, and an open fetches only when
the tab is stale or has never loaded.

Everything here is EXECUTED in node with fetch, document, location and
history stubbed, reusing the extractor and the shape of the stubs in
tests/test_admin_load_path.py, because the claims are about what ran and how
many times, which a source-text assertion cannot count.
"""

import json
import shutil

import pytest

from tests.test_admin_load_path import _extract, _node, _tab_loaders, _view_state_consts

HTML = "app/static/admin.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")


# A document big enough for login(), the filter, the tab strip and the head
# of refresh(): every id resolves to an element; #period carries the four
# real options; the app checkboxes come into being when loadAppFilter()
# paints the list, keyed by value, so _checkedApps() can find them.
_STUB = r"""
const BASE = '';
let adminKey = '';
let _APPS = [];
let _activeTab = 'overview';
let _tabFresh = {};
let _panelErrors = [];
let _openDetailUserId = null;
const _liveMeetingsTimer = null;
const _heatMaps = {};
const events = [];
const fetched = [];
const calls = [];
const els = {};
const checkboxes = {};
const classes = () => ({ toggle() {}, add() {}, remove() {} });
function el(id) {
  const e = { id, value: id === 'admin-key' ? 'test-key' : '', style: {}, textContent: '',
              _html: '', classList: classes(), checked: id === 'app-filter-all' };
  if (id === 'period') { e.value = '7'; e.options = ['1', '7', '30', '90'].map(v => ({ value: v })); }
  Object.defineProperty(e, 'innerHTML', {
    get() { return this._html; },
    set(v) {
      this._html = v; events.push('populate:' + id);
      if (id === 'app-filter-list') {
        for (const m of v.matchAll(/value="([^"]+)"/g)) {
          checkboxes[m[1]] = checkboxes[m[1]] || { value: m[1], checked: false };
        }
      }
    },
  });
  return e;
}
const document = {
  // Sections exist only for the page's real tabs; any other id resolves, as
  // the load-path stub's do, so a mistyped tab in the hash is seen as missing.
  getElementById: (id) => id.startsWith('tab-') && !TABS.includes(id) ? null : (els[id] || (els[id] = el(id))),
  querySelectorAll: (sel) => sel === '.app-filter-cb' ? Object.values(checkboxes) : [],
  querySelector: () => null,
  addEventListener() {},
  hidden: false,
};
const localStorage = { setItem() {}, getItem() { return null; }, removeItem() {} };
const location = { hash: HASH };
const history = { replaceState(_s, _t, url) { location.hash = url; } };
const window = { addEventListener() {} };
const APPS_BODY = { default_app: 'ss',
  apps: [{id: 'ss', label: 'ShoulderSurf'}, {id: 'n400', label: 'N-400'}] };
function fakeResponse() {
  return Promise.resolve({ ok: true, status: 200, json: async () => APPS_BODY });
}
const _origFetch = (url) => { fetched.push(url); return fakeResponse(); };
const fetch = (url) => { fetched.push(url); return fakeResponse(); };
function handleAdminAuthFailure() { events.push('authFailure'); }
function refreshCertPinBanner() {}
// The real refresh() is cut short by the apps body once it awaits the
// dashboard payload (tiers.tiers is not there). The fetches it asked for are
// already recorded by then, which is all these tests read.
process.on('unhandledRejection', () => {});
"""


def _loader_stubs():
    import re
    names = sorted(set(re.findall(r"^\s+\w+: \(\) => (\w+)\(\)", _tab_loaders(), re.M)))
    # Each loader fetches once, so the fetch count and the call count can be
    # compared: a loader that ran without fetching would hide a double load.
    return "\n".join(
        f"function {n}() {{ calls.push('{n}'); fetch('/webhooks/admin/{n[4:].lower()}'); }}"
        for n in names)


def _tab_ids():
    import re
    ids = re.findall(r'<div id="(tab-[a-z]+)" class="section', open(HTML).read())
    assert len(ids) >= 18, ids
    return ids


def _page(hash_value, real_refresh):
    js = _STUB.replace("HASH", json.dumps(hash_value)).replace("TABS", json.dumps(_tab_ids()))
    js += _loader_stubs() + "\n"
    js += _view_state_consts() + "\n" + _tab_loaders() + "\n"
    for name in ("login", "loadAppFilter", "applyAppFilter", "_checkedApps", "appQuery",
                 "onAppFilterChange", "onAppFilterAll", "_markAllStale", "_viewHash",
                 "_writeViewHash", "_restoreViewState", "_showTab", "_loadTabIfStale",
                 "switchTab"):
        js += _extract(name) + "\n"
    if real_refresh:
        js += _extract("refresh") + "\n"
    else:
        js += "let refreshCalls = 0;\nfunction refresh() { refreshCalls++; events.push('refresh'); }\n"
    return js


def _state_js():
    return """JSON.stringify({
  refreshCalls: typeof refreshCalls === 'undefined' ? null : refreshCalls,
  calls, fetched, events,
  period: document.getElementById('period').value,
  appValue: document.getElementById('app').value,
  checked: Object.values(checkboxes).filter(c => c.checked).map(c => c.value),
  allChecked: document.getElementById('app-filter-all').checked,
  activeTab: _activeTab, hash: location.hash, fresh: _tabFresh,
  authError: document.getElementById('auth-error').textContent,
})"""


# ── (a) the load path restores the hash before its one refresh() ──────────

def _load_with_hash(hash_value):
    js = _page(hash_value, real_refresh=False) + """
login();
setTimeout(() => console.log(%s), 50);
""" % _state_js()
    return _node(js)


def test_a_reload_lands_on_the_tab_period_and_app_the_hash_names():
    r = _load_with_hash("#tab=users&days=30&app=ss")
    assert r["authError"] == "", r
    assert r["period"] == "30", f"#period was not restored: {r['period']!r}"
    assert r["checked"] == ["ss"], f"the app checkboxes were not restored: {r['checked']}"
    assert r["allChecked"] is False, "ticking an app must clear All apps"
    assert r["appValue"] == "ss", "the hidden #app must carry the restored selection for appQuery()"
    assert r["activeTab"] == "users", f"the tab was not switched: {r['activeTab']!r}"


def test_a_reload_still_runs_refresh_exactly_once_and_after_the_restore():
    r = _load_with_hash("#tab=users&days=30&app=ss")
    assert r["refreshCalls"] == 1, (
        f"refresh() ran {r['refreshCalls']} times on a load with a hash; events: {r['events']}. "
        "Restoring the view must not add a second refresh (PR #1171 made it one).")
    # The restore must come first, or the one refresh() fetches the defaults
    # and the restored view needs a second round.
    assert r["calls"] == [], f"switching to the restored tab must not run a loader itself: {r['calls']}"
    populated = r["events"].index("populate:app-filter-list")
    assert populated < r["events"].index("refresh"), r["events"]


def test_two_apps_in_the_hash_tick_both_boxes():
    r = _load_with_hash("#tab=overview&days=7&app=ss,n400")
    assert sorted(r["checked"]) == ["n400", "ss"], r["checked"]
    assert r["appValue"] == "ss,n400"


def test_a_bad_hash_degrades_to_the_defaults():
    r = _load_with_hash("#tab=nope&days=12&app=")
    assert r["authError"] == "", r
    assert r["activeTab"] == "overview"
    assert r["period"] == "7"
    assert r["checked"] == [] and r["allChecked"] is True
    assert r["refreshCalls"] == 1


def test_no_hash_is_the_default_view():
    r = _load_with_hash("")
    assert r["activeTab"] == "overview" and r["period"] == "7" and r["appValue"] == ""
    assert r["refreshCalls"] == 1


# ── (b) a period change from a non-overview tab fetches only that tab ──────

def _after_load_on(tab, then):
    # A page after login: the dashboard group was painted by the first
    # refresh(), the operator opened Performance and then `tab`.
    js = _page("", real_refresh=True) + """
_tabFresh = { dashboard: true, latency: true };
_activeTab = %s;
calls.length = 0; fetched.length = 0;
%s
console.log(%s);
""" % (json.dumps(tab), then, _state_js())
    return _node(js)


def test_a_period_change_on_errors_fetches_only_the_errors_loader():
    r = _after_load_on("errors", """
document.getElementById('period').value = '30';
refresh(true);
""")
    assert r["calls"] == ["loadErrors"], (
        f"a period change on the Errors tab ran {r['calls']}; it must run only "
        "that tab's loader (2026-10-31: 6 to 8 requests whatever tab was open)")
    assert r["fetched"] == ["/webhooks/admin/errors"], (
        f"a period change on the Errors tab fetched {r['fetched']}; the dashboard "
        "group (dashboard, users, tiers) belongs to the Overview group and must wait")
    assert "days=30" in r["hash"], r["hash"]


def test_a_period_change_marks_the_other_tabs_stale_and_the_active_one_fresh():
    r = _after_load_on("errors", """
document.getElementById('period').value = '30';
refresh(true);
""")
    assert r["fresh"]["dashboard"] is False, r["fresh"]
    assert r["fresh"]["latency"] is False, r["fresh"]
    assert r["fresh"]["errors"] is True, r["fresh"]


def test_a_period_change_on_overview_does_fetch_the_dashboard_group():
    # The positive control for the two tests above: a refresh() that never
    # reached the group fetch would pass them for the wrong reason.
    r = _after_load_on("overview", """
document.getElementById('period').value = '30';
refresh(true);
""")
    paths = [u.split("?")[0] for u in r["fetched"]]
    assert paths == ["/webhooks/admin/dashboard", "/webhooks/admin/users", "/webhooks/admin/tiers"], r["fetched"]
    assert "days=30" in r["fetched"][0], r["fetched"][0]
    assert r["calls"] == [], "Overview has no loader of its own"
    assert r["fresh"]["dashboard"] is True


def test_an_app_change_takes_the_same_path_as_a_period_change():
    r = _after_load_on("errors", """
checkboxes['ss'] = { value: 'ss', checked: true };
onAppFilterChange();
""")
    assert r["calls"] == ["loadErrors"], r["calls"]
    assert r["fetched"] == ["/webhooks/admin/errors"], r["fetched"]
    assert r["fresh"]["dashboard"] is False
    assert r["hash"].endswith("&app=ss"), r["hash"]


# ── (c) opening a tab fetches once; after a filter change, again ────────────

def test_opening_a_tab_twice_without_a_change_fetches_once():
    r = _after_load_on("overview", """
switchTab('errors');
switchTab('overview');
switchTab('errors');
""")
    assert r["calls"] == ["loadErrors"], (
        f"two opens of Errors with nothing changed ran {r['calls']}; the second "
        "open must reuse the data the first one fetched")
    assert r["fetched"] == ["/webhooks/admin/errors"], r["fetched"]


def test_opening_a_tab_after_a_period_change_fetches_again():
    r = _after_load_on("overview", """
switchTab('errors');
switchTab('overview');
document.getElementById('period').value = '30';
refresh(true);
switchTab('errors');
""")
    assert r["calls"] == ["loadErrors", "loadErrors"], r["calls"]


def test_opening_a_stale_overview_refreshes_the_group_once():
    # Period changed while on Errors, then back to Overview: the group is
    # stale, so the switch runs refresh(); a second visit does not.
    r = _after_load_on("errors", """
document.getElementById('period').value = '30';
refresh(true);
fetched.length = 0; calls.length = 0;
switchTab('overview');
switchTab('errors');
switchTab('users');
""")
    paths = [u.split("?")[0] for u in r["fetched"]]
    assert paths == ["/webhooks/admin/dashboard", "/webhooks/admin/users", "/webhooks/admin/tiers"], r["fetched"]
    assert r["calls"] == [], f"Errors was fetched by the period change and must not be again: {r['calls']}"


def test_the_polling_tabs_run_their_loader_on_every_open():
    # Live Log and live meetings arm their own timers inside their loaders
    # and stand them down off screen; skipping a second open would leave the
    # tab with no poll. Their behaviour is not this change's to alter.
    r = _after_load_on("overview", """
switchTab('livelog'); switchTab('overview'); switchTab('livelog');
switchTab('telemetry'); switchTab('overview'); switchTab('telemetry');
""")
    assert r["calls"] == ["loadLiveLog", "loadLiveLog", "loadTelemetry", "loadTelemetry"], r["calls"]


def test_the_global_refresh_forces_the_active_tab():
    r = _after_load_on("overview", """
switchTab('errors');
refresh();
""")
    assert r["calls"] == ["loadErrors", "loadErrors"], r["calls"]
    assert r["fresh"]["dashboard"] is True, "a plain refresh() is not a filter change"


# ── (d) switching tabs writes the hash ─────────────────────────────────────

def test_switching_tabs_writes_the_hash():
    r = _after_load_on("overview", "switchTab('users');")
    assert r["hash"] == "#tab=users&days=7", r["hash"]


def test_the_hash_carries_the_app_selection_with_a_literal_comma():
    r = _after_load_on("overview", """
document.getElementById('app').value = 'ss,n400';
document.getElementById('period').value = '90';
switchTab('latency');
""")
    assert r["hash"] == "#tab=latency&days=90&app=ss,n400", r["hash"]


def test_view_state_copy_carries_no_dashes():
    """Same standing rule as the rest of the page."""
    for name in ("_viewHash", "_writeViewHash", "_restoreViewState", "_showTab",
                 "_loadTabIfStale", "_markAllStale", "switchTab", "refresh"):
        body = _extract(name)
        if name == "refresh":
            # Only the head is this change's copy; the panel renderers below
            # `const days` belong to other regions and predate the rule.
            body = body[:body.index("const days = ")]
        for d in (chr(0x2014), chr(0x2013)):
            assert d not in body, f"{name}() carries a dash"
    src = open(HTML).read()
    block = src[src.index("// --- View state: the URL hash"):src.index("const _TAB_LOADERS = {")]
    for d in (chr(0x2014), chr(0x2013)):
        assert d not in block
