"""Drift warnings must keep carrying information.

2026-09-06: a config sync silently failed (curl absent from the image),
leaving a v24 prompt serving against v25 guards. `detect_overlay_drift`
caught it, fired, and named the right slug and pointer, in the output
being read at the time. It was missed because two of the three warnings
are permanent and expected, so the block had its usual shape.

That is not a missing safeguard. It is a safeguard whose signal-to-noise
had gone to zero, which is harder to catch because everything looks
present and working. An overlay now declares what it holds on purpose, so
anything still warning is a real finding.
"""
import json

from app.routers import config as cfg


def _write(tmp_path, monkeypatch, bundle, overlay, declared=None):
    b = tmp_path / "bundle"; o = tmp_path / "overlay"
    (b / "n400").mkdir(parents=True); (o / "n400").mkdir(parents=True)
    (b / "n400" / "budget.json").write_text(json.dumps(bundle))
    (o / "n400" / "budget.json").write_text(json.dumps(overlay))
    if declared is not None:
        (o / cfg._OVERRIDES_FILE).write_text(json.dumps(declared))
    monkeypatch.setattr(cfg, "_BUNDLED_DIR", b)
    monkeypatch.setattr(cfg, "CONFIG_DIR", o)


def test_a_declared_override_is_expected_not_a_warning(tmp_path, monkeypatch):
    """Scott raised the N-400 cap by hand. That difference is permanent and
    must not sit in the warning stream drowning real findings."""
    _write(tmp_path, monkeypatch,
           {"monthly_cost_limit_usd": 5.0},
           {"monthly_cost_limit_usd": 20.0},
           declared={"n400/budget": ["/monthly_cost_limit_usd"]})
    unexpected, expected = cfg.split_drift(cfg.detect_overlay_drift())
    assert unexpected == {}
    assert expected == {"n400/budget": ["/monthly_cost_limit_usd"]}


def test_an_undeclared_difference_still_warns(tmp_path, monkeypatch):
    """The whole point: a silently failed sync must still be a finding."""
    _write(tmp_path, monkeypatch,
           {"systemPrompt": "v25 text", "monthly_cost_limit_usd": 5.0},
           {"systemPrompt": "v24 text", "monthly_cost_limit_usd": 20.0},
           declared={"n400/budget": ["/monthly_cost_limit_usd"]})
    unexpected, expected = cfg.split_drift(cfg.detect_overlay_drift())
    assert unexpected == {"n400/budget": ["/systemPrompt"]}, (
        "the undeclared drift is the finding and must not be filed with the "
        "deliberate one")
    assert expected == {"n400/budget": ["/monthly_cost_limit_usd"]}


def test_declaring_one_pointer_does_not_silence_the_slug(tmp_path, monkeypatch):
    """A declaration is per-pointer, never per-slug. Silencing a whole slug
    is how the next real drift gets hidden by an old deliberate one."""
    _write(tmp_path, monkeypatch,
           {"a": 1, "b": 2},
           {"a": 9, "b": 8}, declared={"n400/budget": ["/a"]})
    unexpected, _ = cfg.split_drift(cfg.detect_overlay_drift())
    assert unexpected == {"n400/budget": ["/b"]}


def test_no_declaration_behaves_exactly_as_before(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"a": 1}, {"a": 2})
    unexpected, expected = cfg.split_drift(cfg.detect_overlay_drift())
    assert unexpected == {"n400/budget": ["/a"]} and expected == {}


def test_the_declaration_never_reaches_the_wire(tmp_path, monkeypatch):
    """GET /v1/config/{name} returns the resolved config dict verbatim, so
    ops metadata inside a config file would ship to every client. It lives
    in a sidecar for that reason, and this test is what stops someone
    moving it back in."""
    _write(tmp_path, monkeypatch, {"a": 1}, {"a": 2},
           declared={"n400/budget": ["/a"]})
    overlay = json.loads((tmp_path / "overlay" / "n400" / "budget.json").read_text())
    assert "_intentional_overrides" not in overlay
    assert set(overlay) == {"a"}, "the served config carries only its own keys"
    assert cfg.intentional_overrides() == {"n400/budget": {"/a"}}


def test_a_missing_or_broken_sidecar_warns_about_everything(tmp_path, monkeypatch):
    """Degrade toward noise, never toward silence: an unreadable
    declaration must not suppress a real finding."""
    _write(tmp_path, monkeypatch, {"a": 1}, {"a": 2})
    assert cfg.intentional_overrides() == {}
    (tmp_path / "overlay" / cfg._OVERRIDES_FILE).write_text("{ this is not json")
    assert cfg.intentional_overrides() == {}
    unexpected, _ = cfg.split_drift(cfg.detect_overlay_drift())
    assert unexpected == {"n400/budget": ["/a"]}
