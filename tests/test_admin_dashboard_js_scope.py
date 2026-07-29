"""Static guards for admin.html render scope.

2026-07-29: the Usage-by-Call-Type panel called `esc()`, which at the time
was a `const` declared INSIDE renderUserRows(). refresh() therefore threw
`ReferenceError: esc is not defined` partway through painting the
dashboard, and because every panel runs in sequence from one payload,
Users, Latency and everything below simply never rendered. The page looked
like it had no data rather than like it was broken.

Two guards: shared helpers must be hoisted top-level declarations, and the
panels must render in isolation so one failure can't cascade again.
"""

import re

HTML = "app/static/admin.html"


def _src():
    return open(HTML).read()


def test_shared_helpers_are_top_level_declarations():
    src = _src()
    for name in ("esc", "fmt", "fmtCost", "fmtMs", "timeUntil", "panel"):
        assert re.search(rf"^function {name}\(", src, re.M), (
            f"{name}() must be a top-level function declaration so every "
            f"renderer can reach it (a const inside another function is what "
            f"broke the dashboard on 2026-07-29)"
        )


def test_esc_is_not_shadowed_inside_a_renderer():
    # A local re-declaration would work for its own function while leaving
    # the global one live for others, which is exactly the confusing state
    # that hid the original bug.
    assert not re.search(r"^\s+const esc\s*=", _src(), re.M)


def test_dashboard_panels_render_in_isolation():
    src = _src()
    for name in ("models", "call types", "scenarios", "users", "latency"):
        assert f"panel('{name}'" in src, f"panel {name!r} is not isolated"


def test_admin_page_declares_its_own_favicon():
    # Without this the browser requests /favicon.ico on every admin load and
    # takes a 404, which is noise in exactly the console an operator opens
    # when something is wrong. Inline data URI, so there is no request at all
    # and no static file to serve.
    src = _src()
    m = re.search(r'<link rel="icon" href="data:image/svg\+xml,(.*?)">', src)
    assert m, "admin page must declare an inline favicon"

    import urllib.parse
    import xml.etree.ElementTree as ET

    ET.fromstring(urllib.parse.unquote(m.group(1)))  # must be well-formed SVG


def test_panel_helper_reports_rather_than_swallows():
    src = _src()
    body = src[src.index("function panel(name, fn)"):][:900]
    assert "console.error" in body, "a swallowed panel error is worse than a loud one"
    assert "_panelErrors.push" in body
    assert "panel-error-banner" in body
    # The banner element has to exist for the handler to find it.
    assert 'id="panel-error-banner"' in src
