"""The ranked share bars that replaced the admin donuts (2026-11-05).

Scott: the donuts were "not an efficient way to show something like ios
version or hardware". Each one is now _renderShareBars: rows sorted by value,
one hue, every count and share printed, and past eight rows the tail folded
into "Other" instead of more colors. These run the real function in node.
"""

import json
import re

from tests.test_admin_load_path import _extract, _node

HTML = "app/static/admin.html"

_HARNESS = r"""
const _telCharts = {};
const _SHARE_BARS_MAX = 8;
const node = {innerHTML: ''};
const document = {getElementById: id => id === 'box' ? node : null};
"""


def _render(rows, opts="{}"):
    js = (_HARNESS + _extract("esc") + "\n" + _extract("_renderShareBars") + "\n"
          + f"_renderShareBars('box', {json.dumps(rows)}, {opts});\n"
          + "console.log(JSON.stringify({html: node.innerHTML}));")
    html = _node(js)["html"]
    labels = re.findall(r'<span class="share-label" role="cell">(.*?)</span>', html)
    values = re.findall(r'<span class="share-value" role="cell">(.*?)</span>', html)
    pcts = re.findall(r'<span class="share-pct" role="cell">(.*?)</span>', html)
    return html, labels, values, pcts


def test_rows_sort_by_value_and_print_count_and_share():
    html, labels, values, pcts = _render(
        [{"label": "iOS 18.6", "value": 1}, {"label": "iOS 26.1", "value": 3}, {"label": "iOS 26.0", "value": 0}],
        "{unit: 'devices'}")
    assert labels == ["iOS 26.1", "iOS 18.6"], "zero rows drop and the biggest leads"
    assert values == ["3", "1"] and pcts == ["75%", "25%"]
    assert '<div class="share-total">4 devices</div>' in html
    assert 'style="width:100.0%"' in html and 'style="width:33.3%"' in html, "bars scale to the largest row"


def test_past_eight_rows_the_tail_folds_into_other():
    rows = [{"label": f"m{i}", "value": 20 - i} for i in range(10)]
    _, labels, values, _ = _render(rows)
    assert labels == ["m0", "m1", "m2", "m3", "m4", "m5", "m6", "Other (3)"]
    assert values[-1] == str(13 + 12 + 11)


def test_labels_are_escaped_and_the_formatter_reaches_values_and_total():
    html, labels, values, _ = _render([{"label": "<b>x</b>", "value": 1.5}],
                                      "{fmt: v => '$' + v.toFixed(2), unit: 'total'}")
    assert labels == ["&lt;b&gt;x&lt;/b&gt;"] and "<b>" not in html
    assert values == ["$1.50"] and '<div class="share-total">$1.50 total</div>' in html


def test_nothing_to_show_prints_the_empty_line():
    html, labels, _, _ = _render([{"label": "a", "value": 0}], "{empty: 'No meeting events in this window.'}")
    assert labels == [] and html == '<div class="share-empty">No meeting events in this window.</div>'


def test_no_donut_is_left_and_apexcharts_is_pinned_exact():
    src = open(HTML).read()
    assert "type: 'donut'" not in src
    assert 'src="https://cdn.jsdelivr.net/npm/apexcharts@3.54.1"' in src


def _run(js_tail, *names):
    js = _HARNESS + "".join(_extract(n) + "\n" for n in ("esc",) + names) + js_tail
    return _node(js)


def test_a_given_total_is_the_denominator_and_children_open_under_their_row():
    rows = [{"label": "iOS 26", "value": 2, "children": [{"label": "26.1", "value": 1}, {"label": "26.0.1", "value": 1}]},
            {"label": "iOS 18", "value": 1, "children": [{"label": "18.6", "value": 1}]}]
    html, labels, _, pcts = _render(rows, "{unit: 'active devices', total: 2, note: 'counts once per version'}")
    assert '<div class="share-total">2 active devices</div>' in html
    assert '<div class="share-note">counts once per version</div>' in html
    assert pcts[:3] == ["100%", "50%", "50%"], "shares are of the given total, not of the row sum"
    assert labels == ["iOS 26", "26.1", "26.0.1", "iOS 18"], "a lone child adds no drill-down"
    assert html.count('<details class="share-group">') == 1
    assert 'class="share-row lead"' in html and html.count(" lead") == 1


def test_the_hint_reaches_the_hover_title_only():
    html, labels, _, _ = _render([{"label": "SS AI", "hint": "cloudzap/auto", "value": 3}])
    assert labels == ["SS AI"] and 'title="SS AI (cloudzap/auto): 3, 100.0%"' in html


def test_stack_bar_keeps_caller_order_and_colors_and_prints_every_share():
    out = _run("""
_renderStackBar('box', [{label: 'Free', value: 3, color: '#184f95'}, {label: 'Plus', value: 1, color: '#256abf'}, {label: 'Admin', value: 0, color: '#475569'}],
                {unit: 'users', headline: 'paid 1'});
console.log(JSON.stringify({html: node.innerHTML}));""", "_renderStackBar")
    html = out["html"]
    assert '<div class="share-total">4 users · paid 1</div>' in html
    assert re.findall(r'flex:(\d+) 1 0;background:(#\w+)', html) == [("3", "#184f95"), ("1", "#256abf")]
    assert re.findall(r'<span class="share-pct" role="cell">(.*?)</span>', html) == ["75%", "25%"]


def test_insights_name_the_leader_with_its_denominator_and_skip_unknowns():
    out = _run("""
const d = {days: 30, kpis: {distinct_devices: 10},
  models: [{model_id: 'unknown', meetings: 9}, {model_id: 'cloudzap/auto', display_name: 'SS AI', meetings: 6}, {model_id: 'x', display_name: 'X', meetings: 5}],
  os_majors: [{major: 'unknown', devices: 9}, {major: '26', devices: 7}],
  devices: [{device_model: 'unknown', marketing_name: 'Field missing', meetings: 9}, {device_model: 'iPhone17,1', marketing_name: 'iPhone 16 Pro', meetings: 11}]};
console.log(JSON.stringify(_telInsights(d)));""", "_telInsights")
    assert out[0] == {"title": "SS AI runs 30% of meetings", "body": "6 of 20 meetings started in the last 30 days."}
    assert out[1] == {"title": "iOS 26 is on 70% of devices", "body": "7 of 10 active devices ran it in the window."}
    assert out[2] == {"title": "iPhone 16 Pro is the top device", "body": "55% of meetings (11 of 20)."}


def test_data_quality_says_what_is_unattributed():
    out = _run("""
console.log(JSON.stringify([
  _telDataQuality({models: [{model_id: 'unknown', meetings: 2}], devices: [{device_model: 'unknown', meetings: 3}], os_majors: [], distribution: [], by_language: []}),
  _telDataQuality({models: [], devices: [], os_majors: [], distribution: [], by_language: [{language: 'en', devices: 1}]}),
]));""", "_telDataQuality")
    assert out[0] == ["2 meetings arrived without a model id.",
                      "3 meetings came from builds before 1.13, which don't send the device model.",
                      "No device has sent its language yet (iOS sends app_locale from 1.13)."]
    assert out[1] == []
