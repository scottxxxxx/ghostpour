"""A tier cap save must leave its "Saved" line on screen and touch one card.

Audit of 2026-10-31: saveProjectChatCap, saveSearchCap, saveGenerationCap and
saveImageConfig wrote "Saved" into their status element and then called
refresh(). refresh() re-fetches the whole dashboard (dashboard, users at 431
KB, tiers, usage-by-place, latency) and rebuilds #tier-grid with innerHTML, so
the confirmation was wiped a beat after it was written and any half-typed
value in another card was lost. saveShareCap, saveRecallWindow and
saveProjectChatWindow never refreshed at all, so the seven saves disagreed
about what a save does. changeTier's Cancel called refresh() to put one select
back.

The saves are EVALUATED in node rather than grepped for. A source-text check
("no refresh( in the region") is kept as a supplement only: it passes just as
happily when the confirmation is written and then overwritten by the repaint
that replaced refresh(), which is the exact failure the first test is for.

Harness: fetch is stubbed (PUT answers ok with files_updated; GET
/webhooks/admin/tiers answers one tier), document.getElementById is a tiny
map of fake elements, and refresh() is a counter that also does what the real
one does to the grid: resets every status line and every input to its
freshly rendered state. On the original code that counter reads 1 and the
status reads the hint text.
"""

import json
import re
import shutil
import subprocess

import pytest

HTML = "app/static/admin.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")

SAVE_WRAPPERS = (
    "saveProjectChatCap", "saveSearchCap", "saveGenerationCap", "saveImageConfig",
    "saveShareCap", "saveRecallWindow", "saveProjectChatWindow",
)


def _src():
    return open(HTML).read()


def _extract(name, src=None):
    """Pull one top-level function (async or not) out of the page by brace
    matching. The banner test's extractor looks for "function name(" and so
    drops the `async` keyword of an async function; these saves are async and
    must stay awaitable."""
    src = src or _src()
    m = re.search(rf"^(async )?function {re.escape(name)}\(", src, re.M)
    assert m, f"{name} is not a top-level function"
    start = m.start()
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _maybe_extract(name):
    """The helpers do not exist on the pre-fix page; the red run must still
    get as far as the assertions, so a missing helper extracts to nothing."""
    try:
        return _extract(name)
    except AssertionError:
        return ""


def _run(js):
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A tier as /webhooks/admin/tiers serves it, with the fields renderTierCard
# reads. searches_per_month is the value the PUT below is about to set, so a
# repaint from this response is the echo of the save.
TIER = {
    "display_name": "Free", "default_model": "m", "monthly_cost_limit_usd": 1.5,
    "requests_per_minute": 10, "max_images_per_request": 1,
    "allowed_providers": ["anthropic"], "allowed_models": ["*"], "features": {"search": "enabled"},
    "max_input_tokens": 1000, "searches_per_month": 40, "searches_soft_threshold": None,
    "generations_per_month": 3, "recall_max_age_days": None, "project_chat_window_days": None,
    "share_max_archive_mb": None, "share_creations_per_day": 5,
    "images": {"max_long_edge": 1568, "jpeg_quality": 0.8},
}

HINT = "0 = search disabled (Free)"


def _harness():
    """Everything the save path can reach, stubbed. The fake grid mirrors what
    innerHTML does: a repaint hands back NEW status and input elements with
    their rendered defaults, so a confirmation that is not re-applied after the
    repaint is gone, exactly as it was gone after refresh()."""
    return r"""
const BASE = '';
const adminKey = 'test-key';
let currentUserTier = 'admin', simulationState = null;
const window = globalThis;
window.tierGenCaps = {free: 0};

const TIER = %(tier)s;
const HINT = %(hint)s;

// The pristine render of two cards: the one being saved (free) and a
// neighbour (subscriber) that holds a half-typed value.
function pristine() {
  return {
    'tier-grid': {querySelector: sel => { log.querySelector.push(sel); return card; }},
    'src-hard-free': {value: '40', style: {}},
    'src-soft-free': {value: '', style: {}},
    'src-status-free': {textContent: HINT, style: {}},
    'gen-cap-subscriber': {value: '7', style: {}},   // half typed, never saved
    'gen-cap-status-subscriber': {textContent: 'empty = uncapped', style: {}},
  };
}
const log = {fetch: [], querySelector: [], refresh: 0, replaced: 0, renderedHtml: null};
let els = pristine();
const card = {replaceWith(node) { log.replaced++; }};
const tmp = {
  set innerHTML(html) {
    // The repaint: the fresh card arrives with its rendered defaults.
    log.renderedHtml = html;
    const fresh = pristine();
    els['src-status-free'] = fresh['src-status-free'];
    els['src-hard-free'] = fresh['src-hard-free'];
    els['src-soft-free'] = fresh['src-soft-free'];
  },
  get firstElementChild() { return {}; },
};
const document = {
  getElementById: id => els[id] || null,
  createElement: () => tmp,
  querySelector: () => null,
};

// The real refresh() rebuilds #tier-grid with innerHTML (every card) after
// re-fetching the whole dashboard. Count it, and do to the fakes what it does
// to the page: the neighbour's half-typed 7 is gone too.
function refresh() { log.refresh++; els = pristine(); els['gen-cap-subscriber'].value = ''; }

async function fetch(url, init) {
  const method = (init && init.method) || 'GET';
  log.fetch.push({method, url, body: init && init.body ? JSON.parse(init.body) : null});
  if (method === 'PUT') {
    return {ok: true, status: 200, json: async () => ({files_updated: [{}, {}, {}]})};
  }
  if (url.endsWith('/webhooks/admin/tiers')) {
    return {ok: true, status: 200, json: async () => ({tiers: {free: TIER}})};
  }
  return {ok: true, status: 200, json: async () => ({}), text: async () => ''};
}
""" % {"tier": json.dumps(TIER), "hint": json.dumps(HINT)}


def _save_js(call):
    fns = "\n".join([
        _extract("saveSearchCap"),
        _maybe_extract("saveTierField"),
        _maybe_extract("parseIntMin"),
        _maybe_extract("refreshTierCard"),
        _maybe_extract("renderTierCard"),
    ])
    return _harness() + fns + f"""
(async () => {{
  els['src-hard-free'].value = '40';
  await {call};
  console.log(JSON.stringify({{
    refresh: log.refresh,
    status: els['src-status-free'].textContent,
    color: els['src-status-free'].style.color,
    neighbour: els['gen-cap-subscriber'].value,
    hard: els['src-hard-free'].value,
    fetch: log.fetch,
    replaced: log.replaced,
    renderedHasEcho: !!(log.renderedHtml && log.renderedHtml.includes('id="src-hard-free" value="40"')),
    tierGenCaps: window.tierGenCaps,
  }}));
}})().catch(e => {{ console.error(e && e.stack || e); process.exit(2); }});
"""


# ── the bug: a save that re-fetches everything and wipes its own receipt ──

def test_a_search_cap_save_never_calls_refresh_and_keeps_saved_on_screen():
    r = _run(_save_js("saveSearchCap('free', 'searches_per_month')"))
    assert r["refresh"] == 0, (
        f"saveSearchCap called refresh() {r['refresh']} time(s); the whole "
        f"dashboard is re-fetched for a one-field save")
    assert r["status"].startswith("Saved"), (
        f"after the save resolves the status reads {r['status']!r}, not Saved: "
        f"the repaint wiped the confirmation")
    assert r["color"] == "var(--green)"
    assert "3 file(s)" in r["status"], "the files_updated count is the receipt"


def test_saved_is_still_on_screen_after_the_save_resolves():
    # Kept apart from the refresh() count so the red run names the symptom
    # Scott sees, not only the cause.
    r = _run(_save_js("saveSearchCap('free', 'searches_per_month')"))
    assert r["status"].startswith("Saved"), (
        f"the status reads {r['status']!r} after the save resolves: the "
        f"confirmation was wiped by the repaint")


def test_the_neighbouring_card_keeps_its_half_typed_value():
    r = _run(_save_js("saveSearchCap('free', 'searches_per_month')"))
    assert r["neighbour"] == "7", (
        "saving the free card reset an input on the subscriber card")


def test_the_save_re_reads_only_the_tiers_route_and_echoes_the_value():
    r = _run(_save_js("saveSearchCap('free', 'searches_per_month')"))
    puts = [f for f in r["fetch"] if f["method"] == "PUT"]
    gets = [f for f in r["fetch"] if f["method"] == "GET"]
    assert len(puts) == 1
    assert puts[0]["url"].endswith("/webhooks/admin/tunable/tier-field")
    assert puts[0]["body"] == {"tier": "free", "feature": "search",
                               "field": "searches_per_month", "value": 40}
    assert [g["url"] for g in gets] == ["/webhooks/admin/tiers"], (
        f"a one-field save re-fetched {[g['url'] for g in gets]}; only "
        f"/webhooks/admin/tiers is allowed")
    # The card is repainted from the server's answer (rule 4: check the echo),
    # and the confirmation survives that repaint.
    assert r["replaced"] == 1
    assert r["renderedHasEcho"], "the repainted card must carry the value the server now serves"
    assert r["tierGenCaps"]["free"] == 3, "the users table's Files column reads this"
    assert r["status"].startswith("Saved")


def test_validation_failures_do_not_fetch():
    js = _save_js("saveSearchCap('free', 'searches_per_month')").replace(
        "els['src-hard-free'].value = '40';", "els['src-hard-free'].value = '';")
    r = _run(js)
    assert r["fetch"] == []
    assert "required" in r["status"]
    assert r["color"] == "var(--red)"


# ── changeTier: Cancel is a local reset, not a dashboard re-fetch ─────────

def _change_tier_js(confirm_answer, pass_select):
    return _extract("changeTier") + f"""
const BASE = '';
const adminKey = 'test-key';
const log = {{fetch: 0, refresh: 0, alerts: 0}};
const sel = {{value: 'subscriber'}};
const document = {{querySelector: () => sel, getElementById: () => null}};
function confirm() {{ return {json.dumps(confirm_answer)}; }}
function alert() {{ log.alerts++; }}
function refresh() {{ log.refresh++; }}
async function fetch() {{ log.fetch++; return {{ok: true, json: async () => ({{}})}}; }}
(async () => {{
  await changeTier('u-1', 'subscriber', 'free'{", sel" if pass_select else ""});
  console.log(JSON.stringify({{fetch: log.fetch, refresh: log.refresh, select: sel.value}}));
}})().catch(e => {{ console.error(e && e.stack || e); process.exit(2); }});
"""


def test_cancel_on_change_tier_fetches_nothing_and_puts_the_select_back():
    r = _run(_change_tier_js(False, pass_select=False))
    assert r["fetch"] == 0, "Cancel must not hit the network"
    assert r["refresh"] == 0, f"Cancel called refresh() {r['refresh']} time(s) to reset one select"
    assert r["select"] == "free", "the select must show the tier the user still has"


def test_cancel_honours_a_select_passed_in():
    r = _run(_change_tier_js(False, pass_select=True))
    assert (r["fetch"], r["refresh"], r["select"]) == (0, 0, "free")


def test_confirmed_change_still_posts_and_repaints():
    # The whole-dashboard refresh is RIGHT here: a tier move changes the
    # users table, the by-tier cards and the badges.
    r = _run(_change_tier_js(True, pass_select=False))
    assert r["fetch"] == 1
    assert r["refresh"] == 1


# ── supplements: source shape, kept because they are cheap, not because they
#    are sufficient ───────────────────────────────────────────────────────

def _save_region():
    src = _src()
    start = re.search(r"^async function saveTierField\(", src, re.M)
    assert start, "the seven saves must share one helper, saveTierField"
    end = src.index("// --- Remote Configs ---", start.start())
    return src[start.start():end]


def test_no_save_in_the_region_calls_refresh():
    region = _save_region()
    code = "\n".join(ln for ln in region.splitlines() if not ln.lstrip().startswith("//"))
    assert not re.search(r"(?<![A-Za-z_.])refresh\(", code), (
        "a tier save calls refresh(); re-read /webhooks/admin/tiers and repaint "
        "one card instead")
    assert "setTimeout" not in code, "the confirmation must persist"


def test_every_inline_handler_still_has_its_function():
    src = _src()
    for name in SAVE_WRAPPERS:
        assert re.search(rf"^(async )?function {name}\(", src, re.M), f"{name} is gone"
        assert f'onclick="{name}(' in src, f"no card button calls {name}"
    for name in SAVE_WRAPPERS:
        assert "saveTierField(" in _extract(name), f"{name} does not go through saveTierField"


def test_tier_cards_carry_the_attribute_the_repaint_keys_on():
    src = _src()
    assert 'class="tier-card" data-tier="${name}"' in src
    assert '.tier-card[data-tier="${tierName}"]' in src


def test_save_region_copy_carries_no_dashes():
    region = _save_region()
    for d in ("\u2014", "\u2013"):
        assert d not in region
