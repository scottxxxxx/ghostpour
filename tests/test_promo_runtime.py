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
