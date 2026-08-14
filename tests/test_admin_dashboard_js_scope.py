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


def test_maps_use_an_ordinal_heat_scale_not_a_gradient_array():
    # jsvectormap's series `scale` is an ordinal lookup (`scale[value]`), and
    # the library has no `normalizeFunction`. Passing a two-colour array meant
    # scale[1] for one-device countries (bright) and undefined for anything
    # busier, which renders black: on 2026-07-29 the US at 35 devices painted
    # black while Andorra at 1 painted brightest.
    src = _src()
    assert "normalizeFunction:" not in src, (
        "normalizeFunction is a jVectorMap option; jsvectormap ignores it"
    )
    # (the literal appears once more inside the explanatory comment above
    # heatScale, which is why this looks at real config lines only)
    config_lines = [ln for ln in src.split("\n") if not ln.lstrip().startswith("//")]
    assert not re.search(r"scale:\s*\['#", "\n".join(config_lines)), (
        "a colour array is an ordinal lookup here, not a gradient"
    )
    assert "scale: heatScale(" in src, "the computed scale must be used"


def test_maps_are_zoomable_and_pannable():
    src = _src()
    assert "zoomButtons: false" not in src
    assert "zoomButtons: true" in src
    assert "draggable: true" in src


def test_both_cards_share_one_map_renderer():
    # The two location cards drew with near-identical duplicated blocks; a fix
    # to one (the ordinal-scale bug, the deferred re-measure) had to be made
    # twice or it silently applied to only one map.
    src = _src()
    assert len(re.findall(r"new jsVectorMap\(", src)) == 1, (
        "exactly one map construction site"
    )
    for el in ("tel-world-map", "users-world-map"):
        assert f"renderHeatMap('{el}'" in src


def test_us_state_view_is_wired():
    src = _src()
    # the vendored map has to be loaded, and only from our own origin
    assert '<script src="/admin/us-states-map.js"></script>' in src
    # a view toggle per card, and the state lookup keyed off GeoIP region names
    for el in ("tel-world-map", "users-world-map"):
        assert f'id="{el}-toggle"' in src
    assert "function usStateCode(region)" in src
    assert "US_STATE_CODE_BY_NAME" in src
    # the US view must degrade to the world view if the map file didn't load
    assert "if (st.view === 'us' && !usReady) st.view = 'world';" in src


def test_users_map_is_remeasured_when_its_tab_becomes_visible():
    # jsvectormap measures its container once, at construction. The Users map
    # is built by refresh() while #tab-users is still hidden, so it sized to
    # 0x0 and painted an empty SVG: no error, no fallback text, just a blank
    # panel next to the country list. The Telemetry map never showed this
    # because its own tab loader runs after that tab is visible.
    src = _src()
    assert re.search(r"name === 'users'", src), (
        "switching to the Users tab must re-measure the map"
    )
    assert "_heatMaps['users-world-map']" in src
    assert "updateSize()" in src
    assert "addEventListener('resize'" in src, "a window resize has the same problem"


def test_panel_helper_reports_rather_than_swallows():
    src = _src()
    body = src[src.index("function panel(name, fn)"):][:900]
    assert "console.error" in body, "a swallowed panel error is worse than a loud one"
    assert "_panelErrors.push" in body
    assert "panel-error-banner" in body
    # The banner element has to exist for the handler to find it.
    assert 'id="panel-error-banner"' in src


# --- Duplicate ids (2026-08-14) --------------------------------------------
#
# admin.html carries TWO model-routing UIs: a "Model Routing Dials" panel in
# the Config tab and a "Model Routing" tab. Both defined loadRouting(),
# renderRouting() and saveRouting() at top level, so the later pair silently
# shadows the earlier one, and both used id="routing-status".
# getElementById returns the FIRST match, so the live tab wrote every save
# confirmation into a hidden span belonging to the other tab. The save
# worked and looked like it had not.


def test_status_ids_are_unique():
    """A duplicate id does not fail loudly, it just resolves to the wrong
    element forever."""
    src = _src()
    for el_id in ("routing-status", "routing-tab-status", "routing-save-btn"):
        assert src.count(f'id="{el_id}"') <= 1, (
            f'id="{el_id}" appears more than once; getElementById would '
            f"return only the first and the other becomes unreachable")


def test_the_live_routing_save_targets_its_own_tab():
    src = _src()
    save = src[src.rindex("async function saveRouting()"):]
    assert "getElementById('routing-tab-status')" in save, (
        "the Model Routing tab's save must write to the span in its own tab")
    assert "getElementById('routing-status')" not in save


def test_the_save_confirmation_does_not_erase_itself():
    """A confirmation that vanishes after three seconds is the bug, not the
    fix: the whole point is that it is still there when you look up."""
    src = _src()
    save = src[src.rindex("async function saveRouting()"):]
    assert "setTimeout" not in save, "the confirmation must persist"
    assert "Saving..." in save, "in-flight state"
    assert "Save failed (" in save, "failures name the status code"


def test_the_version_is_only_bumped_after_the_server_accepts():
    """Bumping first left the local copy ahead of the server whenever a save
    failed, so the next successful save skipped a version number."""
    src = _src()
    save = src[src.rindex("async function saveRouting()"):]
    bump = save.index("_routingData.version = nextVersion")
    assert save.index("resp.ok") < bump, "assign the version only after resp.ok"


def test_dashboard_copy_carries_no_dashes():
    """Same standing rule as the served prompts; this is copy Scott reads."""
    src = _src()
    save = src[src.rindex("async function saveRouting()"):]
    for d in ("—", "–"):
        assert d not in save
