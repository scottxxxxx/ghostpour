"""The mirror card: N-400 Helper inside I-765 Helper (2026-10-09, PLAN.md
section 5 item 5). Same mechanism as tests/test_n400_i765_crosspromo.py,
seeded as a draft, dark until Scott flips it once N-400 has a store id."""
from __future__ import annotations

import re

ADMIN = {"X-Admin-Key": "test-admin-key"}
I765 = {"X-App-ID": "i765", "Accept-Language": "en-US"}
CID = "i765_n400_crosspromo"
ASSET = "i765-n400-complete.html"
DEV = "9b2c3d4e-5f60-4718-9b3c-4d5e6f7a8b9c"


def _campaign(client):
    r = client.get(f"/webhooks/admin/campaign/{CID}", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _resolve(client, headers=I765, placement="i765_complete"):
    r = client.get(f"/v1/promo/resolve?device_id={DEV}&placement={placement}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_mirror_campaign_is_seeded_dark_for_i765_at_completion(client):
    c = _campaign(client)
    assert (c["status"], c["app_id"]) == ("draft", "i765")
    assert c["placements"] == [{"placement": "i765_complete", "priority": 10}]
    (v,) = c["variants"]
    assert v["render"] == "html" and v["html_url"].endswith(f"/v1/promo/assets/{ASSET}")
    assert set(v["content_locales"]) == {"es", "pt"}
    assert _resolve(client) == {}


def test_flipping_it_active_shows_the_card_to_i765_only(client):
    c = _campaign(client)
    body = {k: c[k] for k in ("id", "name", "app_id", "status", "priority", "targeting",
                              "frequency", "placements", "variants")}
    body["status"] = "active"
    assert client.put(f"/webhooks/admin/campaign/{CID}", json=body, headers=ADMIN).status_code == 200
    out = _resolve(client)
    assert out["campaign_id"] == CID and out["placement"] == "i765_complete"
    assert _resolve(client, headers={"X-App-ID": "n400"}) == {}, "the N-400 app never sees the I-765 campaign"
    assert _resolve(client, headers={"X-App-ID": "n400"}, placement="n400_complete") == {}
    assert _resolve(client, placement="n400_complete") == {}, "and not at the other app's moment"
    es = _resolve(client, headers={"X-App-ID": "i765", "Accept-Language": "es-ES"})
    assert es["variant"]["html_url"].endswith("?lang=es")


def test_the_creative_serves_with_every_link_attributed_to_get_n400(client):
    r = client.get(f"/v1/promo/assets/{ASSET}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "N-400 Helper" in html and "Get N-400 Helper" in html
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert hrefs and all(h.startswith("https://") and "promo_cta_id=get_n400" in h for h in hrefs), hrefs
    assert set(re.findall(r'data-cta-id="([^"]+)"', html)) == {"get_n400"}
    body = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    for bad in ("alert(", "confirm(", "prompt(", "<script src", "<link ", "<img "):
        assert bad not in body, bad
    assert chr(0x2014) not in body and chr(0x2013) not in body
