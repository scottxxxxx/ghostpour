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
                      country=None, region=None):
    import sqlite3, uuid
    from datetime import datetime, timezone
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO telemetry_events (id, event_type, device_id, received_at, app_locale, app_version, device_model, country, region)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), event_type, device_id, received_at, app_locale, app_version, device_model, country, region),
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


# --- Phase 2: geo targeting (country / region) + min_audience privacy floor ---

def test_geo_country_targeting_via_resolve(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-us", country="US", region="California")
    _make_campaign(client, cid="t_us", targeting={"geo": {"countries": ["US"]}})
    h = {"X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=dev-us", headers=h).json()["campaign_id"] == "t_us"
    # a device we have no geo for can't satisfy a geo constraint
    assert client.get("/v1/promo/resolve?device_id=dev-nogeo", headers=h).json() == {}


def test_geo_country_excludes_other_country(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-gb", country="GB", region="England")
    _make_campaign(client, cid="t_usonly", targeting={"geo": {"countries": ["US"]}})
    assert client.get("/v1/promo/resolve?device_id=dev-gb", headers={"X-App-ID": SS}).json() == {}


def test_geo_region_targeting_via_resolve(client, tmp_db_path):
    _insert_telemetry(tmp_db_path, "dev-ca", country="US", region="California")
    _insert_telemetry(tmp_db_path, "dev-tx", country="US", region="Texas")
    _make_campaign(client, cid="t_ca", targeting={"geo": {"countries": ["US"], "regions": ["California"]}})
    h = {"X-App-ID": SS}
    assert client.get("/v1/promo/resolve?device_id=dev-ca", headers=h).json()["campaign_id"] == "t_ca"
    # right country, wrong region -> excluded (country AND region)
    assert client.get("/v1/promo/resolve?device_id=dev-tx", headers=h).json() == {}


def test_geo_min_audience_withholds_small_segment(client, tmp_db_path):
    # only one device in the targeted geo; floor of 3 withholds it
    _insert_telemetry(tmp_db_path, "dev-solo", country="US", region="Wyoming")
    _make_campaign(client, cid="t_floor", targeting={"geo": {"regions": ["Wyoming"]}, "min_audience": 3})
    assert client.get("/v1/promo/resolve?device_id=dev-solo", headers={"X-App-ID": SS}).json() == {}


def test_geo_min_audience_met_serves(client, tmp_db_path):
    for d in ("g1", "g2", "g3"):
        _insert_telemetry(tmp_db_path, d, country="US", region="Ohio")
    _make_campaign(client, cid="t_ok", targeting={"geo": {"regions": ["Ohio"]}, "min_audience": 3})
    assert client.get("/v1/promo/resolve?device_id=g1", headers={"X-App-ID": SS}).json()["campaign_id"] == "t_ok"


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
    assert _post("t_good", {"geo": {"countries": ["US"]}, "min_audience": 50}).status_code == 200
