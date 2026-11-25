"""The shared card grid, the chart theme merge and the heat map (2026-11-25).

Scott: the user map was small, its zoom "doesn't work", a country could not
be clicked, and the page was "not laid out well". Measured at 1835 px: the
Users top row was five tracks holding six units of card, so one card sat
alone on a row that was 19% full; sixteen of twenty charts drew a grey panel
with a zoom toolbar because _renderChart let a caller's `chart` replace its
dark defaults; every refresh rebuilt the map and threw the zoom away.

These run the real functions in node where there is logic to run, and read
the source only for wiring that has no logic of its own.
"""

import json
import re

from tests.test_admin_load_path import _extract, _node, _src

_GRID_JS = "\n".join(_extract(n) for n in ("_gcd", "_packGrid", "_planGrid"))


def _plan(spans, max_cols):
    js_spans = "[" + ",".join("Infinity" if s == "all" else str(s) for s in spans) + "]"
    return _node(_GRID_JS + f"\nconsole.log(JSON.stringify(_planGrid({js_spans}, {max_cols})));")


def _rows(plan):
    rows = {}
    for at in plan["place"]:
        rows.setdefault(at["row"], []).append(at)
    return [sorted(r, key=lambda a: a["start"]) for _, r in sorted(rows.items())]


def _assert_no_gaps(plan):
    for row in _rows(plan):
        cursor = 1
        for at in row:
            assert at["start"] == cursor, f"gap or overlap before column {at['start']} in {row}"
            cursor += at["span"]
        assert cursor - 1 == plan["tracks"], f"row stops at {cursor - 1} of {plan['tracks']} tracks: {row}"


def test_six_units_in_five_tracks_go_three_across_not_five_and_one():
    """The Users top row as it was: a double-width map and four cards."""
    plan = _plan([2, 1, 1, 1, 1], 5)
    assert plan["cols"] == 3, "five tracks left one card alone on a row 19% full"
    assert len(_rows(plan)) == 2
    _assert_no_gaps(plan)


def test_a_half_filled_last_row_does_not_cost_the_charts_their_columns():
    """Telemetry's chart grid. Counting empty cells alone picked two columns
    (packed solid), which made every chart a full-width band and the page
    longer. Four columns with the two small cards sharing the last row is the
    layout wanted."""
    plan = _plan([2, 2, 2, "all", 2, 1, 1], 4)
    assert plan["cols"] == 4
    _assert_no_gaps(plan)
    last = _rows(plan)[-1]
    assert len(last) == 2 and last[0]["span"] == last[1]["span"], "the two small cards split their row evenly"


def test_ten_tiles_in_nine_tracks_make_two_rows_of_five():
    plan = _plan([1] * 10, 9)
    assert plan["cols"] == 5, "nine tracks left the tenth tile alone (Acquisition KPIs, fill 11%)"
    _assert_no_gaps(plan)


def test_what_fits_on_one_row_stays_on_one_row():
    plan = _plan([1, 1, 1], 9)
    assert plan["cols"] == 3 and len(_rows(plan)) == 1
    _assert_no_gaps(plan)


def test_no_arrangement_leaves_a_gap_and_a_double_never_overflows_one_column():
    cases = [[1] * n for n in range(1, 12)]
    cases += [[2, 1, 1, 1], [2, 2, 1], [1, 2, 1, 2, 1], ["all", 1, 1, 1], [2, "all", 1], [2], ["all"],
              [2, 2, 2, 2, 2, 1, 1], [1, 1, 2, 2]]
    for spans in cases:
        for max_cols in range(1, 9):
            plan = _plan(spans, max_cols)
            assert 1 <= plan["cols"] <= max_cols, (spans, max_cols, plan["cols"])
            assert len(plan["place"]) == len(spans)
            _assert_no_gaps(plan)


def test_a_callers_chart_options_extend_the_dark_defaults():
    js = r"""
const _telCharts = {};
const _telTheme = {mode: 'dark'}; const _telForeground = '#94a3b8'; const _telGrid = '#293342';
let seen = null;
class ApexCharts { constructor(el, o) { seen = o; } render() {} destroy() {} }
const document = {getElementById: () => ({})};
""" + _extract("_renderChart") + r"""
_renderChart('a', {series: [], chart: {type: 'bar', height: 230}, tooltip: {shared: true}});
const plain = seen;
_renderChart('b', {series: [], chart: {type: 'line', toolbar: {show: true}}});
console.log(JSON.stringify({plain: plain, own: seen}));
"""
    out = _node(js)
    chart = out["plain"]["chart"]
    assert chart["type"] == "bar" and chart["height"] == 230, "the caller's own settings survive"
    assert chart["background"] == "transparent", "a chart with its own `chart` drew a grey rgb(66,66,66) panel"
    assert chart["toolbar"] == {"show": False}, "and grew a zoom and download toolbar"
    assert chart["foreColor"] == "#94a3b8"
    assert out["plain"]["tooltip"] == {"theme": "dark", "shared": True}
    assert out["plain"]["theme"] == {"mode": "dark"}
    assert out["own"]["chart"]["toolbar"] == {"show": True}, "a caller that asks for the toolbar still gets it"


_MAP_HARNESS = r"""
const built = [];
function fakeEl() {
  const el = {
    innerHTML: '', title: '', className: '', textContent: '', dataset: {}, style: {}, offsetWidth: 800,
    classList: {add() {}, remove() {}, toggle() {}},
    querySelector: () => fakeEl(), querySelectorAll: () => [],
    addEventListener() {}, appendChild() {}, setAttribute() {},
  };
  return el;
}
const els = {};
const document = {
  getElementById: id => (els[id] = els[id] || fakeEl()),
  createElement: () => fakeEl(),
  addEventListener() {},
};
class jsVectorMap {
  constructor(o) {
    this.o = o; this._baseScale = 2; this._baseTransX = 0; this._baseTransY = 5;
    this.scale = 2; this.transX = 0; this.transY = 5; this.regions = {}; this.applied = 0;
    built.push(this);
  }
  _applyTransform() { this.applied++; }
  destroy() { this.destroyed = true; }
  updateSize() {}
}
jsVectorMap.maps = {};
const window = {jsVectorMap, US_STATE_CODE_BY_NAME: {Texas: 'US-TX'}};
const _heatMaps = {};
function heatScale() { return {}; }
"""

_MAP_FNS = ("_sameCounts", "renderHeatMap", "setHeatMapView", "_heatHint", "_setHeatActive",
            "_markHeatSelection", "resetHeatMap", "focusHeatMapRegion", "_heatFocusBox",
            "_wireHeatMap", "_restoreHeatView", "_drawHeatMap")


def _map(script):
    js = _MAP_HARNESS + "\n".join(_extract(n) for n in _MAP_FNS) + "\n" + script
    return _node(js)


def test_a_refresh_with_the_same_counts_leaves_the_map_alone_and_new_counts_keep_the_view():
    out = _map(r"""
renderHeatMap('m', {US: 3, IN: 1}, {'US-TX': 2}, 'user');
const first = built[0];
first.scale = 7; first.transX = -40; first.transY = -12;       // the operator zoomed and panned
renderHeatMap('m', {IN: 1, US: 3}, {'US-TX': 2}, 'user');      // a refresh, nothing changed
const afterSame = built.length;
renderHeatMap('m', {US: 4, IN: 1}, {'US-TX': 2}, 'user');      // a refresh with one more user
const second = built[1];
console.log(JSON.stringify({afterSame, total: built.length, firstDestroyed: !!first.destroyed,
  scale: second.scale, x: second.transX, y: second.transY, applied: second.applied,
  zoomButtons: second.o.zoomButtons, selector: second.o.selector, usOffered: els['m-toggle'].innerHTML.includes('United States')}));
""")
    assert out["afterSame"] == 1, "identical data must not rebuild the map (it reset zoom and pan on every refresh)"
    assert out["total"] == 2 and out["firstDestroyed"]
    assert (out["scale"], out["x"], out["y"]) == (7, -40, -12), "the rebuilt map opens where the old one was"
    assert out["applied"] >= 1
    assert out["zoomButtons"] is True
    assert out["selector"] == "#m .heat-canvas", "the canvas is a child, so the wheel guard on the parent runs first"
    assert out["usOffered"], "the US view is offered when the vendored map has loaded, with jsVectorMap.maps empty"


def test_changing_the_view_starts_from_the_whole_map():
    out = _map(r"""
renderHeatMap('m', {US: 3}, {'US-TX': 2}, 'user');
built[0].scale = 7; built[0].transX = -40;
_heatMaps.m.selected = 'US';
setHeatMapView('m', 'us');
console.log(JSON.stringify({total: built.length, map: built[1].o.map, scale: built[1].scale, selected: _heatMaps.m.selected}));
""")
    assert out["total"] == 2 and out["map"] == "us-states"
    assert out["scale"] == 2, "a zoom on the world map means nothing on the US map"
    assert out["selected"] is None


def test_clicking_a_region_zooms_to_it_selects_it_and_a_second_click_lets_go():
    out = _map(r"""
renderHeatMap('m', {BR: 1}, {}, 'user');
const m = built[0];
m._width = 800; m._height = 400;
const calls = [];
m._setScale = (...a) => calls.push(a);
m.regions.BR = {element: {shape: {node: {
  getBBox: () => ({x: 100, y: 200, width: 50, height: 40}),
  getAttribute: () => 'M100,200l50,0l0,40l-50,0z',
  classList: {add() {}, remove() {}}, parentNode: {appendChild() {}},
}}}};
let dragSelected = null;
_heatMaps.m.dragged = true;
m.o.onRegionClick({}, 'BR');                 // the click the library reports at the end of a drag
dragSelected = _heatMaps.m.selected;
_heatMaps.m.dragged = false;
m.o.onRegionClick({}, 'BR');
const selected = _heatMaps.m.selected;
m.o.onRegionClick({}, 'BR');
console.log(JSON.stringify({dragSelected, selected, calls, after: _heatMaps.m.selected, scale: m.scale, y: m.transY}));
""")
    assert out["dragSelected"] is None, "a drag that ends over a country must not select it"
    assert out["selected"] == "BR"
    assert len(out["calls"]) == 1
    scale, cx, cy, centered, animate = out["calls"][0]
    assert (cx, cy) == (-125, -220), "centered on the middle of the region"
    assert centered is True and animate is True
    assert 2 * 1.6 <= scale <= 2 * 12, "inside the zoom band, never the library's edge-to-edge fit"
    assert out["after"] is None and (out["scale"], out["y"]) == (2, 5), "second click resets the view"


def test_focus_box_ignores_a_far_flung_piece_of_the_path():
    """The United States path reaches both edges of the world map, so its
    bounding box was the whole map and focus went to the middle of it."""
    js = _extract("_heatFocusBox") + r"""
const node = {
  getBBox: () => ({x: 0, y: 0, width: 900, height: 300}),
  getAttribute: () => 'M100,100l200,0l0,100l-200,0zM20,60l60,0l0,30l-60,0zM890,50l5,0l0,5l-5,0z',
};
const one = {getBBox: () => ({x: 1, y: 2, width: 3, height: 4}), getAttribute: () => 'M1,2l3,0l0,4z'};
console.log(JSON.stringify({box: _heatFocusBox(node), one: _heatFocusBox(one)}));
"""
    out = _node(js)
    assert out["box"] == {"x": 20, "y": 60, "width": 280, "height": 140}, "main piece plus the near one, not the far one"
    assert out["one"] == {"x": 1, "y": 2, "width": 3, "height": 4}, "a single piece keeps its own box"


def test_the_wheel_only_reaches_the_map_after_a_click_or_with_a_modifier():
    body = _extract("_wireHeatMap")
    guard = re.search(r"addEventListener\('wheel', e => \{(.*?)\}, \{([^}]*)\}\)", body, re.S)
    assert guard, "no wheel guard on the map element"
    assert "if (!st.active && !e.ctrlKey && !e.metaKey) e.stopPropagation();" in guard.group(1)
    assert "capture: true" in guard.group(2), "it has to run before the library's listener on the canvas"
    assert "preventDefault" not in guard.group(1), "a stopped wheel must still scroll the page"
    assert "_setHeatActive(elId, true)" in body and "'mouseleave', () => _setHeatActive(elId, false)" in body


def test_list_rows_beside_both_maps_focus_the_map():
    src = _src()
    assert "onclick=\"focusHeatMapRegion('users-world-map','${code}')\"" in src
    assert "focusHeatMapRegion('tel-world-map','${x.country}',this.dataset.state)" in src
    assert "renderHeatMap('users-world-map', byCountry, byState, 'user', 'users-country-rank')" in src
    assert "renderHeatMap('tel-world-map', telByCountry, telByState, 'device', 'tel-location')" in src


def test_place_map_states_are_keyed_the_way_the_us_map_names_them():
    src = _src()
    assert "byState['US-' + code]" in src, "the US map's regions are US-TX, not TX"
    assert '"US-TX":{"path"' in open("app/static/us-states-map.js").read()


def test_no_fixed_inline_card_grids_or_sub_11px_text_are_left():
    src = _src()
    assert "grid-column:span 2" not in src, "inline spans bypass the shared grid (page scrolled sideways at 800 px)"
    assert not re.search(r"grid-template-columns:repeat\(\d+,1fr\)", src), "a fixed column count at every width"
    assert not re.search(r"font-size:\s*(?:[0-9]|10)(?:\.\d+)?px", src), "text under 11 px"
    assert not re.search(r"fontSize: '(?:[0-9]|10)px'", src), "chart labels under 11 px"
    assert "wireGridBalance();" in src


def test_grid_classes_the_balancer_reads_each_set_their_own_minimum():
    """--dash-min inherits, so a strip of tiles inside a card would take the
    outer grid's 340 or 560 px unless its own class sets one."""
    src = _src()
    css = src[src.index("<style>"):src.index("</style>")]
    sel = re.search(r"const _GRID_SEL = '([^']+)';", src).group(1)
    for cls in [c.strip() for c in sel.split(",")]:
        rule = re.search(rf"^\s*{re.escape(cls)} \{{([^}}]*)\}}", css, re.M)
        assert rule and "--dash-min:" in rule.group(1), f"{cls} has no --dash-min of its own"
