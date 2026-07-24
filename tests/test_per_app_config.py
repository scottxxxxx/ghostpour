"""Phase B1 — per-app config resolution (#249).

Backward-compatible: with no per-app files present (pre-B2), every lookup falls
back to today's flat filenames, so existing clients are unchanged. These tests
cover the resolution helpers, subdir-aware loading, and the /v1/config wire
behavior (app dir, Option-C tr- alias, flat fallback, unknown-app 404).
"""

import json

import app.routers.config as cfg


# --- pure helpers -----------------------------------------------------------

def test_resolve_app_dir_fails_open_to_default():
    # missing / blank / "unknown" header → default app (shouldersurf)
    assert cfg.resolve_app_dir(None) == "shouldersurf"
    assert cfg.resolve_app_dir("") == "shouldersurf"
    assert cfg.resolve_app_dir("unknown") == "shouldersurf"
    # known apps → their dirs
    assert cfg.resolve_app_dir("shouldersurf") == "shouldersurf"
    assert cfg.resolve_app_dir("techrehearsal") == "techrehearsal"
    # case / whitespace insensitive (older builds / proxies may vary)
    assert cfg.resolve_app_dir(" ShoulderSurf ") == "shouldersurf"
    assert cfg.resolve_app_dir("TECHREHEARSAL") == "techrehearsal"
    # UNRECOGNIZED id → fail open to default, never None (no 404). Protects
    # older SS builds that might send an unexpected value.
    assert cfg.resolve_app_dir("interviewbuddy") == "shouldersurf"
    assert cfg.resolve_app_dir("com.weirtech.shouldersurf") == "shouldersurf"


def test_candidate_slugs_tr_alias_and_flat_fallback():
    # TR legacy prefixed name: app file, Option-C stripped alias, then flat
    assert cfg.candidate_slugs("techrehearsal", "tr-jd-analysis") == [
        "techrehearsal/tr-jd-analysis",
        "techrehearsal/jd-analysis",
        "tr-jd-analysis",
    ]
    # TR clean name: no alias, app file then flat
    assert cfg.candidate_slugs("techrehearsal", "jd-analysis") == [
        "techrehearsal/jd-analysis",
        "jd-analysis",
    ]
    # SS: app file then flat (no tr- stripping)
    assert cfg.candidate_slugs("shouldersurf", "tiers") == ["shouldersurf/tiers", "tiers"]


def test_load_apps_registry():
    reg = cfg.load_apps(force=True)
    assert reg["default_app"] == "shouldersurf"
    assert reg["apps"]["techrehearsal"]["dir"] == "techrehearsal"
    assert reg["apps"]["shouldersurf"]["label"] == "ShoulderSurf"


def test_load_remote_configs_walks_subdirs(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    (tmp_path / "tiers.json").write_text(json.dumps({"version": 1}))
    appdir = tmp_path / "techrehearsal"
    appdir.mkdir()
    (appdir / "jd-analysis.json").write_text(json.dumps({"version": 2}))
    configs = cfg.load_remote_configs()
    assert "tiers" in configs                      # flat slug = stem
    assert "techrehearsal/jd-analysis" in configs  # composite slug = rel posix
    assert configs["techrehearsal/jd-analysis"]["version"] == 2


# --- /v1/config wire behavior ----------------------------------------------

def _get(client, name, app_id=None):
    headers = {}
    if app_id is not None:
        headers["X-App-ID"] = app_id
    return client.get(f"/v1/config/{name}", headers=headers)


def test_no_header_resolves_shouldersurf_flat(client):
    client.app.state.remote_configs = {"tiers": {"version": 5}}
    r = _get(client, "tiers")  # no X-App-ID
    assert r.status_code == 200
    assert r.headers["X-Config-Resolved"] == "tiers"


def test_per_app_file_wins_over_flat(client):
    client.app.state.remote_configs = {
        "shouldersurf/tiers": {"version": 9, "marker": "ss"},
        "tiers": {"version": 5, "marker": "flat"},
    }
    r = _get(client, "tiers", "shouldersurf")
    assert r.status_code == 200
    assert r.headers["X-Config-Resolved"] == "shouldersurf/tiers"
    assert r.json()["marker"] == "ss"


def test_tr_prefix_alias_resolves_clean_file(client):
    client.app.state.remote_configs = {"techrehearsal/jd-analysis": {"version": 3}}
    # legacy prefixed request → alias to the clean per-app file
    r1 = _get(client, "tr-jd-analysis", "techrehearsal")
    assert r1.status_code == 200
    assert r1.headers["X-Config-Resolved"] == "techrehearsal/jd-analysis"
    # clean request (post-cutover) → same file
    r2 = _get(client, "jd-analysis", "techrehearsal")
    assert r2.status_code == 200
    assert r2.headers["X-Config-Resolved"] == "techrehearsal/jd-analysis"


def test_flat_fallback_preserved_pre_migration(client):
    # No per-app file yet — TR's flat tr- file still resolves (B2 not run)
    client.app.state.remote_configs = {"tr-idle-tips": {"version": 2}}
    r = _get(client, "tr-idle-tips", "techrehearsal")
    assert r.status_code == 200
    assert r.headers["X-Config-Resolved"] == "tr-idle-tips"


def test_unrecognized_app_fails_open_to_flat(client):
    # An older/odd SS build sending an unexpected X-App-ID must still get its
    # config (flat fallback), not a 404.
    client.app.state.remote_configs = {"tiers": {"version": 5}}
    r = _get(client, "tiers", "com.weirtech.shouldersurf")
    assert r.status_code == 200
    assert r.headers["X-Config-Resolved"] == "tiers"


def test_uppercase_app_id_resolves(client):
    client.app.state.remote_configs = {
        "shouldersurf/tiers": {"version": 9, "marker": "ss"},
        "tiers": {"version": 5},
    }
    r = _get(client, "tiers", "ShoulderSurf")
    assert r.status_code == 200
    assert r.headers["X-Config-Resolved"] == "shouldersurf/tiers"


def test_unknown_config_returns_404(client):
    client.app.state.remote_configs = {"tiers": {"version": 5}}
    r = _get(client, "does-not-exist", "shouldersurf")
    assert r.status_code == 404
    assert "Unknown config" in r.json()["error"]


# --- dashboard plumbing -----------------------------------------------------

def test_config_app_bucketing():
    from app.routers.webhooks import _config_app
    assert _config_app("techrehearsal/jd-analysis") == "techrehearsal"  # composite authoritative
    assert _config_app("shouldersurf/tiers.es") == "shouldersurf"
    assert _config_app("tr-mock-interview") == "techrehearsal"          # flat tr- convention
    assert _config_app("tiers") == "shouldersurf"
    assert _config_app("model-routing") == "shared"


def test_bundle_route_declared_before_catchall():
    # The greedy {slug:path} detail route would swallow `…/bundle` if declared
    # first. Lock the order so config diff/sync keeps working with per-app slugs.
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    bundle = paths.index("/webhooks/admin/config/{slug:path}/bundle")
    detail = paths.index("/webhooks/admin/config/{slug:path}")
    assert bundle < detail


# --- server-only gate (2026-07-24) -----------------------------------------

def test_server_only_config_404s_like_unknown(client):
    client.app.state.remote_configs = {
        "techrehearsal/intake": {"version": 7, "server_only": True,
                                 "systemPrompt": "secret"},
    }
    r = _get(client, "tr-intake", "techrehearsal")
    assert r.status_code == 404
    # indistinguishable from a slug that does not exist
    r2 = _get(client, "tr-no-such-config", "techrehearsal")
    assert r.json() == {"error": "Unknown config: tr-intake"}
    assert r2.status_code == 404
    assert "secret" not in r.text


def test_server_only_never_falls_through_to_flat(client):
    # a gated per-app file must not leak via the flat fallback either
    client.app.state.remote_configs = {
        "model-routing": {"version": 16, "server_only": True,
                          "apps": {}},
    }
    r = _get(client, "model-routing", "shouldersurf")
    assert r.status_code == 404
    r2 = _get(client, "model-routing")  # no header at all
    assert r2.status_code == 404


def test_client_facing_configs_stay_served(client):
    client.app.state.remote_configs = {
        "techrehearsal/jd-analysis": {"version": 9, "systemPrompt": "byok"},
        "techrehearsal/protected-prompts": {"version": 7},
        "techrehearsal/practice-openers": {"version": 1},
        "techrehearsal/idle-tips": {"version": 4},
    }
    for name in ("tr-jd-analysis", "tr-protected-prompts",
                 "tr-practice-openers", "tr-idle-tips"):
        r = _get(client, name, "techrehearsal")
        assert r.status_code == 200, name


def test_all_bundled_tr_prompt_configs_carry_the_flag():
    """The gate list is data, so pin it: every managed TR prompt config
    in the bundle is server_only, and the six client-fetched slugs
    (TR's authoritative list, 2026-07-24) are not."""
    import pathlib
    fetched = {"idle-tips", "protected-prompts", "llm-providers",
               "model-capabilities", "jd-analysis", "practice-openers"}
    tr_dir = pathlib.Path("config/remote/techrehearsal")
    for path in tr_dir.glob("*.json"):
        data = json.loads(path.read_text())
        if path.stem in fetched:
            assert not data.get("server_only"), path.name
        else:
            assert data.get("server_only") is True, path.name
    routing = json.loads(
        pathlib.Path("config/remote/model-routing.json").read_text())
    assert routing.get("server_only") is True
