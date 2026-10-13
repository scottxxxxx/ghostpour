"""The "Other forms" section on N-400 Helper's Home screen (Scott via the
auditor, 2026-10-13).

Whether the section shows, and what it offers, come from GP. It ships
PAUSED: the client resolves placement n400_home_forms at launch and shows no
section on {}, which is today's app. Scott flips it active from the
dashboard once every card links somewhere real (App Store guideline 2.1).
Active, it is always on (no cap, never suppressed), render html with one
https page per language, the placement echoed, and one link per card with
its own promo_cta_id.
"""
from __future__ import annotations

import re
from pathlib import Path

ADMIN = {"X-Admin-Key": "test-admin-key"}
CID = "n400_home_forms"
PLACEMENT = "n400_home_forms"
DEV = "6c3d4e5f-6071-4829-8b3c-4d5e6f7a8b9c"
BASE = "https://cz.shouldersurf.com/v1/promo/assets/"
PAGES = ("n400-home-forms.html", "n400-home-forms.es.html", "n400-home-forms.pt.html")


def _resolve(client, lang="en-US", app="n400", placement=PLACEMENT, device_id=DEV) -> dict:
    r = client.get(f"/v1/promo/resolve?device_id={device_id}&placement={placement}",
                   headers={"X-App-ID": app, "Accept-Language": lang})
    assert r.status_code == 200, r.text
    return r.json()


def _activate(client) -> None:
    """The dashboard's Save: GET the campaign, PUT it back active."""
    c = client.get(f"/webhooks/admin/campaign/{CID}", headers=ADMIN).json()
    body = {k: c[k] for k in ("id", "name", "app_id", "status", "starts_at", "expires_at",
                              "priority", "mutual_exclusion_group", "targeting", "frequency",
                              "placements", "variants")}
    body["status"] = "active"
    r = client.put(f"/webhooks/admin/campaign/{CID}", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text


def test_ships_paused_so_the_app_shows_no_section(client):
    c = client.get(f"/webhooks/admin/campaign/{CID}", headers=ADMIN).json()
    assert (c["status"], c["app_id"]) == ("paused", "n400")
    assert _resolve(client) == {}


def test_active_serves_the_section_with_the_placement_echoed(client):
    _activate(client)
    out = _resolve(client)
    assert (out["campaign_id"], out["placement"]) == (CID, PLACEMENT)
    assert (out["variant"]["render"], out["variant"]["html_url"]) == ("html", BASE + PAGES[0])
    assert _resolve(client, "es-MX")["variant"]["html_url"] == BASE + PAGES[1]
    assert _resolve(client, "pt-BR")["variant"]["html_url"] == BASE + PAGES[2]
    assert _resolve(client, app="shouldersurf") == {}


def test_active_stays_on_through_clicks_and_a_convert(client):
    _activate(client)
    for n in range(5):
        assert _resolve(client).get("campaign_id") == CID, f"launch {n + 1}"
        ev = {"device_id": DEV, "campaign_id": CID, "variant_id": "section"}
        for extra in ({"event_type": "impression", "surface": PLACEMENT},
                      {"event_type": "click", "cta_id": "home_i765"},
                      {"event_type": "convert"}):
            r = client.post("/v1/promo/events", headers={"X-App-ID": "n400"}, json={**ev, **extra})
            assert r.status_code == 204, r.text


def test_every_page_has_three_cards_each_with_its_own_cta(client):
    for name in PAGES:
        r = client.get(f"/v1/promo/assets/{name}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), name
        html = r.text
        for cta in ("home_i765", "home_i90", "home_i130"):
            assert html.count(f'href="https://n400helper.com/?promo_cta_id={cta}"') == 1, (name, cta)
        assert "overflow-x: auto" in html and "background: transparent" in html
        assert "prefers-color-scheme: dark" in html and "padding: 0 20px" in html
        assert "<script" not in html and "@import" not in html
        visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        assert "—" not in visible and "–" not in visible, name


def test_copy_per_language():
    root = Path(__file__).resolve().parent.parent / "app" / "static" / "promo"
    en, es, pt = ((root / p).read_text() for p in PAGES)
    assert "Other forms" in en and "Coming soon" in en and "Replace your green card" in en
    assert "Otros formularios" in es and "Reemplazo de su green card" in es
    assert "Outros formulários" in pt and "Substituição do green card" in pt
