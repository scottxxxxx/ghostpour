"""I-765 Helper's share card and "Other forms" section (the I-765 lead,
2026-10-18), mirrored from N-400's. BOTH ship PAUSED: there is no I-765
website or App Store id yet, so the share link is the id0000000000 placeholder
the cross-promo cards carry, and Scott turns each on from the dashboard once
its link is real (App Store guideline 2.1). Paused answers {} on resolve,
which the client reads as "no card".
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ADMIN = {"X-Admin-Key": "test-admin-key"}
DEV = "7d4e5f60-7182-4930-9c4d-5e6f7a8b9c0d"
BASE = "https://cz.shouldersurf.com/v1/promo/assets/"
_PROMO = Path(__file__).resolve().parent.parent / "app" / "static" / "promo"
CAMPAIGNS = {
    "i765_share_card": ("i765_share", "i765-share"),
    "i765_home_forms": ("i765_home_forms", "i765-home-forms"),
}


def _resolve(client, placement, lang="en-US", app="i765") -> dict:
    r = client.get(f"/v1/promo/resolve?device_id={DEV}&placement={placement}",
                   headers={"X-App-ID": app, "Accept-Language": lang})
    assert r.status_code == 200, r.text
    return r.json()


def _save(client, cid, status):
    """The dashboard's Save, which 400s on a placement outside the vocabulary."""
    c = client.get(f"/webhooks/admin/campaign/{cid}", headers=ADMIN).json()
    body = {k: c[k] for k in ("id", "name", "app_id", "status", "starts_at", "expires_at",
                              "priority", "mutual_exclusion_group", "targeting", "frequency",
                              "placements", "variants")}
    body["status"] = status
    r = client.put(f"/webhooks/admin/campaign/{cid}", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("cid", sorted(CAMPAIGNS))
def test_ships_paused_and_resolves_nothing(client, cid):
    placement, _ = CAMPAIGNS[cid]
    c = client.get(f"/webhooks/admin/campaign/{cid}", headers=ADMIN).json()
    assert (c["status"], c["app_id"]) == ("paused", "i765")
    assert _resolve(client, placement) == {}


@pytest.mark.parametrize("cid", sorted(CAMPAIGNS))
def test_the_dashboard_can_turn_it_on_and_it_serves_per_language(client, cid):
    placement, stem = CAMPAIGNS[cid]
    _save(client, cid, "active")
    out = _resolve(client, placement)
    assert (out["campaign_id"], out["placement"]) == (cid, placement)
    assert out["variant"]["html_url"] == f"{BASE}{stem}.html"
    assert _resolve(client, placement, "es-MX")["variant"]["html_url"] == f"{BASE}{stem}.es.html"
    assert _resolve(client, placement, "pt-BR")["variant"]["html_url"] == f"{BASE}{stem}.pt.html"
    assert _resolve(client, placement, app="n400") == {}


@pytest.mark.parametrize("name", [f"{s}{l}.html" for s in ("i765-share", "i765-home-forms")
                                  for l in ("", ".es", ".pt")])
def test_every_page_is_served_and_names_no_n400_as_itself(client, name):
    r = client.get(f"/v1/promo/assets/{name}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text.split("<body>")[1]
    assert "—" not in r.text and "–" not in r.text
    if name.startswith("i765-share"):
        assert "N-400" not in body
    else:
        # The section offers OTHER forms: N-400 is one, I-765 itself is not.
        assert "N-400" in body and "I-765" not in body and "home_i765" not in body


_TAILS = {"i765-share.html": ("Here's the app I used for my I-765.", "I answered the whole thing in"),
          "i765-share.es.html": ("Esta es la app que usé para mi I-765.", "Respondí todo en"),
          "i765-share.pt.html": ("Este é o app que usei para o meu I-765.", "Respondi tudo em")}


@pytest.mark.parametrize("name", sorted(_TAILS))
def test_share_href_carries_timed_and_untimed_lines(name):
    head, tail = _TAILS[name]
    raw = (_PROMO / name).read_text()
    href = _html.unescape(re.search(r'<a class="btn" href="([^"]+)"', raw).group(1))
    assert href.startswith("https://apps.apple.com/app/id0000000000?"), "placeholder until I-765 has an id"
    assert "%7Btime_with_us%7D" in href and "{" not in href
    q = parse_qs(urlsplit(href).query)
    assert q["promo_cta_id"] == ["share_i765"] and q["promo_action"] == ["share"]
    (timed,), (untimed,) = q["promo_share_text"], q["promo_share_text_untimed"]
    assert timed.startswith(head) and timed.endswith(tail + " {time_with_us}.")
    assert timed.startswith(untimed) and "{time_with_us}" not in untimed
