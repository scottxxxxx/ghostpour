"""The admin page must say WHICH server it is driving.

2026-09-10: Scott raised the N-400 spend cap twice through this page, saw a
green "Saved! v3" both times, and neither write reached production. Bifrost
read the edge access log across its whole retention window and found no PUT to
/webhooks/admin/config/n400/budget at all, not even a 4xx. He had a second copy
of the page open against a local server. The save handler was correct code and
the server was correct; the page could not say which server it was talking to,
because `BASE = ''` means it always drives whoever served it. Cost: three days
of blocked QA, two false "the cap is raised" reports, and a spend estimate
reasoned backwards from a block with an entirely different cause.

`deploymentWarning()` is evaluated in node rather than grepped for, because a
source-text assertion passes just as happily when the function is never called
and would not have caught the boundary that matters here: the banner must be
IMPOSSIBLE on production, or it becomes a banner people stop seeing.
"""

import json
import re
import shutil
import subprocess

import pytest

HTML = "app/static/admin.html"
PROD_SHA = "d99ab05f8e979d7bae6f257d72307cd67b8c5c1c"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to evaluate the page's JS")


def _extract(name):
    """Pull one top-level function out of the page by brace matching."""
    src = open(HTML).read()
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


def _warn(host, sha):
    js = _extract("deploymentWarning") + (
        f"\nconsole.log(JSON.stringify(deploymentWarning("
        f"{json.dumps(host)}, {json.dumps(sha)})));"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


# ── the banner must be impossible on production ───────────────────────────

@pytest.mark.parametrize("host", [
    "cz.shouldersurf.com",
    "api.ghostpour.com",
    "cz.shouldersurf.com:443",
])
def test_production_never_warns(host):
    assert _warn(host, PROD_SHA) is None


def test_a_deploy_502_does_not_paint_the_banner_on_prod():
    # /health unreachable is gitSha null. During the 3.5s window every merge
    # opens, prod must not accuse itself of being a local box.
    assert _warn("cz.shouldersurf.com", None) is None


# ── the case that actually happened ───────────────────────────────────────

@pytest.mark.parametrize("host", [
    "localhost:8000",
    "127.0.0.1:8000",
    "localhost",
    "ghostpour",            # a bare docker hostname, no dot
    "mac-mini.local",
    "192.168.1.50:8000",
    "10.0.0.7",
    "172.17.0.3",
])
def test_a_local_server_is_named_as_not_production(host):
    warning = _warn(host, PROD_SHA)
    assert warning is not None, f"{host} must warn even with a real-looking SHA"
    assert warning["level"] == "local"
    assert "not production" in warning["reason"].lower()


def test_a_public_host_with_no_ci_sha_is_unidentified_not_local():
    warning = _warn("staging.example.com", "unknown")
    assert warning is not None
    assert warning["level"] == "unidentified"


def test_the_local_verdict_beats_the_sha_verdict():
    # A local box with no SHA is local, the more specific and more useful
    # of the two messages.
    assert _warn("localhost:8000", "unknown")["level"] == "local"


def test_172_16_through_31_is_private_but_172_32_is_not():
    # The private range stops at 172.31. Getting this wrong either misses a
    # docker network or accuses a public host.
    assert _warn("172.16.0.1", PROD_SHA)["level"] == "local"
    assert _warn("172.31.255.254", PROD_SHA)["level"] == "local"
    assert _warn("172.32.0.1", PROD_SHA) is None
    assert _warn("172.15.0.1", PROD_SHA) is None


# ── the page must actually call it, and must not go back to silence ───────

def test_the_banner_is_rendered_from_the_health_probe():
    src = open(HTML).read()
    assert "renderDeploymentBanner(deploymentWarning(host, sha))" in src, (
        "computing the warning without painting it is the same silence that "
        "hid the 09-10 mix-up")


def test_the_badge_never_returns_early_on_an_unknown_sha():
    # The old code did `if (!sha || sha === 'unknown') return;`, so a local
    # dev image rendered NO badge, which reads as "it didn't load".
    src = open(HTML).read()
    assert not re.search(r"if \(!sha \|\| sha === 'unknown'\) return;", src)


def test_the_badge_states_the_host():
    assert "on ${esc(host)}" in open(HTML).read()
