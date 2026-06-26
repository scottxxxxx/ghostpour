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
