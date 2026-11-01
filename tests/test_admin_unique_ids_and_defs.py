"""Static guards against duplicate ids and duplicate function declarations in
admin.html.

Receipt: audit of 2026-10-31. Two silent bugs, same shape, neither logged
nor threw.

1. loadRouting(), renderRouting() and saveRouting() were each declared
   twice: once for the Models tab's "Model Routing Dials" editor (rendering
   into #routing-editor) and once for the Model Routing tab (rendering into
   #routing-grid). Function declarations hoist and the later one wins, so
   opening the Models tab ran the Routing tab's loader, painted the hidden
   grid, and left #routing-editor empty forever. The dead editor is gone;
   this file keeps a second declaration from ever landing again.

2. id="call-type-table" existed in the Models tab AND in the Users tab's
   detail panel. getElementById returns the first match, so opening a user
   overwrote the Models tab's call-type table with that user's rows while
   the detail panel's own "Usage by Query Type" table stayed empty. The
   Users one is now user-call-type-table.

tests/test_admin_dashboard_js_scope.py already pins three specific ids and
the live saveRouting's target span; these guards are the general form.
"""

import re
from collections import Counter

HTML = "app/static/admin.html"


def _src():
    return open(HTML).read()


def _script_body(src):
    # Only the page's own inline JS. The HTML before it carries onclick
    # attributes and the words "function" in prose, neither of which is a
    # declaration.
    start = src.rindex("<script>")
    end = src.rindex("</script>")
    return src[start:end]


def _dupes(names):
    return sorted(n for n, c in Counter(names).items() if c > 1)


def test_every_element_id_is_unique():
    ids = re.findall(r'\bid="([^"]+)"', _src())
    dupes = _dupes(ids)
    assert not dupes, (
        f"duplicate element ids in admin.html: {dupes}. getElementById "
        f"returns only the first match, so the second element is unreachable "
        f"and whatever writes to it lands on the first (call-type-table, "
        f"2026-10-31)")


def test_no_top_level_function_is_declared_twice():
    names = re.findall(
        r"^\s*(?:async\s+)?function\s+(\w+)\s*\(", _script_body(_src()), re.M)
    dupes = _dupes(names)
    assert not dupes, (
        f"functions declared more than once in admin.html: {dupes}. "
        f"Declarations hoist and the later one silently wins, so the earlier "
        f"caller runs the wrong code (loadRouting, renderRouting, saveRouting, "
        f"2026-10-31)")


def test_show_user_detail_targets_its_own_call_type_table():
    src = _src()
    # From the public wrapper through _showUserDetail, which paints the panel
    # (split on 2026-11-01 so the fetch runs under the detail's state line).
    body = src[src.index("function showUserDetail("):]
    body = body[:body.index("\nasync function loadSearchActivity(")]
    assert "querySelector('#user-call-type-table tbody')" in body, (
        "showUserDetail must write into the detail panel's own table")
    assert "'#call-type-table tbody'" not in body, (
        "that selector is the Models tab's table")
    # And the element it targets has to exist, inside the detail panel.
    detail = src[src.index('id="user-detail"'):src.index('id="prompt-mode-table"')]
    assert 'id="user-call-type-table"' in detail


def test_the_dead_models_tab_routing_editor_is_gone():
    # Deleted rather than renamed: the Model Routing tab edits the same
    # per-app, per-call_type, per-tier dials. If someone wants the editor
    # back it must not reuse the live tab's function names.
    src = _src()
    assert 'id="routing-editor"' not in src
    assert 'id="routing-status"' not in src, "the dead editor's status span"
    assert "routingDoc" not in src


def test_the_routing_tab_reloads_on_every_open():
    # The wrapper used to guard on `!_routingData`, which loaded once per
    # page life. Worse, switchTab's `models` branch calls loadRouting() and
    # set _routingData before the Routing tab was ever opened, so the guard
    # was already false on first open and the tab showed whatever the Models
    # tab had fetched, forever.
    src = _src()
    tail = src[src.index("const _origSwitchTab = switchTab;"):]
    assert "if (tab === 'routing') loadRouting();" in tail
    assert "!_routingData" not in tail


def test_new_copy_carries_no_dashes():
    """Same standing rule as the served prompts and the js_scope guard."""
    src = _src()
    regions = (
        ("// Call type table.", "data.by_call_type"),
        ("// --- Model Routing ---", "let _routingData = null;"),
        ("// Load routing when the tab is shown.", "</script>"),
    )
    for start, end in regions:
        region = src[src.index(start):]
        region = region[:region.index(end)]
        for d in (chr(0x2014), chr(0x2013)):  # em dash, en dash
            assert d not in region, f"dash in copy near {start!r}"
