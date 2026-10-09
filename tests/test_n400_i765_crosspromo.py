"""The I-765 Helper card inside N-400 Helper (Scott, 2026-10-09).

Scott's question, verbatim: "We need a way to control when it appears."
The control is the campaign itself. N-400 Helper resolves once when the
applicant's N-400 is built complete (placement n400_complete, X-App-ID
n400). GP answers with the card only while the campaign
`n400_i765_crosspromo` is active and inside its date window, and only as
many times per device as its frequency cap allows. The campaign ships as a
DRAFT seeded at boot, so the N-400 app is dark until Scott flips it from
the Campaigns tab. Flipping it active is the "I-765 is ready" flag.

The N-400 client (build 154) was built to these exact shapes: it shows the
card only for render html with an https html_url and the placement echoed
back, reads promo_cta_id off every tapped href, and sends impression, click
(cta_id) and dismiss (visible_ms) to /v1/promo/events.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database

ADMIN = {"X-Admin-Key": "test-admin-key"}
N400 = {"X-App-ID": "n400", "Accept-Language": "en-US"}
SS = {"X-App-ID": "shouldersurf"}
CID = "n400_i765_crosspromo"
PLACEMENT = "n400_complete"
ASSET = "n400-i765-complete.html"
DEV = "7a1c2d3e-4f50-4617-8a2b-3c4d5e6f7a8b"

_ROOT = Path(__file__).resolve().parent.parent


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _campaign(client) -> dict:
    r = client.get(f"/webhooks/admin/campaign/{CID}", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _set(client, **over) -> dict:
    """PUT the seeded campaign back with some fields changed, the way the
    dashboard's Save does."""
    c = _campaign(client)
    body = {k: c[k] for k in ("id", "name", "app_id", "status", "starts_at", "expires_at",
                              "priority", "mutual_exclusion_group", "targeting", "frequency",
                              "placements", "variants")}
    body.update(over)
    r = client.put(f"/webhooks/admin/campaign/{CID}", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return _campaign(client)


def _resolve(client, headers=N400, placement=PLACEMENT, device_id=DEV) -> dict:
    q = f"device_id={device_id}" + (f"&placement={placement}" if placement else "")
    r = client.get(f"/v1/promo/resolve?{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- the flag ships OFF -----------------------------------------------------


def test_the_campaign_is_seeded_as_a_draft_and_the_app_is_dark(client):
    c = _campaign(client)
    assert (c["status"], c["app_id"], c["priority"]) == ("draft", "n400", 10)
    assert c["placements"] == [{"placement": PLACEMENT, "priority": 10}]
    assert c["frequency"] == {"max_impressions": 3}
    assert c["targeting"] == {}
    (v,) = c["variants"]
    assert (v["variant_id"], v["weight"], v["render"]) == ("card", 100, "html")
    assert v["html_url"] == f"https://cz.shouldersurf.com/v1/promo/assets/{ASSET}"
    assert v["content_locales"] == {
        "es": {"html_url": f"https://cz.shouldersurf.com/v1/promo/assets/{ASSET}?lang=es"},
        "pt": {"html_url": f"https://cz.shouldersurf.com/v1/promo/assets/{ASSET}?lang=pt"},
    }
    # The seed points at a creative that actually ships in the image.
    from app.services import promo_assets
    assert promo_assets.resolve_path(ASSET) is not None
    assert _resolve(client) == {}


def test_a_boot_never_overwrites_a_dashboard_edit(client, tmp_db_path):
    """INSERT OR IGNORE: the seed is a default, not a reset. Run the seed
    statement again over an edited row and the edit must survive."""
    assert _set(client, status="active", name="edited")["status"] == "active"
    (seed,) = [m for m in database.MIGRATIONS if isinstance(m, str) and CID in m]
    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.execute(seed)
        conn.commit()
        row = conn.execute("SELECT status, name FROM promo_campaigns WHERE id = ?", (CID,)).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM promo_campaigns WHERE app_id = 'n400'").fetchone() == (1,)
    finally:
        conn.close()
    assert row == ("active", "edited")


# --- how Scott controls when it appears -----------------------------------


def test_flipping_the_campaign_active_is_the_ready_flag(client):
    _set(client, status="active")
    out = _resolve(client)
    assert out["campaign_id"] == CID
    assert out["placement"] == PLACEMENT, "the N-400 client requires this echo"
    v = out["variant"]
    assert v["render"] == "html" and v["html_url"].startswith("https://")
    assert v["variant_id"] == "card"
    # And every other status turns it off again, no deploy involved.
    for status in ("paused", "archived", "draft"):
        _set(client, status=status)
        assert _resolve(client) == {}, status


def test_the_date_window_also_controls_it(client):
    _set(client, status="active", starts_at=_iso(days=1), expires_at=None)
    assert _resolve(client) == {}, "not yet started"
    _set(client, status="active", starts_at=None, expires_at=_iso(days=-1))
    assert _resolve(client) == {}, "already expired"
    _set(client, status="active", starts_at=_iso(days=-1), expires_at=_iso(days=1))
    assert _resolve(client)["campaign_id"] == CID


def test_the_language_follows_the_apps_accept_language(client):
    """The N-400 app sends Accept-Language as its EFFECTIVE language (en, es or
    pt-BR), which can differ from the device locale the webview would read.
    GP swaps the page per language so the app's choice wins; the page honors
    ?lang= before it looks at the device."""
    _set(client, status="active")
    base = f"https://cz.shouldersurf.com/v1/promo/assets/{ASSET}"
    for header, want in (("en-US", base), ("es-MX", f"{base}?lang=es"),
                         ("pt-BR", f"{base}?lang=pt"), ("fr-FR", base)):
        out = _resolve(client, headers={"X-App-ID": "n400", "Accept-Language": header})
        assert out["variant"]["html_url"] == want, header
        assert "content_locales" not in out["variant"], "authoring-side map stays off the wire"
    served = client.get(f"/v1/promo/assets/{ASSET}?lang=es").text
    assert "lang=(es|pt|en)" in served, "the page reads the param GP appended"
    # The override is validated like the rest of the variant: https only.
    c = _campaign(client)
    c["variants"][0]["content_locales"]["es"]["html_url"] = "http://cz.shouldersurf.com/x.html"
    r = client.put(f"/webhooks/admin/campaign/{CID}", headers=ADMIN,
                   json={k: c[k] for k in ("id", "name", "app_id", "status", "priority",
                                           "targeting", "frequency", "placements", "variants")})
    assert r.status_code == 400 and "html_url" in r.text


def test_the_card_is_for_n400_at_completion_and_nowhere_else(client):
    _set(client, status="active")
    assert _resolve(client, headers=SS) == {}, "another app never sees an n400 campaign"
    assert _resolve(client, placement="launch") == {}, "not on launch"
    assert _resolve(client, placement=None) == {}, "a client that names no moment is asking for launch"
    assert _resolve(client)["campaign_id"] == CID


def test_three_showings_per_device_then_quiet(client):
    """The client sends the events the auditor built to: impression on
    didFinish with the surface, click with the cta_id read off the href,
    dismiss with visible_ms. Three impressions spend the cap."""
    _set(client, status="active")
    for n in range(3):
        out = _resolve(client)
        assert out.get("campaign_id") == CID, f"showing {n + 1}"
        ev = {"device_id": DEV, "campaign_id": CID, "variant_id": out["variant"]["variant_id"]}
        assert client.post("/v1/promo/events", headers=N400, json={
            **ev, "event_type": "impression", "surface": PLACEMENT}).status_code == 204
        assert client.post("/v1/promo/events", headers=N400, json={
            **ev, "event_type": "dismiss", "visible_ms": 4200}).status_code == 204
    assert _resolve(client) == {}, "the fourth completion on this device shows nothing"
    assert _resolve(client, device_id="0f0f0f0f-0000-4000-8000-000000000001")["campaign_id"] == CID
    assert client.post("/v1/promo/events", headers=N400, json={
        "device_id": DEV, "campaign_id": CID, "variant_id": "card",
        "event_type": "click", "cta_id": "get_i765"}).status_code == 204
    rep = client.get(f"/webhooks/admin/campaign/{CID}/report", headers=ADMIN)
    assert rep.status_code == 200, rep.text


# --- the creative -------------------------------------------------------------


def test_the_creative_serves_and_every_link_carries_its_cta_id(client):
    r = client.get(f"/v1/promo/assets/{ASSET}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    assert "I-765 Helper" in html
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert hrefs, "a card with no link is not a CTA"
    for h in hrefs:
        assert h.startswith("https://"), h
        assert "promo_cta_id=" in h, f"the N-400 client attributes a tap by this param: {h}"
    assert set(re.findall(r'data-cta-id="([^"]+)"', html)) == {"get_i765"}
    # Nothing that would hang or leave the webview: no dialogs, no external loads.
    body = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    for bad in ("alert(", "confirm(", "prompt(", "<script src", "<link ", "<img ", "url("):
        assert bad not in body, bad
    # The three languages N-400 Helper ships in, English on by default.
    assert re.search(r'lang-copy="en" class="on"', body)
    assert 'lang-copy="es"' in body and 'lang-copy="pt"' in body


def test_the_campaign_dashboard_offers_both_new_apps():
    """The App dropdown is hardcoded markup, so this is the only place the
    two new ids can be checked without a browser."""
    html = (_ROOT / "app" / "static" / "admin.html").read_text()
    select = re.search(r'<select id="cf-app">(.*?)</select>', html, flags=re.S).group(1)
    assert '<option value="n400">N-400 Helper</option>' in select
    assert '<option value="i765">I-765 Helper</option>' in select
    assert "CAMPAIGN_APPS = ['shouldersurf', 'techrehearsal', 'n400', 'i765']" in html
