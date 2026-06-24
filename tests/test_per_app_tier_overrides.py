"""Per-app tier overrides (#249) — Tech Rehearsal caps max_images_per_request=1.

The override is keyed on X-App-ID and must ONLY affect techrehearsal. The
critical regression guard (Scott 2026-06-23): an old/no-header ShoulderSurf
request keeps its tier's normal max_images — old SS TestFlight builds in the
field cannot be broken by the TR override.
"""

import app.routers.config as cfg


def test_overrides_only_for_techrehearsal():
    assert cfg.tier_overrides_for_app("techrehearsal") == {"max_images_per_request": 1}
    assert cfg.tier_overrides_for_app("TechRehearsal") == {"max_images_per_request": 1}  # case-insensitive
    # SS / no header / unknown / unrecognized → NO overrides (tier value untouched)
    assert cfg.tier_overrides_for_app("shouldersurf") == {}
    assert cfg.tier_overrides_for_app(None) == {}
    assert cfg.tier_overrides_for_app("") == {}
    assert cfg.tier_overrides_for_app("unknown") == {}
    assert cfg.tier_overrides_for_app("interviewbuddy") == {}


def _tiers(client, app_id=None):
    headers = {"X-App-ID": app_id} if app_id else {}
    r = client.get("/v1/tiers", headers=headers)
    assert r.status_code == 200
    return r.json()["tiers"]


def test_tiers_catalog_tr_capped_ss_unchanged(client):
    # techrehearsal → every tier shows the capped value (1)
    tr = _tiers(client, "techrehearsal")
    assert all(t["max_images_per_request"] == 1 for t in tr.values())
    # no header (OLD SS build) → tier defaults intact
    ss = _tiers(client)
    assert ss["free"]["max_images_per_request"] == 1
    assert ss["plus"]["max_images_per_request"] == 3
    assert ss["pro"]["max_images_per_request"] == 5
    # explicit shouldersurf → identical to no-header
    assert _tiers(client, "shouldersurf")["pro"]["max_images_per_request"] == 5


def test_usage_me_tr_capped_ss_unchanged(client, pro_user):
    h = pro_user["headers"]
    # pro user, NO header (old SS) → pro's 5 in both the nested + top-level fields
    j = client.get("/v1/usage/me", headers=h).json()
    assert j["max_images_per_request"] == 5
    assert j["app_config"]["max_images_per_request"] == 5
    # same pro user via techrehearsal → capped to 1 in both fields
    j2 = client.get("/v1/usage/me", headers={**h, "X-App-ID": "techrehearsal"}).json()
    assert j2["max_images_per_request"] == 1
    assert j2["app_config"]["max_images_per_request"] == 1
