"""Promo runtime MVP — serve creative, resolve on launch, ingest events, report.

Thin runtime over the campaign store (#293) and the cta_id contract (#306):
GET /v1/promo/assets/{name}, GET /v1/promo/resolve, POST /v1/promo/events,
GET /webhooks/admin/campaign/{id}/report. App-scoped by X-App-ID.
"""

ADMIN = {"X-Admin-Key": "test-admin-key"}
SS = "shouldersurf"
PRO_EMAIL = "test-pro-user@test.com"  # _insert_user sets {user_id}@test.com


def _make_campaign(client, *, cid, app_id=SS, status="active", targeting=None,
                   frequency=None, variants=None):
    body = {
        "id": cid, "name": cid, "app_id": app_id, "status": status, "priority": 10,
        "targeting": targeting or {}, "frequency": frequency or {},
        "placements": [{"placement": "launch", "priority": 10}],
        "variants": variants or [{
            "variant_id": "html", "weight": 100, "render": "html",
            "html_url": "https://api.ghostpour.com/v1/promo/assets/ss-launch-techrehearsal.html",
        }],
    }
    r = client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return body


def test_serve_promo_asset(client):
    r = client.get("/v1/promo/assets/ss-launch-techrehearsal.html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "promo_cta_id=get_tr" in r.text  # attribution wired into the creative
    assert client.get("/v1/promo/assets/nope.html").status_code == 404
    assert client.get("/v1/promo/assets/..%2f..%2fconfig%2ftiers.yml").status_code == 404


def test_resolve_app_scoped_and_targeted(client, pro_user):
    _make_campaign(client, cid="ss_promo_1", targeting={"users": [PRO_EMAIL]})
    h = {**pro_user["headers"], "X-App-ID": SS}
    j = client.get("/v1/promo/resolve?device_id=devA", headers=h).json()
    assert j["campaign_id"] == "ss_promo_1"
    assert j["variant"]["render"] == "html"
    # app scoping: same campaign must NOT resolve for a different app
    other = {**pro_user["headers"], "X-App-ID": "techrehearsal"}
    assert client.get("/v1/promo/resolve?device_id=devA", headers=other).json() == {}


def test_resolve_targeting_excludes_other_users(client, pro_user):
    _make_campaign(client, cid="ss_promo_2", targeting={"users": ["someone-else@x.com"]})
    h = {**pro_user["headers"], "X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=devB", headers=h).json() == {}


def test_events_and_report_funnel(client, pro_user):
    _make_campaign(client, cid="ss_promo_3", targeting={"users": [PRO_EMAIL]})
    h = {**pro_user["headers"], "X-App-ID": SS}
    assert client.post("/v1/promo/events", headers=h, json={
        "event_type": "impression", "campaign_id": "ss_promo_3",
        "variant_id": "html", "device_id": "devC"}).status_code == 204
    assert client.post("/v1/promo/events", headers=h, json={
        "event_type": "click", "campaign_id": "ss_promo_3", "variant_id": "html",
        "device_id": "devC", "cta_id": "get_tr"}).status_code == 204

    rep = client.get("/webhooks/admin/campaign/ss_promo_3/report", headers=ADMIN).json()
    assert rep["impressions"] == 1
    assert rep["clicks"] == 1
    assert rep["ctr"] == 1.0
    assert rep["clicks_by_cta"] == {"get_tr": 1}
    assert rep["reach_devices"] == 1

    assert client.post("/v1/promo/events", headers=h, json={
        "event_type": "bogus", "campaign_id": "ss_promo_3", "device_id": "devC"}).status_code == 400


def test_frequency_cap_hides_after_max(client, pro_user):
    _make_campaign(client, cid="ss_promo_4", targeting={"users": [PRO_EMAIL]},
                   frequency={"max_impressions": 1})
    h = {**pro_user["headers"], "X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=devD", headers=h).json()["campaign_id"] == "ss_promo_4"
    client.post("/v1/promo/events", headers=h, json={
        "event_type": "impression", "campaign_id": "ss_promo_4", "device_id": "devD"})
    # frequency cap spent -> no longer resolves for that device
    assert client.get("/v1/promo/resolve?device_id=devD", headers=h).json() == {}


def test_convert_suppresses_campaign_for_that_device(client, pro_user):
    # The client reports `convert` but doesn't self-suppress, so once a device
    # converts on a campaign GP stops resolving it for that device by default.
    _make_campaign(client, cid="ss_promo_conv", targeting={"users": [PRO_EMAIL]})
    h = {**pro_user["headers"], "X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=devCV", headers=h).json()["campaign_id"] == "ss_promo_conv"
    # impression creates the presentation row; then the device converts
    client.post("/v1/promo/events", headers=h, json={
        "event_type": "impression", "campaign_id": "ss_promo_conv", "variant_id": "html", "device_id": "devCV"})
    client.post("/v1/promo/events", headers=h, json={
        "event_type": "convert", "campaign_id": "ss_promo_conv", "variant_id": "html", "device_id": "devCV"})
    # converted -> suppressed for this device, but a fresh device still sees it
    assert client.get("/v1/promo/resolve?device_id=devCV", headers=h).json() == {}
    assert client.get("/v1/promo/resolve?device_id=devCV2", headers=h).json()["campaign_id"] == "ss_promo_conv"


def test_repeat_after_convert_opt_out_keeps_showing(client, pro_user):
    # A campaign can opt back in to showing after a convert.
    _make_campaign(client, cid="ss_promo_repeat", targeting={"users": [PRO_EMAIL]},
                   frequency={"repeat_after_convert": True})
    h = {**pro_user["headers"], "X-App-ID": SS}
    client.post("/v1/promo/events", headers=h, json={
        "event_type": "impression", "campaign_id": "ss_promo_repeat", "variant_id": "html", "device_id": "devRP"})
    client.post("/v1/promo/events", headers=h, json={
        "event_type": "convert", "campaign_id": "ss_promo_repeat", "variant_id": "html", "device_id": "devRP"})
    assert client.get("/v1/promo/resolve?device_id=devRP", headers=h).json()["campaign_id"] == "ss_promo_repeat"


def test_draft_campaign_not_resolved(client, pro_user):
    _make_campaign(client, cid="ss_promo_5", status="draft", targeting={"users": [PRO_EMAIL]})
    h = {**pro_user["headers"], "X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=devE", headers=h).json() == {}


# --- unauthenticated path: the unsigned base is the prime cross-promo audience ---

def test_resolve_unauthenticated_gets_broad_campaign(client):
    # No token, just device_id + X-App-ID. A campaign with no user/tier
    # constraint must reach the unsigned base (BYOK / on-device).
    _make_campaign(client, cid="ss_broad", targeting={})
    r = client.get("/v1/promo/resolve?device_id=anon-1", headers={"X-App-ID": SS})
    assert r.json()["campaign_id"] == "ss_broad"


def test_resolve_unauthenticated_skips_user_and_tier_targeted(client):
    _make_campaign(client, cid="ss_user_only", targeting={"users": ["scott@weirtech.com"]})
    _make_campaign(client, cid="ss_tier_only", app_id=SS, targeting={"tiers": ["pro"]})
    h = {"X-App-ID": SS}
    # anonymous can't satisfy user/tier targeting -> nothing
    assert client.get("/v1/promo/resolve?device_id=anon-2", headers=h).json() == {}


def test_events_unauthenticated_recorded_with_null_user(client):
    _make_campaign(client, cid="ss_anon_evt", targeting={})
    h = {"X-App-ID": SS}
    assert client.post("/v1/promo/events", headers=h, json={
        "event_type": "impression", "campaign_id": "ss_anon_evt", "device_id": "anon-3"}).status_code == 204
    assert client.post("/v1/promo/events", headers=h, json={
        "event_type": "click", "campaign_id": "ss_anon_evt", "device_id": "anon-3", "cta_id": "get_tr"}).status_code == 204
    rep = client.get("/webhooks/admin/campaign/ss_anon_evt/report", headers=ADMIN).json()
    assert rep["impressions"] == 1 and rep["clicks"] == 1


def test_campaign_events_timeline(client, pro_user):
    # The dashboard Activity view reads this: raw interactions with dwell + cta.
    _make_campaign(client, cid="ss_evt_tl", targeting={"users": [PRO_EMAIL]})
    h = {**pro_user["headers"], "X-App-ID": SS}
    client.post("/v1/promo/events", headers=h, json={"event_type": "impression", "campaign_id": "ss_evt_tl", "device_id": "devT"})
    client.post("/v1/promo/events", headers=h, json={"event_type": "dismiss", "campaign_id": "ss_evt_tl", "device_id": "devT", "visible_ms": 4200})
    client.post("/v1/promo/events", headers=h, json={"event_type": "click", "campaign_id": "ss_evt_tl", "device_id": "devT", "cta_id": "get_tr"})

    ev = client.get("/webhooks/admin/campaign/ss_evt_tl/events", headers=ADMIN).json()["events"]
    assert len(ev) == 3
    assert {e["event_type"] for e in ev} == {"impression", "dismiss", "click"}
    assert next(e for e in ev if e["event_type"] == "dismiss")["visible_ms"] == 4200  # dwell
    assert next(e for e in ev if e["event_type"] == "click")["cta_id"] == "get_tr"     # what was clicked
    assert ev[0]["created_at"] >= ev[-1]["created_at"]  # newest first
    # enriched with readable user (email/tier); device/locale keys present (None w/o telemetry)
    assert all(e["email"] == PRO_EMAIL and e["tier"] == "pro" for e in ev)
    assert all("device" in e and "locale" in e for e in ev)
    # admin required
    assert client.get("/webhooks/admin/campaign/ss_evt_tl/events", headers={"X-Admin-Key": "wrong"}).status_code == 403


def test_signed_in_targeting_field(client, pro_user):
    # signed_in:false -> only the unsigned base sees it
    _make_campaign(client, cid="ss_anon_only", targeting={"signed_in": False})
    assert client.get("/v1/promo/resolve?device_id=d1",
                      headers={"X-App-ID": SS}).json()["campaign_id"] == "ss_anon_only"
    assert client.get("/v1/promo/resolve?device_id=d1",
                      headers={**pro_user["headers"], "X-App-ID": SS}).json() == {}


# --- Phase 1: existing-data targeting (locale / version / usage / device) ---

def _insert_telemetry(db_path, device_id, event_type="app_start", *, app_locale=None,
                      app_version=None, device_model=None, received_at=None,
                      country=None, region=None, city=None):
    import sqlite3, uuid
    from datetime import datetime, timezone
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO telemetry_events (id, event_type, device_id, received_at, app_locale, app_version, device_model, country, region, city)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), event_type, device_id, received_at, app_locale, app_version, device_model, country, region, city),
    )
    con.commit()
    con.close()


def _seed_geo(db_path, n, prefix, **geo):
    """Seed n distinct devices in a geo segment — the enforced min-audience
    floor (25) means geo tests must build a real segment before a geo-targeted
    campaign can activate or resolve."""
    for i in range(n):
        _insert_telemetry(db_path, f"{prefix}-{i}", **geo)


def _insert_campaign_raw(db_path, cid, targeting, app_id=SS):
    """Insert an ACTIVE campaign directly into sqlite, bypassing the admin
    CRUD's activation-time floor check — lets resolve-side floor enforcement
    be tested in isolation (defense in depth)."""
    import json as _json, sqlite3
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO promo_campaigns (id, name, status, app_id, priority, targeting, frequency, placements, variants, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cid, "active", app_id, 10, _json.dumps(targeting), "{}",
         _json.dumps([{"placement": "launch", "priority": 10}]),
         _json.dumps([{"variant_id": "html", "weight": 100, "render": "html",
                       "html_url": "https://api.ghostpour.com/v1/promo/assets/ss-launch-techrehearsal.html"}]),
         now, now),
    )
    con.commit()
    con.close()


def test_targeting_matcher_helpers():
    from app.routers.promo import _version_in_range, _locale_matches, _device_family_matches, _within_days
    from datetime import datetime, timezone, timedelta
    assert _version_in_range("1.5.0", {"min": "1.4.0", "max": None})
    assert not _version_in_range("1.3.0", {"min": "1.4.0"})
    assert not _version_in_range("2.0.0", {"max": "1.9.0"})
    assert not _version_in_range(None, {"min": "1.0.0"})
    assert _locale_matches("en_US", ["en"]) and _locale_matches("en_US", ["en_US"])
    assert not _locale_matches("fr_FR", ["en"]) and not _locale_matches(None, ["en"])
    assert _device_family_matches("iPhone 16 Pro Max", ["iPhone16"])
    assert not _device_family_matches("iPhone 15 Pro", ["iPhone16"])
    now = datetime.now(timezone.utc)
    assert _within_days(now.isoformat(), 7)
    assert not _within_days((now - timedelta(days=30)).isoformat(), 7)


def test_targeting_locale_via_resolve(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-loc", app_locale="en_US", app_version="1.5.0")
    _make_campaign(client, cid="t_en", targeting={"locales": ["en"]})
    h = {"X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=dev-loc", headers=h).json()["campaign_id"] == "t_en"
    # a device we have no telemetry for can't satisfy a locale constraint
    assert client.get("/v1/promo/resolve?device_id=dev-unknown", headers=h).json() == {}


def test_targeting_locale_prefers_header_over_system_locale(client, tmp_db_path):
    """SS sends the app's RUNNING language on resolve (their 2026-07-14
    fix). Eligibility must read the same signal as content localization:
    a system-en device running the UI in es matches an es-targeted
    campaign via the header; with no header the system-locale fallback
    keeps today's behavior."""
    h = {"X-App-ID": SS}
    _insert_telemetry(tmp_db_path, "dev-es-ui", app_locale="en_US", app_version="1.5.0")
    _make_campaign(client, cid="t_es", targeting={"locales": ["es"]})
    r = client.get("/v1/promo/resolve?device_id=dev-es-ui",
                   headers={**h, "Accept-Language": "es-MX"}).json()
    assert r.get("campaign_id") == "t_es"
    # no header -> telemetry system locale (en) does not match the es campaign
    assert client.get("/v1/promo/resolve?device_id=dev-es-ui", headers=h).json() == {}


def test_explicit_en_header_beats_spanish_system_locale(client, pro_user, tmp_db_path):
    """The promo parser keeps an explicit "en" (the config-file parser
    maps en -> None): an English UI on a Spanish-system device gets the
    authored default copy, never the system locale's. Header absent keeps
    the telemetry fallback."""
    _insert_telemetry(tmp_db_path, "dev-en-ui", app_locale="es_ES", app_version="1.5.0")
    _make_campaign(client, cid="loc_en", variants=[dict(_NATIVE_ES)])
    h = {**pro_user["headers"], "X-App-ID": SS}
    r = client.get("/v1/promo/resolve?device_id=dev-en-ui",
                   headers={**h, "Accept-Language": "en-US"}).json()
    assert r["variant"]["native"]["title"] == "Hey there,"
    r2 = client.get("/v1/promo/resolve?device_id=dev-en-ui", headers=h).json()
    assert r2["variant"]["native"]["title"] == "Hola,"


def test_targeting_app_version_via_resolve(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-new", app_locale="en", app_version="1.5.0")
    _make_campaign(client, cid="t_newbuilds", targeting={"app_version": {"min": "1.4.0"}})
    assert client.get("/v1/promo/resolve?device_id=dev-new", headers={"X-App-ID": SS}).json()["campaign_id"] == "t_newbuilds"


def test_targeting_app_version_excludes_old_build(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-old", app_locale="en", app_version="1.3.0")
    _make_campaign(client, cid="t_min140", targeting={"app_version": {"min": "1.4.0"}})
    assert client.get("/v1/promo/resolve?device_id=dev-old", headers={"X-App-ID": SS}).json() == {}


def test_targeting_usage_band_and_recency_via_resolve(client, tmp_db_path):
    dev = "dev-power"
    for _ in range(3):
        _insert_telemetry(tmp_db_path, dev, event_type="meeting_start", app_locale="en", app_version="1.5.0")
    _insert_telemetry(tmp_db_path, dev, event_type="app_start", app_locale="en", app_version="1.5.0")
    _make_campaign(client, cid="t_power", targeting={"meetings_recorded": {"min": 3}, "active_within_days": 7})
    assert client.get(f"/v1/promo/resolve?device_id={dev}", headers={"X-App-ID": SS}).json()["campaign_id"] == "t_power"


def test_targeting_usage_band_excludes_light_user(client, tmp_db_path):
    dev = "dev-light"
    _insert_telemetry(tmp_db_path, dev, event_type="meeting_start", app_locale="en", app_version="1.5.0")
    _make_campaign(client, cid="t_min5", targeting={"meetings_recorded": {"min": 5}})
    assert client.get(f"/v1/promo/resolve?device_id={dev}", headers={"X-App-ID": SS}).json() == {}


# --- Geo targeting (country / region / city) + the ENFORCED min-audience floor.
# #318 §9 (2026-07-08): every geo-targeted campaign carries a floor of
# GEO_MIN_AUDIENCE_FLOOR (25) devices; targeting.min_audience can only raise
# it. Enforced at activation (admin CRUD) and again at resolve.

FLOOR = 25  # mirrors app.routers.promo.GEO_MIN_AUDIENCE_FLOOR


def test_geo_floor_constant_is_25():
    from app.routers.promo import GEO_MIN_AUDIENCE_FLOOR
    assert GEO_MIN_AUDIENCE_FLOOR == FLOOR == 25


def test_geo_country_targeting_via_resolve(client, tmp_db_path):
    _seed_geo(tmp_db_path, FLOOR, "us", country="US", region="California")
    _make_campaign(client, cid="t_us", targeting={"geo": {"countries": ["US"]}})
    h = {"X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=us-0", headers=h).json()["campaign_id"] == "t_us"
    # a device we have no geo for can't satisfy a geo constraint
    assert client.get("/v1/promo/resolve?device_id=dev-nogeo", headers=h).json() == {}


def test_geo_country_excludes_other_country(client, tmp_db_path):
    _seed_geo(tmp_db_path, FLOOR, "us", country="US", region="California")
    _insert_telemetry(tmp_db_path, "dev-gb", country="GB", region="England")
    _make_campaign(client, cid="t_usonly", targeting={"geo": {"countries": ["US"]}})
    assert client.get("/v1/promo/resolve?device_id=dev-gb", headers={"X-App-ID": SS}).json() == {}


def test_geo_region_targeting_via_resolve(client, tmp_db_path):
    _seed_geo(tmp_db_path, FLOOR, "ca", country="US", region="California")
    _insert_telemetry(tmp_db_path, "dev-tx", country="US", region="Texas")
    _make_campaign(client, cid="t_ca", targeting={"geo": {"countries": ["US"], "regions": ["California"]}})
    h = {"X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=ca-0", headers=h).json()["campaign_id"] == "t_ca"
    # right country, wrong region -> excluded (country AND region)
    assert client.get("/v1/promo/resolve?device_id=dev-tx", headers=h).json() == {}


def test_geo_city_targeting_case_insensitive(client, tmp_db_path):
    _seed_geo(tmp_db_path, FLOOR, "sf", country="US", region="California", city="San Francisco")
    _insert_telemetry(tmp_db_path, "dev-la", country="US", region="California", city="Los Angeles")
    _make_campaign(client, cid="t_sf", targeting={"geo": {"cities": ["san francisco"]}})
    h = {"X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=sf-0", headers=h).json()["campaign_id"] == "t_sf"
    # same region, wrong city -> excluded; no city on record -> excluded
    assert client.get("/v1/promo/resolve?device_id=dev-la", headers=h).json() == {}
    assert client.get("/v1/promo/resolve?device_id=dev-nogeo", headers=h).json() == {}


def test_geo_floor_withholds_small_segment_at_resolve(client, tmp_db_path):
    # bypass the CRUD (raw insert) to prove resolve enforces the floor on its
    # own: FLOOR-1 devices in the segment -> withheld even with no
    # min_audience set (the 25 floor is not opt-in).
    _seed_geo(tmp_db_path, FLOOR - 1, "wy", country="US", region="Wyoming")
    _insert_campaign_raw(tmp_db_path, "t_floor", {"geo": {"regions": ["Wyoming"]}})
    assert client.get("/v1/promo/resolve?device_id=wy-0", headers={"X-App-ID": SS}).json() == {}


def test_geo_floor_met_serves(client, tmp_db_path):
    _seed_geo(tmp_db_path, FLOOR, "oh", country="US", region="Ohio")
    _make_campaign(client, cid="t_ok", targeting={"geo": {"regions": ["Ohio"]}})
    assert client.get("/v1/promo/resolve?device_id=oh-0", headers={"X-App-ID": SS}).json()["campaign_id"] == "t_ok"


def test_geo_min_audience_can_only_raise_the_floor(client, tmp_db_path):
    # segment of 30; min_audience 40 raises the floor above the segment ->
    # withheld. min_audience below 25 would be ignored (floor stays 25).
    _seed_geo(tmp_db_path, 30, "nv", country="US", region="Nevada")
    _insert_campaign_raw(tmp_db_path, "t_raise", {"geo": {"regions": ["Nevada"]}, "min_audience": 40})
    assert client.get("/v1/promo/resolve?device_id=nv-0", headers={"X-App-ID": SS}).json() == {}
    from app.routers.promo import _geo_floor
    assert _geo_floor({"min_audience": 3}) == FLOOR   # can't lower
    assert _geo_floor({"min_audience": 40}) == 40     # can raise
    assert _geo_floor({}) == FLOOR


def test_geo_activation_blocked_below_floor(client, tmp_db_path):
    # authoring-time enforcement: activating a geo campaign whose segment is
    # below the floor is a 400 with the audience count in the message...
    _seed_geo(tmp_db_path, 3, "mt", country="US", region="Montana")
    body = {
        "id": "t_block", "name": "t_block", "app_id": SS, "status": "active", "priority": 10,
        "targeting": {"geo": {"regions": ["Montana"]}}, "frequency": {},
        "placements": [{"placement": "launch", "priority": 10}],
        "variants": [{"variant_id": "html", "weight": 100, "render": "html",
                      "html_url": "https://api.ghostpour.com/v1/promo/assets/ss-launch-techrehearsal.html"}],
    }
    r = client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    assert r.status_code == 400
    assert "3 devices" in r.json()["detail"] and "25" in r.json()["detail"]
    # ...but the same campaign saves fine as draft (author ahead of growth)
    draft = {**body, "id": "t_draft", "name": "t_draft", "status": "draft"}
    assert client.post("/webhooks/admin/campaigns", json=draft, headers=ADMIN).status_code == 200
    # flipping the draft to active re-runs the check
    r = client.put("/webhooks/admin/campaign/t_draft", json={**draft, "status": "active"}, headers=ADMIN)
    assert r.status_code == 400
    # non-geo campaigns are unaffected by the floor at activation
    nogeo = {**body, "id": "t_nogeo", "name": "t_nogeo", "targeting": {}}
    assert client.post("/webhooks/admin/campaigns", json=nogeo, headers=ADMIN).status_code == 200


# --- Native render slice: schema validation + min_app_version capability gate ---

def _native_variant(vid, *, weight, min_app_version=None, title="Hi"):
    v = {"variant_id": vid, "weight": weight, "render": "native",
         "native": {"schema_version": 1, "title": title}}
    if min_app_version:
        v["min_app_version"] = min_app_version
    return v


def test_native_schema_validation(client):
    def _post(cid, variants, status="draft"):
        body = {"id": cid, "name": cid, "app_id": SS, "status": status, "priority": 10,
                "targeting": {}, "frequency": {}, "placements": [], "variants": variants}
        return client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    # missing native block on a native variant
    assert _post("n_bad1", [{"variant_id": "a", "weight": 100, "render": "native"}]).status_code == 400
    # wrong schema_version
    assert _post("n_bad2", [{"variant_id": "a", "weight": 100, "render": "native", "native": {"schema_version": 2, "title": "x"}}]).status_code == 400
    # missing title
    assert _post("n_bad3", [{"variant_id": "a", "weight": 100, "render": "native", "native": {"schema_version": 1}}]).status_code == 400
    # non-https media
    assert _post("n_bad4", [{"variant_id": "a", "weight": 100, "render": "native", "native": {"schema_version": 1, "title": "x", "media": {"type": "image", "url": "http://x/y.png"}}}]).status_code == 400
    # bad min_app_version
    assert _post("n_bad5", [{**_native_variant("a", weight=100), "min_app_version": "1.x"}]).status_code == 400
    # valid native + media + gate
    ok = [{"variant_id": "a", "weight": 100, "render": "native", "min_app_version": "1.6.0",
           "native": {"schema_version": 1, "title": "Hi", "body": "there", "media": {"type": "image", "url": "https://cdn/x.png"}}}]
    assert _post("n_good", ok).status_code == 200


def test_capability_gate_withholds_below_min_version(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-old", app_version="1.5.0")
    _insert_telemetry(tmp_db_path, "dev-new", app_version="1.6.0")
    # native variant gated at 1.6.0, plus an ungated html fallback
    variants = [
        _native_variant("native", weight=100, min_app_version="1.6.0"),
        {"variant_id": "fallback", "weight": 0, "render": "html",
         "html_url": "https://api.ghostpour.com/v1/promo/assets/ss-launch-techrehearsal.html"},
    ]
    _make_campaign(client, cid="cap_gate", variants=variants)
    h = {"X-App-ID": SS}
    # capable build gets the native variant
    assert client.get("/v1/promo/resolve?device_id=dev-new", headers=h).json()["variant"]["variant_id"] == "native"
    # below-min build falls through to the ungated fallback
    assert client.get("/v1/promo/resolve?device_id=dev-old", headers=h).json()["variant"]["variant_id"] == "fallback"
    # unknown app_version (no telemetry) is fail-closed -> also the fallback
    assert client.get("/v1/promo/resolve?device_id=dev-unknown", headers=h).json()["variant"]["variant_id"] == "fallback"


def test_capability_gate_all_gated_yields_nothing(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-old2", app_version="1.5.0")
    _make_campaign(client, cid="all_gated", variants=[_native_variant("native", weight=100, min_app_version="1.6.0")])
    # the only variant is gated above this build -> campaign yields nothing
    assert client.get("/v1/promo/resolve?device_id=dev-old2", headers={"X-App-ID": SS}).json() == {}


def test_geo_targeting_validation_rejects_bad_shapes(client):
    # min_audience must be a non-negative int; geo must be an object with list members
    def _post(cid, targeting):
        body = {
            "id": cid, "name": cid, "app_id": SS, "status": "draft", "priority": 10,
            "targeting": targeting, "frequency": {}, "placements": [], "variants": [],
        }
        return client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    assert _post("t_bad1", {"min_audience": -1}).status_code == 400
    assert _post("t_bad2", {"geo": {"countries": "US"}}).status_code == 400
    assert _post("t_bad3", {"geo": []}).status_code == 400
    # valid geo + floor accepted
    assert _post("t_badcity", {"geo": {"cities": "San Francisco"}}).status_code == 400
    assert _post("t_good", {"geo": {"countries": ["US"]}, "min_audience": 50}).status_code == 200


# --- per-locale content (content_locales) ---

_NATIVE_ES = {
    "variant_id": "native", "weight": 100, "render": "native",
    "native": {
        "schema_version": 1,
        "title": "Hey there,",
        "body": "Native card rendered by ShoulderSurf.",
        "ctas": [{"cta_id": "open", "label": "Open ShoulderSurf",
                  "action": {"type": "none"}}],
    },
    "content_locales": {
        "es": {"title": "Hola,",
               "body": "Tarjeta nativa de ShoulderSurf.",
               "ctas": [{"cta_id": "open", "label": "Abrir ShoulderSurf",
                         "action": {"type": "none"}}]},
        "ja": {"title": "こんにちは"},
    },
}


def test_resolve_localizes_by_accept_language(client, pro_user):
    _make_campaign(client, cid="loc1", variants=[dict(_NATIVE_ES)])
    h = {**pro_user["headers"], "X-App-ID": SS}

    es = client.get("/v1/promo/resolve?device_id=devL",
                    headers={**h, "Accept-Language": "es-MX"}).json()
    assert es["variant"]["native"]["title"] == "Hola,"
    assert es["variant"]["native"]["ctas"][0]["label"] == "Abrir ShoulderSurf"
    assert "content_locales" not in es["variant"]          # authoring-side only

    # partial override: ja replaces title, keeps base body/ctas
    ja = client.get("/v1/promo/resolve?device_id=devL",
                    headers={**h, "Accept-Language": "ja"}).json()
    assert ja["variant"]["native"]["title"] == "こんにちは"
    assert ja["variant"]["native"]["body"] == "Native card rendered by ShoulderSurf."

    # en / unknown -> authored default
    en = client.get("/v1/promo/resolve?device_id=devL",
                    headers={**h, "Accept-Language": "en-US"}).json()
    assert en["variant"]["native"]["title"] == "Hey there,"
    none = client.get("/v1/promo/resolve?device_id=devL", headers=h).json()
    assert none["variant"]["native"]["title"] == "Hey there,"


def test_campaign_validation_rejects_bad_content_locales(client):
    import copy
    bad = copy.deepcopy(_NATIVE_ES)
    bad["content_locales"] = {"es": "not an object"}
    body = {
        "id": "locbad", "name": "locbad", "app_id": SS, "status": "active",
        "priority": 1, "targeting": {}, "frequency": {},
        "placements": [{"placement": "launch", "priority": 1}],
        "variants": [bad],
    }
    r = client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    assert r.status_code == 400
    # locale override CTA with unknown action type is rejected like base CTAs
    bad2 = copy.deepcopy(_NATIVE_ES)
    bad2["content_locales"]["es"]["ctas"] = [
        {"cta_id": "x", "label": "X", "action": {"type": "rm_rf"}}]
    body["variants"] = [bad2]
    r = client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    assert r.status_code == 400
