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


_SPARK = ("_sparkWhen", "_monoPath", "_trendSpark")


def test_spark_skips_an_empty_bucket_instead_of_drawing_zero():
    out = _run("""
const P = vs => vs.map((v, i) => ({key: '2026-11-0' + (i + 1), v}));
console.log(JSON.stringify({gap: _trendSpark(P([4000, null, 1000]), '#4c9aff'), one: _trendSpark(P([null, 5, null]), '#4c9aff')}));""",
               *_SPARK)
    line = re.findall(r'<path d="(M[^"]*)" fill="none"', out["gap"])[0]
    # Two points, at the window's first and last x: 4000 is the max (y 6),
    # 1000 a quarter of it (y 28.5). One segment, nothing at the middle x.
    assert line.startswith("M0.00 6.00 C") and line.endswith(" 100.00 28.50")
    assert line.count(" C") == 1
    assert "Nov 2: no data" in out["gap"], "the gap hour says so on hover, not 0"
    assert out["one"] == "", "one point is not a trend"


def _curve_ys(path, samples=50):
    """Every y the drawn curve reaches, sampled along each cubic segment."""
    nums = [float(x) for x in re.findall(r"-?\d+\.\d+", path)]
    x0, y0, rest = nums[0], nums[1], nums[2:]
    segs = []
    for k in range(0, len(rest), 6):
        c1y, c2y, ey = rest[k + 1], rest[k + 3], rest[k + 5]
        ys = [(1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * c1y + 3 * (1 - t) * t * t * c2y + t ** 3 * ey
              for t in (i / samples for i in range(samples + 1))]
        segs.append((y0, ey, ys))
        y0 = ey
    return segs


def test_curve_never_swings_past_a_day():
    # A spike between flat days is where Catmull-Rom overshot: the old line
    # dipped below the quiet days on either side of the peak.
    out = _run("""
const P = [3, 3, 20, 3, 3, 9, 0, 0].map((v, i) => ({key: '2026-11-0' + (i + 1), v}));
console.log(JSON.stringify({s: _trendSpark(P, '#4c9aff', v => v + ' meetings', 'Meetings')}));""", *_SPARK)["s"]
    line = re.findall(r'<path d="(M[^"]*)" fill="none"', out)[0]
    for a, b, ys in _curve_ys(line):
        lo, hi = min(a, b), max(a, b)
        assert all(lo - 1e-6 <= y <= hi + 1e-6 for y in ys), (a, b, min(ys), max(ys))


def test_strip_labels_the_window_the_peak_and_every_day():
    out = _run("""
const P = [3, 0, 20].map((v, i) => ({key: '2026-11-0' + (i + 3), v}));
console.log(JSON.stringify({s: _trendSpark(P, '#4c9aff', v => v + ' meetings', 'Meetings')}));""", *_SPARK)["s"]
    assert '<div class="spark-axis"><span>Nov 3</span><span>Nov 5</span></div>' in out
    assert '<div class="spark-peak">peak 20 meetings</div>' in out
    tips = re.findall(r'<span class="spark-tip">(.*?)</span>', out)
    assert tips == ["Nov 3: 3 meetings", "Nov 4: 0 meetings", "Nov 5: 20 meetings"]
    assert 'aria-label="Meetings, Nov 3 to Nov 5, peak 20 meetings"' in out


def test_overview_fills_the_days_gp_left_out():
    out = _run("""
console.log(JSON.stringify(_anaFill([{day: '2026-11-04', meetings: 5}, {day: '2026-11-07', meetings: 2}], 'meetings', 3, '2026-11-07')));""",
               "_anaDays", "_anaFill")
    assert out == [{"key": "2026-11-04", "v": 5}, {"key": "2026-11-05", "v": 0},
                   {"key": "2026-11-06", "v": 0}, {"key": "2026-11-07", "v": 2}]


def test_hero_chips_flip_for_latency_and_cost_and_go_quiet_without_a_prior():
    tail = """
const _PERF_ICONS = {}; const _ANA_ICONS = {};
console.log(JSON.stringify({html: _perfHeroCards(D)}));"""
    funcs = ("_anaDelta", "_anaN") + _SPARK + ("_perfHeroCards",)
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
