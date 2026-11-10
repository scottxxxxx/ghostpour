"""Performance at a glance (Scott, 2026-11-08: "do what you did to improve the
overview page"). Hero cards with a trend line and a change chip, takeaways
that carry their denominators, and ranked latency bars per model and query
type. These run the real functions in node.
"""

import re

from tests.test_admin_load_path import _extract, _node

HTML = "app/static/admin.html"

_HARNESS = r"""
const node = {innerHTML: ''};
const sel = {value: ''};
const document = {getElementById: id => id === 'box' ? node : (id === 'lat-call-type' || id === 'lat-model' ? sel : null)};
const fmt = n => n == null ? '-' : Number(n).toLocaleString('en-US');
const fmtMs = v => v == null ? '-' : (v >= 1000 ? (v / 1000).toFixed(1) + 's' : v + 'ms');
"""


def _run(tail, *names):
    # The real constant, read from the page, so the test cannot drift from it.
    src = open(HTML).read()
    js = _HARNESS + re.search(r"const PERF_TAKE_MIN = \d+;", src).group(0) + "\n" + "".join(_extract(n) + "\n" for n in ("esc",) + names) + tail
    return _node(js)


_D = """{days: 7,
  current: {n: 595, p50: 2900, p95: 28500, error_rate: 0, cost_per_call: 0.0115},
  previous: {n: 472, p50: 4300, p95: 28100, cost_per_call: 0.0165},
  buckets: [{n: 9, p50: 4072}, {n: 0, p50: null}, {n: 13, p50: 913}],
  breakdown: {by_call_type: [
    {call_type: 'i765_interviewer_turn', n: 196, p50: 2700, p95: 4700},
    {call_type: 'report', n: 20, p50: 29300, p95: 57200},
    {call_type: 'tr_parse_jd', n: 2, p50: 54696, p95: 64479},
    {call_type: 'quiet', n: 0, p50: null, p95: null}]}}"""


def test_takeaways_name_the_trend_the_slowest_and_busiest_lane_and_errors():
    out = _run(f"console.log(JSON.stringify(perfTakeaways({_D})));", "perfTakeaways")
    assert [t["title"] for t in out] == ["Responses got faster", "report is the slowest lane",
                                         "i765_interviewer_turn is the busiest lane", "No failed calls"]
    assert out[0]["body"] == "Median 2.9s against 4.3s in the prior 7 days."
    # tr_parse_jd has the worst p95 but only 2 calls, under PERF_TAKE_MIN.
    assert out[1]["body"] == "p95 57.2s, median 29.3s, over 20 calls."
    assert out[2]["body"] == "90% of calls (196 of 218), median 2.7s."
    assert out[3]["body"] == "0 errors in 595 successful calls."


def test_takeaways_skip_the_trend_without_prior_calls_and_report_a_failure_rate():
    d = _D.replace("previous: {n: 472", "previous: {n: 0").replace("error_rate: 0,", "error_rate: 0.0125,")
    out = _run(f"console.log(JSON.stringify(perfTakeaways({d})));", "perfTakeaways")
    assert out[0]["title"] == "report is the slowest lane"
    assert out[-1]["title"] == "1.3% of calls failed"


def test_spark_skips_an_empty_bucket_instead_of_drawing_zero():
    out = _run("""
console.log(JSON.stringify({gap: _perfSpark([4000, null, 1000], '#4c9aff'), one: _perfSpark([null, 5, null], '#4c9aff')}));""",
               "_perfSpark")
    line = re.findall(r'<path d="(M[^"]*)" fill="none"', out["gap"])[0]
    # Two points, at the window's first and last x: 4000 is the max (y 6),
    # 1000 a quarter of it (y 28.5). One segment, nothing at the middle x.
    assert line.startswith("M0.00 6.00 C") and line.endswith(" 100.00 28.50")
    assert line.count(" C") == 1
    assert out["one"] == "", "one point is not a trend"


def test_hero_chips_flip_for_latency_and_cost_and_go_quiet_without_a_prior():
    tail = """
const _PERF_ICONS = {}; const _ANA_ICONS = {};
console.log(JSON.stringify({html: _perfHeroCards(D)}));"""
    funcs = ("_anaDelta", "_perfSpark", "_perfHeroCards")
    html = _run(f"const D = {_D};" + tail, *funcs)["html"]
    chips = re.findall(r'<span class="ana-chip (\w+)">(.*?)</span>', html)
    assert chips == [("good", "↑ +26%"), ("good", "↓ -33%"), ("bad", "↑ +1%"), ("good", "↓ -30%")]
    quiet = _run("const D = " + _D.replace("previous: {n: 472", "previous: {n: 0") + ";" + tail, *funcs)["html"]
    assert "new in this window" in quiet.split("Median response")[0], "calls against a prior of 0"
    assert quiet.count("no prior 7 days") == 3, "latency and cost have nothing to compare against"


def test_latency_bars_rank_by_calls_share_one_scale_and_filter_on_click():
    out = _run(f"""
_perfLatencyBars('box', ({_D}).breakdown.by_call_type, 'call_type', {{max: 2}});
console.log(JSON.stringify({{html: node.innerHTML}}));""", "_perfLatencyBars")
    html = out["html"]
    assert re.findall(r'data-name="(.*?)"', html) == ["i765_interviewer_turn", "report"]
    assert "quiet" not in html, "a lane with no calls has no latency"
    # report's p95 is the longest shown, so it fills the track and the
    # interviewer's p50 sits at 2700 / 57200 of it.
    assert 'class="perf-lat-p95" style="width:100.0%"' in html
    assert 'class="perf-lat-p50" style="width:4.7%"' in html
    assert "onclick=\"perfPick('call_type', this.dataset.name)\"" in html
    assert "2 of 3 shown" in html


def test_glance_sits_above_the_detail_and_the_strip_drops_the_hero_numbers():
    src = open(HTML).read()
    tab = src[src.index('<div id="tab-latency"'):src.index('<!-- Providers -->')]
    for div in ("perf-hero", "perf-takeaways", "perf-lat-cts", "perf-lat-models"):
        assert f'id="{div}"' in tab
    assert tab.index('id="perf-hero"') < tab.index('id="perf-main-chart"') < tab.index('<details class="perf-more" id="perf-more">')
    assert tab.index('<details class="perf-more" id="perf-more">') < tab.index('id="perf-models-table"')
    strip = _extract("renderPerfKpis")
    assert "'Successful calls'" not in strip and "'p50 latency'" not in strip and "'Cost per call'" not in strip
    assert "renderPerfGlance(d);" in _extract("renderPerformance")


def test_an_unselected_window_falls_back_to_the_defaults_instead_of_a_422():
    # Live log 2026-11-10: GET latency-trends?days=&bucket= from the dashboard,
    # 422 int_parsing, twice. The request must never carry an empty window.
    js = r"""
const els = {};
const mk = (id, value) => (els[id] = {id, value, disabled: false, innerHTML: '',
  classList: {add() {}, remove() {}}, querySelector: () => ({disabled: false})});
mk('lat-days', ''); mk('lat-bucket', ''); mk('lat-model', ''); mk('lat-call-type', '');
mk('app', ''); mk('tab-latency', ''); mk('perf-kpis', '');
const document = {getElementById: id => els[id] || null};
const BASE = ''; const adminKey = 'k';
let url = null;
const fetch = async u => { url = u; return {ok: false, status: 0}; };
""" + _extract("loadLatency") + r"""
loadLatency().then(() => console.log(JSON.stringify({url, days: els['lat-days'].value, bucket: els['lat-bucket'].value})));
"""
    out = _node(js)
    assert out["url"] == "/webhooks/admin/latency-trends?days=7&bucket=day"
    assert (out["days"], out["bucket"]) == ("7", "day"), "the selects show what was sent"
