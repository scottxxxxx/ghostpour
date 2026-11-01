"""The Entitlements panel must keep the app the operator chose.

2026-10-31 audit of admin.html: _TAB_LOADERS and switchTab both call
loadEntitlements() with no argument, and the loader turned a missing
argument straight into "no app param", so the server answered with its
default_app. Every visit to the tab and every global Refresh therefore
snapped the #ent-app select back to the default, and an operator comparing
n400 against i765 lost their place each time. The audit also noted the
panel ignores the global #app filter; see the comment above _entResolveApp
in the page for why that stays uncoupled (one app per request there, a
comma list with an "all apps" empty state here).

The loader is evaluated in node rather than grepped for, because the fix is
a resolution order (argument, then the select, then localStorage, then the
server default) and a source-text assertion cannot tell which branch a
bare call actually takes. The harness stubs fetch to record the URL and
hand back the smallest payload the renderer accepts, so the assertions are
on the request the function made.
"""

import json
import shutil
import subprocess

import pytest

HTML = "app/static/admin.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")


def _src():
    return open(HTML).read()


def _extract(name):
    """Pull one top-level function out of the page by brace matching."""
    src = _src()
    start = src.index(f"function {name}(")
    if src[start - 6:start] == "async ":
        start -= 6  # loadEntitlements is `async function`; the keyword must travel with it
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _node(js):
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A browser-shaped harness. fetch records every URL it was handed and
# answers with the smallest payload loadEntitlements renders without
# throwing (no tiers, no matrix rows, empty knob blocks). The fake #ent-app
# select carries whatever options and value the test says; `select_apps`
# empty models the first visit, before any payload populated it. The 404
# list names apps the stub refuses, for the stale-key case.
_HARNESS = """
const urls = [];
const store = %(store)s;
const notFound = %(not_found)s;
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
const payload = {
  apps: [{id: 'shouldersurf', label: 'Shoulder Surf'}, {id: 'n400', label: 'N-400'}, {id: 'i765', label: 'I-765'}],
  app: {id: 'shouldersurf'},
  tiers: [],
  matrix: {},
  knobs: {
    documents: {}, document_generation: {},
    project_chat_max_input_chars: {}, search: {}, max_images_per_request: {},
  },
  provenance: {matrix_source: 'test', tier_overrides: {}, configs: {}},
};
globalThis.fetch = (url) => {
  urls.push(url);
  const m = /[?&]app=([^&]*)/.exec(url);
  const app = m ? decodeURIComponent(m[1]) : '';
  if (notFound.includes(app)) return Promise.resolve({ok: false, status: 404});
  return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(payload)});
};
const select = {
  value: %(select_value)s,
  options: %(select_apps)s.map(id => ({value: id})),
  innerHTML: '',
};
const content = {innerHTML: ''};
globalThis.document = {getElementById: (id) => (id === 'ent-app' ? select : (id === 'ent-content' ? content : null))};
globalThis.BASE = '';
globalThis.adminKey = 'k';
"""


def _load(arg=None, select_value="", select_apps=(), store=None, not_found=()):
    js = _HARNESS % {
        "store": json.dumps(store or {}),
        "not_found": json.dumps(list(not_found)),
        "select_value": json.dumps(select_value),
        "select_apps": json.dumps(list(select_apps)),
    }
    js += _extract("entEsc") + "\n"
    # The helper is optional so a page WITHOUT the fix still runs its loader
    # and fails on the URL it fetched, rather than on a missing substring.
    if "function _entResolveApp(" in _src():
        js += _extract("_entResolveApp") + "\n"
    js += _extract("loadEntitlements") + "\n"
    call = "loadEntitlements()" if arg is None else f"loadEntitlements({json.dumps(arg)})"
    js += """
(async () => {
  await %s;
  console.log(JSON.stringify({urls, store, content: content.innerHTML}));
})();
""" % call
    return _node(js)


def _app_of(url):
    import re
    m = re.search(r"[?&]app=([^&]*)", url)
    return m.group(1) if m else None


# ── the bug: a bare call must not discard the select ──────────────────────

def test_a_bare_call_keeps_the_app_the_select_shows():
    # _TAB_LOADERS and switchTab call loadEntitlements() with no argument.
    # The operator is looking at n400; the fetch must ask for n400.
    r = _load(select_value="n400", select_apps=["shouldersurf", "n400", "i765"])
    assert len(r["urls"]) == 1, r["urls"]
    assert _app_of(r["urls"][0]) == "n400", (
        f"a bare loadEntitlements() fetched {r['urls'][0]!r}; the select said "
        "n400 and the server default would have replaced it")


def test_a_bare_call_with_an_empty_select_uses_the_persisted_choice():
    # First visit after a page reload: the select has not been populated yet,
    # but the operator chose i765 last time and that choice was persisted.
    r = _load(select_value="", select_apps=[], store={"cz_admin_ent_app": "i765"})
    assert len(r["urls"]) == 1, r["urls"]
    assert _app_of(r["urls"][0]) == "i765", (
        f"a bare loadEntitlements() fetched {r['urls'][0]!r}; localStorage held i765")


def test_an_explicit_app_wins_and_is_persisted():
    # The select's onchange calls loadEntitlements(this.value). It beats both
    # the select's stale value and whatever was persisted, and it becomes the
    # new persisted choice.
    r = _load(arg="ss", select_value="n400", select_apps=["shouldersurf", "n400", "i765"],
              store={"cz_admin_ent_app": "i765"})
    assert len(r["urls"]) == 1, r["urls"]
    assert _app_of(r["urls"][0]) == "ss", r["urls"][0]
    assert r["store"].get("cz_admin_ent_app") == "ss", r["store"]


# ── the edges the fix introduces ──────────────────────────────────────────

def test_nothing_chosen_anywhere_lets_the_server_pick():
    # The positive control for the server default: no argument, empty select,
    # empty storage. The URL must carry no app so default_app still applies.
    r = _load(select_value="", select_apps=[], store={})
    assert len(r["urls"]) == 1, r["urls"]
    assert _app_of(r["urls"][0]) is None, r["urls"][0]
    assert "app=" not in r["urls"][0]


def test_a_stale_persisted_app_is_forgotten_not_fatal():
    # Persistence adds one failure the old code could not have: the stored
    # app is renamed or retired and the server 404s. The tab must not wedge
    # on "Failed to load (404)" with nothing in the select to recover with.
    r = _load(select_value="", select_apps=[], store={"cz_admin_ent_app": "retired"},
              not_found=["retired"])
    assert [_app_of(u) for u in r["urls"]] == ["retired", None], r["urls"]
    assert "cz_admin_ent_app" not in r["store"], r["store"]
    assert "Failed to load" not in r["content"]


def test_a_bare_call_does_not_write_the_persisted_choice():
    # Only an explicit choice is a choice. A bare call that read the select
    # must not rewrite storage, or a Refresh on a default view would pin the
    # default as if the operator had picked it.
    r = _load(select_value="n400", select_apps=["shouldersurf", "n400"], store={})
    assert "cz_admin_ent_app" not in r["store"], r["store"]


# ── the callers this fix cannot touch ─────────────────────────────────────

def test_the_tab_loaders_still_call_the_loader_bare():
    # The fix lives inside loadEntitlements on purpose: _TAB_LOADERS and
    # switchTab belong to another PR's region. If a later change passes an
    # argument there, these tests still hold, but the premise above changes.
    src = _src()
    assert "entitlements: () => loadEntitlements()," in src
    assert "if (name === 'entitlements') loadEntitlements();" in src


def test_the_select_and_refresh_button_pass_the_choice_explicitly():
    src = _src()
    assert '<select id="ent-app" class="period-select" onchange="loadEntitlements(this.value)">' in src
    assert "onclick=\"loadEntitlements(document.getElementById('ent-app').value)\"" in src
