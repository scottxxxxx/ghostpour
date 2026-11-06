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
