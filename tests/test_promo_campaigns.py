"""Promo campaign CRUD (#promo, slice 1).

Foundation for the server-decided promo feature: the dashboard Campaigns tab
manages campaigns through these admin endpoints. GP owns the campaigns
(targeting/frequency/schedule are GP-internal; `variants` carry the SS render
payload). Decision engine, event ingestion, and analytics are later slices.
"""

ADMIN = {"X-Admin-Key": "test-admin-key"}
BASE = "/webhooks/admin"


def _campaign(**over):
    c = {
        "id": "tr_crosspromo_2026_07",
        "name": "Cross-promote TR",
        "app_id": "shouldersurf",
        "status": "draft",
        "priority": 10,
        "targeting": {"locales": ["en"], "tiers": ["free"], "meetings_recorded": {"min": 3}},
        "frequency": {"max_impressions": 3, "min_interval_seconds": 172800},
        "placements": [{"placement": "launch", "priority": 10}],
        "variants": [
            {"variant_id": "A", "weight": 50, "render": "native", "native": {"schema_version": 1, "title": "hi"}},
            {"variant_id": "B", "weight": 50, "render": "html", "html_url": "https://x/y.html"},
        ],
    }
    c.update(over)
    return c


def test_create_list_get_roundtrip(client):
    r = client.post(f"{BASE}/campaigns", json=_campaign(), headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "tr_crosspromo_2026_07"

    lst = client.get(f"{BASE}/campaigns", headers=ADMIN).json()["campaigns"]
    assert any(c["id"] == "tr_crosspromo_2026_07" for c in lst)

    g = client.get(f"{BASE}/campaign/tr_crosspromo_2026_07", headers=ADMIN).json()
    # JSON columns come back as structures, not strings
    assert g["targeting"]["tiers"] == ["free"]
    assert g["targeting"]["meetings_recorded"]["min"] == 3
    assert len(g["variants"]) == 2 and g["variants"][0]["weight"] == 50
    assert g["placements"][0]["placement"] == "launch"


def test_update_preserves_created_at(client):
    client.post(f"{BASE}/campaigns", json=_campaign(id="c1"), headers=ADMIN)
    created = client.get(f"{BASE}/campaign/c1", headers=ADMIN).json()["created_at"]
    r = client.put(f"{BASE}/campaign/c1", json=_campaign(id="c1", name="renamed"), headers=ADMIN)
    assert r.status_code == 200
    after = client.get(f"{BASE}/campaign/c1", headers=ADMIN).json()
    assert after["name"] == "renamed"
    assert after["created_at"] == created           # preserved
    assert after["updated_at"] >= created


def test_delete(client):
    client.post(f"{BASE}/campaigns", json=_campaign(id="c2"), headers=ADMIN)
    assert client.delete(f"{BASE}/campaign/c2", headers=ADMIN).status_code == 200
    assert client.get(f"{BASE}/campaign/c2", headers=ADMIN).status_code == 404


def test_validation_and_conflicts(client):
    # duplicate id
    client.post(f"{BASE}/campaigns", json=_campaign(id="dup"), headers=ADMIN)
    assert client.post(f"{BASE}/campaigns", json=_campaign(id="dup"), headers=ADMIN).status_code == 409
    # bad status
    assert client.post(f"{BASE}/campaigns", json=_campaign(id="bs", status="live"), headers=ADMIN).status_code == 400
    # active campaign whose variant weights don't sum to 100
    bad = _campaign(id="bw", status="active",
                    variants=[{"variant_id": "A", "weight": 30}, {"variant_id": "B", "weight": 30}])
    assert client.post(f"{BASE}/campaigns", json=bad, headers=ADMIN).status_code == 400
    # 404s
    assert client.get(f"{BASE}/campaign/nope", headers=ADMIN).status_code == 404
    assert client.delete(f"{BASE}/campaign/nope", headers=ADMIN).status_code == 404


def _native(*ctas):
    return [{"variant_id": "A", "weight": 100, "render": "native",
             "native": {"schema_version": 1, "title": "hi", "ctas": list(ctas)}}]


def test_cta_action_type_allowlist(client):
    # every locked type is accepted
    ok = _campaign(id="cta_ok", variants=_native(
        {"label": "Get it", "action": {"type": "appstore", "value": "id1"}},
        {"label": "Upgrade", "action": {"type": "paywall"}},
        # storekit_offer must now say which pool it draws from, or admit it
        # is sharing one code. See test_storekit_offer_* below.
        {"label": "Offer", "action": {"type": "storekit_offer",
                                     "offer_id": "OFF1", "environment": "sandbox"}},
        {"label": "Site", "action": {"type": "url", "value": "https://x/y"}},
        {"label": "Open", "action": {"type": "deeplink", "value": "shouldersurf://record"}},
        {"label": "Dismiss", "action": {"type": "none"}},
    ))
    assert client.post(f"{BASE}/campaigns", json=ok, headers=ADMIN).status_code == 200
    # unknown type is rejected
    bad = _campaign(id="cta_bad", variants=_native(
        {"label": "Evil", "action": {"type": "open_url_scheme", "value": "tel://911"}}))
    assert client.post(f"{BASE}/campaigns", json=bad, headers=ADMIN).status_code == 400
    # missing action.type is rejected
    notype = _campaign(id="cta_notype", variants=_native({"label": "Naked"}))
    assert client.post(f"{BASE}/campaigns", json=notype, headers=ADMIN).status_code == 400


def test_cta_id_optional_string(client):
    good = _campaign(id="cid_ok", variants=_native(
        {"label": "Get it", "cta_id": "primary", "action": {"type": "appstore", "value": "id1"}}))
    assert client.post(f"{BASE}/campaigns", json=good, headers=ADMIN).status_code == 200
    bad = _campaign(id="cid_bad", variants=_native(
        {"label": "Get it", "cta_id": 7, "action": {"type": "appstore", "value": "id1"}}))
    assert client.post(f"{BASE}/campaigns", json=bad, headers=ADMIN).status_code == 400


def test_cta_label_required(client):
    # GP owns the wording, so every native CTA must carry button text.
    good = _campaign(id="lbl_ok", variants=_native(
        {"label": "Get it", "action": {"type": "appstore", "value": "id1"}}))
    assert client.post(f"{BASE}/campaigns", json=good, headers=ADMIN).status_code == 200
    # missing label is rejected
    missing = _campaign(id="lbl_missing", variants=_native(
        {"action": {"type": "appstore", "value": "id1"}}))
    assert client.post(f"{BASE}/campaigns", json=missing, headers=ADMIN).status_code == 400
    # empty / non-string label is rejected
    empty = _campaign(id="lbl_empty", variants=_native(
        {"label": "", "action": {"type": "appstore", "value": "id1"}}))
    assert client.post(f"{BASE}/campaigns", json=empty, headers=ADMIN).status_code == 400
    nonstr = _campaign(id="lbl_nonstr", variants=_native(
        {"label": 7, "action": {"type": "appstore", "value": "id1"}}))
    assert client.post(f"{BASE}/campaigns", json=nonstr, headers=ADMIN).status_code == 400


def test_paywall_action_shape(client):
    # bare paywall (default), placement id, and a featured plan are all valid
    ok = _campaign(id="pw_ok", variants=_native(
        {"label": "Upgrade", "action": {"type": "paywall"}},
        {"label": "Go Pro", "action": {"type": "paywall", "value": "promo_slot", "plan": "pro"}},
        {"label": "Plus", "action": {"type": "paywall", "plan": "plus"}},
    ))
    assert client.post(f"{BASE}/campaigns", json=ok, headers=ADMIN).status_code == 200
    # unknown plan rejected
    badplan = _campaign(id="pw_badplan", variants=_native(
        {"label": "X", "action": {"type": "paywall", "plan": "gold"}}))
    assert client.post(f"{BASE}/campaigns", json=badplan, headers=ADMIN).status_code == 400
    # non-string placement id rejected
    badval = _campaign(id="pw_badval", variants=_native(
        {"label": "X", "action": {"type": "paywall", "value": 7}}))
    assert client.post(f"{BASE}/campaigns", json=badval, headers=ADMIN).status_code == 400


def test_deeplink_route_allowlist(client):
    # SS allowlist: shouldersurf://record is the only campaign-authorable route.
    ok = _campaign(id="dl_ok", app_id="shouldersurf", variants=_native(
        {"label": "Record", "action": {"type": "deeplink", "value": "shouldersurf://record"}}))
    assert client.post(f"{BASE}/campaigns", json=ok, headers=ADMIN).status_code == 200
    # an unlisted route can't be authored
    bad = _campaign(id="dl_bad", app_id="shouldersurf", variants=_native(
        {"label": "Sneaky", "action": {"type": "deeplink", "value": "shouldersurf://settings"}}))
    assert client.post(f"{BASE}/campaigns", json=bad, headers=ADMIN).status_code == 400
    # an app with no registered deeplink routes rejects any deeplink
    tr = _campaign(id="dl_tr", app_id="techrehearsal", variants=_native(
        {"label": "X", "action": {"type": "deeplink", "value": "techrehearsal://home"}}))
    assert client.post(f"{BASE}/campaigns", json=tr, headers=ADMIN).status_code == 400


def test_admin_key_required(client):
    assert client.get(f"{BASE}/campaigns", headers={"X-Admin-Key": "wrong"}).status_code == 403


def test_list_scoped_by_app(client):
    client.post(f"{BASE}/campaigns", json=_campaign(id="ss1", app_id="shouldersurf"), headers=ADMIN)
    client.post(f"{BASE}/campaigns", json=_campaign(id="tr1", app_id="techrehearsal"), headers=ADMIN)
    tr = client.get(f"{BASE}/campaigns?app=techrehearsal", headers=ADMIN).json()["campaigns"]
    ids = {c["id"] for c in tr}
    assert "tr1" in ids and "ss1" not in ids


# --- storekit_offer: one code is ONE redemption -----------------------------

def test_storekit_offer_dispensable_cta_is_accepted(client):
    """The good shape: offer_id + environment, no value. Promo resolve fills
    `value` with a code reserved to that actor from the pool."""
    ok = _campaign(id="offer_dispensable", variants=_native(
        {"label": "Start 3 free months",
         "action": {"type": "storekit_offer",
                    "offer_id": "OFFER1", "environment": "production"}}))
    assert client.post(f"{BASE}/campaigns", json=ok, headers=ADMIN).status_code == 200


def test_a_hardcoded_offer_code_must_be_acknowledged(client):
    """The bug that shipped, caught at authoring time.

    A one-time-use offer code is ONE redemption. A CTA carrying a hardcoded
    `value` hands the SAME code to everyone who sees the card, and the
    second person to tap gets a code Apple has already burned.

    On 2026-08-29 a friend-code card shipped carrying the exact code sitting
    in an earlier campaign's CTA, already redeemed in July. GP has had a
    reserve-once dispense pool since #310 and every live CTA bypassed it,
    because nothing ever asked which one the author meant.

    The dangerous shape stays AVAILABLE and stops being the ACCIDENTAL one.
    """
    bad = _campaign(id="offer_bare_value", variants=_native(
        {"label": "Redeem", "action": {"type": "storekit_offer",
                                       "value": "AYJW8TXXTM3JEJ6K3P"}}))
    r = client.post(f"{BASE}/campaigns", json=bad, headers=ADMIN)
    assert r.status_code == 400
    assert "shared_code" in r.text


def test_an_acknowledged_shared_code_is_allowed(client):
    """Sharing one code is legitimate for a single-recipient card. It just
    has to be said out loud, because a shared code and a per-user code are
    indistinguishable in the payload."""
    ok = _campaign(id="offer_shared", variants=_native(
        {"label": "Redeem", "action": {"type": "storekit_offer",
                                       "value": "AYJW8TXXTM3JEJ6K3P",
                                       "shared_code": True}}))
    assert client.post(f"{BASE}/campaigns", json=ok, headers=ADMIN).status_code == 200


def test_offer_id_plus_a_hardcoded_value_is_rejected(client):
    """Contradictory: resolve OVERWRITES value from the pool, so the authored
    code would silently never be used. Someone who wrote both meant one of
    them and this is the only moment we can ask which."""
    bad = _campaign(id="offer_both", variants=_native(
        {"label": "Redeem", "action": {"type": "storekit_offer",
                                       "offer_id": "OFFER1",
                                       "environment": "production",
                                       "value": "HARDCODED"}}))
    r = client.post(f"{BASE}/campaigns", json=bad, headers=ADMIN)
    assert r.status_code == 400
    assert "never be used" in r.text


def test_shared_code_without_a_value_is_rejected(client):
    """An empty shared code renders a button that cannot do anything."""
    bad = _campaign(id="offer_shared_empty", variants=_native(
        {"label": "Redeem", "action": {"type": "storekit_offer",
                                       "shared_code": True}}))
    assert client.post(f"{BASE}/campaigns", json=bad,
                       headers=ADMIN).status_code == 400
