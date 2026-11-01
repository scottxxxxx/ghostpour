"""Static guards for admin.html's stylesheet and tab strip.

2026-10-31, live page at 1120 px wide: the eighteen tabs ran to roughly
1,800 px in a flex row that neither wrapped nor scrolled, so "Model Routing"
and "Live Log" broke onto two lines inside their pills and "Telemetry" was
clipped off the right edge. The app filter dropdown had no background and
showed the page through it, because its inline style read var(--bg-card) and
nothing in :root defined --bg-card (an undefined custom property makes the
whole declaration invalid, silently). --bg-alt and --card-bg were in the same
state, class="btn" had no rule behind it, .badge was defined twice at equal
specificity, and the tabs were <div onclick> with no role, no tabindex and
no keyboard path at all.

Every check here reads the page text; none of it needs a browser. The
regexes are deliberately loose about whitespace so a reformat of one rule
does not fail the file, and tight about names so a rename does.
"""

import re

HTML = "app/static/admin.html"


def _src():
    return open(HTML).read()


def _style_block(src):
    m = re.search(r"<style>(.*?)</style>", src, re.S)
    assert m, "the page must carry one inline <style> block"
    return m.group(1)


def _root_names(css):
    m = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert m, ":root block not found in the stylesheet"
    return set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", m.group(1)))


def _rule_bodies(css):
    """(selector, body) pairs at the top level of the stylesheet, with
    @media blocks unwrapped one level so their inner rules are seen too."""
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    flat = re.sub(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", r"\1", flat, flags=re.S)
    return re.findall(r"([^{}]+?)\s*\{([^{}]*)\}", flat)


def test_every_custom_property_the_page_reads_is_defined_in_root():
    src = _src()
    defined = _root_names(_style_block(src))
    # var(--${statusCls}) at the query-log status cell is composed at render
    # time from a JS variable; it cannot be checked by name here and is left
    # to the renderer. Everything with a literal name is checked, including
    # names used with a fallback: a fallback hides the gap, it does not fill it.
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", src))
    missing = sorted(used - defined)
    assert not missing, (
        f"var() reads custom properties that :root never defines: {missing}. "
        f"An undefined property makes the declaration invalid and paints "
        f"nothing (the app filter dropdown was transparent on 2026-10-31)."
    )


def test_tab_strip_wraps_or_scrolls():
    css = _style_block(_src())
    bodies = [b for sel, b in _rule_bodies(css) if sel.strip() == ".tabs"]
    assert bodies, "no top-level .tabs rule"
    body = " ".join(bodies)
    assert (re.search(r"flex-wrap\s*:\s*wrap", body)
            or re.search(r"overflow-x\s*:\s*auto", body)), (
        ".tabs is a flex row that neither wraps nor scrolls; eighteen tabs "
        "need about 1,800 px and were clipped at 1120 px on 2026-10-31"
    )


def _tab_strip(src):
    m = re.search(r'<div class="tabs"[^>]*>(.*?)\n  </div>', src, re.S)
    assert m, "the tab strip was not found"
    return m.group(0), m.group(1)


def test_tab_strip_is_a_tablist_and_every_tab_is_focusable():
    src = _src()
    whole, inner = _tab_strip(src)
    assert 'role="tablist"' in whole.split("\n", 1)[0], "the strip needs role=tablist"
    tabs = re.findall(r"<div class=\"tab(?: active)?\"[^>]*>", inner)
    assert len(tabs) >= 18, f"expected the full strip, found {len(tabs)} tabs"
    bad = [t for t in tabs if 'role="tab"' not in t or not re.search(r'tabindex="\d+"', t)]
    assert not bad, (
        f"{len(bad)} tab(s) lack role=\"tab\" or tabindex and cannot be reached "
        f"by keyboard: {bad[:3]}"
    )
    no_sel = [t for t in tabs if 'aria-selected=' not in t]
    assert not no_sel, f"tabs without aria-selected: {no_sel[:3]}"
    # The keyboard handler is the one piece of JS this file owns; the strip
    # is useless to a keyboard without it.
    assert re.search(r"^function wireTabKeyboard\(", src, re.M)
    assert "wireTabKeyboard();" in src, "the handler must actually be wired"
    handler = src[src.index("function wireTabKeyboard("):]
    for key in ("'Enter'", "' '", "'ArrowLeft'", "'ArrowRight'"):
        assert key in handler, f"the tab keydown handler does not handle {key}"


def test_tab_labels_still_match_switch_tab_by_prefix():
    # switchTab() picks the active tab by the first four letters of its
    # label text unless data-tab names it. Adding attributes to the tabs must
    # not have changed a label; this pins the text that the matcher reads.
    _, inner = _tab_strip(_src())
    labels = re.findall(r">([^<]+)</div>", inner)
    for expected in ("Overview", "Model Routing", "Live Log", "Telemetry", "Performance"):
        assert expected in labels, f"tab label {expected!r} is missing or changed"


def test_component_classes_used_in_markup_have_a_rule():
    src = _src()
    css = _style_block(src)
    selectors = " ".join(sel for sel, _ in _rule_bodies(css))
    used = set()
    for attr in re.findall(r'class="([^"]*)"', src):
        used.update(attr.split())
    components = {"btn", "badge", "card", "table-wrap", "tab", "tabs", "section"}
    unstyled = sorted(c for c in components & used
                      if not re.search(rf"\.{re.escape(c)}(?![A-Za-z0-9_-])", selectors))
    assert not unstyled, (
        f"component classes used in markup with no CSS rule: {unstyled} "
        f"(class=\"btn\" rendered as a browser default button on 2026-10-31)"
    )


def test_badge_is_defined_once_at_top_level():
    css = _style_block(_src())
    badge_rules = [sel for sel, _ in _rule_bodies(css) if sel.strip() == ".badge"]
    assert len(badge_rules) == 1, (
        f".badge is declared {len(badge_rules)} times at equal specificity; the "
        f"later one silently wins each conflicting property, so a change to the "
        f"first does nothing and nobody can tell which values render"
    )


def test_table_wrap_lets_wide_tables_scroll():
    css = _style_block(_src())
    bodies = [b for sel, b in _rule_bodies(css) if sel.strip() == ".table-wrap"]
    assert bodies, "no top-level .table-wrap rule"
    body = " ".join(bodies)
    assert re.search(r"overflow-x\s*:\s*auto", body), (
        ".table-wrap must scroll horizontally; the 22-column user table was "
        "clipped at the card edge on 2026-10-31"
    )
    assert not re.search(r"overflow\s*:\s*hidden", body)


def test_no_dashes_in_this_regions_copy_or_comments():
    """Same standing rule as the served prompts and the rest of the page."""
    src = _src()
    css = _style_block(src)
    strip, _ = _tab_strip(src)
    at = src.find("function wireTabKeyboard(")
    handler = src[at:] if at >= 0 else ""
    # Built from code points so this file itself carries neither character.
    for region in (css, strip, handler):
        for d in (chr(0x2014), chr(0x2013)):
            assert d not in region
