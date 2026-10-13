"""The Share N-400 Helper card on N-400 Helper's Form screen (Scott via the
auditor, 2026-10-13).

Scott wants the card's copy and link served as HTML so the share target can
move from https://n400helper.com to the App Store listing, and later carry
links to other apps, without a release. The client resolves placement
n400_share once at launch and shows the built-in card when GP answers {}.
The contract: always on (no cap, never suppressed), render html with an https
html_url per language, the placement echoed, and a share link carrying
promo_cta_id and promo_action=share.
"""
from __future__ import annotations

import re
from pathlib import Path

ADMIN = {"X-Admin-Key": "test-admin-key"}
CID = "n400_share_card"
PLACEMENT = "n400_share"
DEV = "5b2c3d4e-5f60-4718-9a2b-3c4d5e6f7a8c"
BASE = "https://cz.shouldersurf.com/v1/promo/assets/"
_PROMO = Path(__file__).resolve().parent.parent / "app" / "static" / "promo"
SHARE_HREF = "https://n400helper.com/?promo_cta_id=share_n400&amp;promo_action=share"


def _resolve(client, lang="en-US", app="n400", placement=PLACEMENT, device_id=DEV) -> dict:
    r = client.get(f"/v1/promo/resolve?device_id={device_id}&placement={placement}",
                   headers={"X-App-ID": app, "Accept-Language": lang})
    assert r.status_code == 200, r.text
    return r.json()


def test_resolve_serves_the_card_with_the_placement_echoed(client):
    out = _resolve(client)
    assert out["campaign_id"] == CID
    assert out["placement"] == PLACEMENT
    v = out["variant"]
    assert (v["render"], v["html_url"]) == ("html", BASE + "n400-share.html")
    assert "content_locales" not in v


def test_language_follows_accept_language(client):
    assert _resolve(client, "es-US")["variant"]["html_url"] == BASE + "n400-share.es.html"
    assert _resolve(client, "pt-BR")["variant"]["html_url"] == BASE + "n400-share.pt.html"
    assert _resolve(client, "fr-FR")["variant"]["html_url"] == BASE + "n400-share.html"


def test_only_n400_at_its_own_moment(client):
    assert _resolve(client, app="shouldersurf") == {}
    assert _resolve(client, app="i765") == {}
    assert _resolve(client, placement="n400_complete").get("campaign_id") != CID


def test_always_on_through_impressions_clicks_and_a_convert(client):
    for n in range(6):
        out = _resolve(client)
        assert out.get("campaign_id") == CID, f"launch {n + 1}"
        ev = {"device_id": DEV, "campaign_id": CID, "variant_id": "card"}
        for extra in ({"event_type": "impression", "surface": PLACEMENT},
                      {"event_type": "click", "cta_id": "share_n400"},
                      {"event_type": "convert"}):
            r = client.post("/v1/promo/events", headers={"X-App-ID": "n400"}, json={**ev, **extra})
            assert r.status_code == 204, r.text


def test_every_page_is_served_and_carries_the_share_link(client):
    for name in ("n400-share.html", "n400-share.es.html", "n400-share.pt.html"):
        r = client.get(f"/v1/promo/assets/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith("text/html")
        html = r.text
        assert html.count(SHARE_HREF) == 1, name
        assert "background: transparent" in html
        assert "prefers-color-scheme: dark" in html
        assert "<script" not in html and "@import" not in html and "fonts." not in html
        visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        assert "—" not in visible and "–" not in visible, name


def test_copy_per_language(client):
    en = (_PROMO / "n400-share.html").read_text()
    es = (_PROMO / "n400-share.es.html").read_text()
    pt = (_PROMO / "n400-share.pt.html").read_text()
    assert "Know someone preparing for citizenship?" in en and "Share N-400 Helper" in en
    assert "Usted ya hizo la parte difícil" in es and "Envíele la app" in es
    assert "Compartilhar o N-400 Helper" in pt


def test_the_dashboard_can_save_and_pause_the_live_campaign(client):
    """#1092 seeded this campaign at boot, but its placement was not in the
    authoring vocabulary, so the dashboard's Save answered 400 and Scott
    could not pause or edit a live campaign. Found 2026-10-13 by the Other
    forms section's activation test hitting the same wall."""
    c = client.get(f"/webhooks/admin/campaign/{CID}", headers=ADMIN).json()
    body = {k: c[k] for k in ("id", "name", "app_id", "status", "starts_at", "expires_at",
                              "priority", "mutual_exclusion_group", "targeting", "frequency",
                              "placements", "variants")}
    body["status"] = "paused"
    r = client.put(f"/webhooks/admin/campaign/{CID}", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert _resolve(client) == {}
