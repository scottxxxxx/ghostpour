"""Build-pinned config variants (2026-08-03).

The lever for the one case that cannot be solved additively. Build 803 is
the App Store release, is most of the base, and decodes whole-file with no
per-entry tolerance. Adding fields is always safe for it, so almost
everything can ship to everyone. What cannot is a genuine restructure:
removing, renaming or retyping a field it requires discards the entire
file on that build, and it has no way to report that it happened.

So `build-lte-<N>/` holds the shape served to builds at or below N, and
everyone else keeps getting the current one.

Two properties do the safety work:

- An unknown build resolves to the TIGHTEST pin, never to none. SS reports
  their build stamp can silently fall back to a hardcoded baseline when
  the git-count script does not run, so a number is a claim rather than a
  fact. Serving a pinned client the modern shape is the failure that
  cannot be undone remotely; serving a modern client the pinned shape is
  merely stale.
- Falling through. A pin overrides only the configs it defines, so pinning
  one file does not freeze a build's entire config tree at the moment the
  pin was created.
"""

import json
import shutil

import pytest

from app.routers.config import (
    CONFIG_DIR,
    build_pin_dirs,
    candidate_slugs,
    resolve_build_pin,
)

SS = {"X-App-ID": "shouldersurf"}


def _write_pin(ceiling: int, name: str, payload: dict) -> None:
    path = CONFIG_DIR / f"build-lte-{ceiling}" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


@pytest.fixture(autouse=True)
def _clean_pins():
    yield
    for d in CONFIG_DIR.glob("build-lte-*"):
        shutil.rmtree(d, ignore_errors=True)


# --- which pin applies -----------------------------------------------


def test_no_pins_means_no_pinning():
    """The lever is inert until somebody creates a pin directory."""
    assert build_pin_dirs() == []
    assert resolve_build_pin("803") is None
    assert resolve_build_pin(None) is None


def test_a_build_at_or_below_the_ceiling_is_pinned():
    _write_pin(803, "tiers", {"version": 1})
    assert resolve_build_pin("803") == "build-lte-803"
    assert resolve_build_pin("500") == "build-lte-803"


def test_a_build_above_every_ceiling_is_not_pinned():
    _write_pin(803, "tiers", {"version": 1})
    assert resolve_build_pin("906") is None


def test_the_tightest_applicable_pin_wins():
    """803 and 900 both apply to build 803; the tighter one is the more
    specific statement about that build."""
    _write_pin(803, "tiers", {"version": 1})
    _write_pin(900, "tiers", {"version": 2})
    assert resolve_build_pin("803") == "build-lte-803"
    assert resolve_build_pin("850") == "build-lte-900"


@pytest.mark.parametrize("build", [None, "", "not-a-number", "335"])
def test_an_unknown_or_untrustworthy_build_gets_the_tightest_pin(build):
    """SS's build stamp falls back to a hardcoded 335 when the git-count
    script does not run, so a low number is not evidence of an old install
    and a missing one is not evidence of anything. Serving a pinned client
    the modern shape cannot be undone remotely; the reverse is just stale.
    """
    _write_pin(803, "tiers", {"version": 1})
    _write_pin(900, "tiers", {"version": 2})
    assert resolve_build_pin(build) == "build-lte-803"


# --- resolution order ------------------------------------------------


def test_pin_sits_between_tester_and_production():
    """A tester asked to see a specific change, so that intent outranks a
    build ceiling; but a tester on a pinned build still falls through to
    the pin for anything the tester tree omits."""
    assert candidate_slugs("shouldersurf", "tiers", "tester", "build-lte-803") == [
        "tester/shouldersurf/tiers", "tester/tiers",
        "build-lte-803/shouldersurf/tiers", "build-lte-803/tiers",
        "shouldersurf/tiers", "tiers",
    ]


def test_production_with_no_pin_is_unchanged():
    assert candidate_slugs("shouldersurf", "tiers") == [
        "shouldersurf/tiers", "tiers"]


# --- serving ---------------------------------------------------------


def _reload(client):
    from app.routers.config import load_remote_configs
    client.app.state.remote_configs = load_remote_configs()


def test_a_pinned_build_gets_the_pinned_shape(client):
    _write_pin(803, "idle-tips", {"version": 1, "tips": ["OLD SHAPE"]})
    _reload(client)
    r = client.get("/v1/config/idle-tips", headers={**SS, "X-App-Build": "803"})
    assert r.status_code == 200
    assert r.json()["tips"] == ["OLD SHAPE"]
    assert r.headers["x-config-build-pin"] == "build-lte-803"


def test_a_current_build_gets_the_current_shape(client):
    _write_pin(803, "idle-tips", {"version": 1, "tips": ["OLD SHAPE"]})
    _reload(client)
    r = client.get("/v1/config/idle-tips", headers={**SS, "X-App-Build": "906"})
    assert r.json().get("tips") != ["OLD SHAPE"]
    assert r.headers["x-config-build-pin"] == "none"


def test_pinning_one_config_does_not_freeze_the_rest(client):
    """The fallback half. Pinning tiers must not also pin idle-tips at
    whatever it was when the pin was made."""
    _write_pin(803, "tiers", {"version": 1, "tiers": {}})
    _reload(client)
    r = client.get("/v1/config/idle-tips", headers={**SS, "X-App-Build": "803"})
    assert r.status_code == 200
    assert r.headers["x-config-build-pin"] == "build-lte-803"
    assert not r.headers["x-config-resolved"].startswith("build-lte-")


def test_a_build_that_lies_low_is_served_conservatively(client):
    """335 is their broken stamp and could be the newest code. It still
    gets the pin, which is stale rather than broken."""
    _write_pin(803, "idle-tips", {"version": 1, "tips": ["OLD SHAPE"]})
    _reload(client)
    r = client.get("/v1/config/idle-tips", headers={**SS, "X-App-Build": "335"})
    assert r.json()["tips"] == ["OLD SHAPE"]


def test_a_headerless_client_is_served_conservatively(client):
    _write_pin(803, "idle-tips", {"version": 1, "tips": ["OLD SHAPE"]})
    _reload(client)
    r = client.get("/v1/config/idle-tips", headers=SS)
    assert r.json()["tips"] == ["OLD SHAPE"]
    assert r.headers["x-config-build-pin"] == "build-lte-803"
