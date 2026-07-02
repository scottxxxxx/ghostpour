"""Per-user offer-code dispense: reserve-once idempotency, per-campaign pools,
and resolve-time injection / CTA suppression on exhaustion.

Resolve is device-anchored (auth optional), so idempotency and reserve-once are
exercised across distinct device_ids without minting multiple authed users. The
pool is seeded directly (the load-pool endpoint pulls from the Connect API, which
is unconfigured in tests).
"""

import asyncio
import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite

ADMIN = {"X-Admin-Key": "test-admin-key"}
SS = {"X-App-ID": "shouldersurf"}
OFFER = "offer-pro-test"


def _seed_pool(db_path, codes, *, offer_id=OFFER, environment="sandbox"):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    for c in codes:
        conn.execute(
            "INSERT OR IGNORE INTO offer_code_pool "
            "(code, offer_id, product_id, environment, batch_id, status, created_at) "
            "VALUES (?, ?, 'pro', ?, 'b1', 'available', ?)",
            (c, offer_id, environment, now),
        )
    conn.commit()
    conn.close()


def _offer_campaign(client, cid, *, offer_id=OFFER, environment="sandbox"):
    """Active, untargeted native campaign with an Open CTA + a dispensable
    storekit_offer CTA."""
    variant = {
        "variant_id": "native", "weight": 100, "render": "native",
        "native": {
            "schema_version": 1, "title": "Hey", "body": "test",
            "ctas": [
                {"label": "Open", "cta_id": "open",
                 "action": {"type": "url", "value": "https://shouldersurf.com"}},
                {"label": "Redeem offer", "cta_id": "ss_offer",
                 "action": {"type": "storekit_offer", "offer_id": offer_id,
                            "environment": environment, "value": ""}},
            ],
        },
    }
    body = {
        "id": cid, "name": cid, "app_id": "shouldersurf", "status": "active",
        "priority": 10, "targeting": {}, "frequency": {},
        "placements": [{"placement": "launch", "priority": 10}],
        "variants": [variant],
    }
    r = client.post("/webhooks/admin/campaigns", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text


def _resolve(client, device_id):
    return client.get(f"/v1/promo/resolve?device_id={device_id}", headers=SS).json()


def _offer_cta(resolved):
    ctas = resolved["variant"]["native"]["ctas"]
    return next((c for c in ctas if c["action"]["type"] == "storekit_offer"), None)


def test_resolve_injects_dispensed_code(client, tmp_db_path):
    _seed_pool(tmp_db_path, ["CODE1", "CODE2"])
    _offer_campaign(client, "off_inject")
    r = _resolve(client, "devA")
    assert r["campaign_id"] == "off_inject"
    cta = _offer_cta(r)
    assert cta["action"]["value"] in ("CODE1", "CODE2")   # a real code was injected
    # the non-dispensable Open CTA is untouched and still present
    labels = [c["cta_id"] for c in r["variant"]["native"]["ctas"]]
    assert labels == ["open", "ss_offer"]


def test_dispense_idempotent_same_device(client, tmp_db_path):
    _seed_pool(tmp_db_path, ["CODE1", "CODE2"])
    _offer_campaign(client, "off_idem")
    first = _offer_cta(_resolve(client, "devA"))["action"]["value"]
    second = _offer_cta(_resolve(client, "devA"))["action"]["value"]
    assert first == second                                 # same device -> same code
    # only one code got reserved, not one per resolve
    conn = sqlite3.connect(tmp_db_path)
    reserved = conn.execute(
        "SELECT COUNT(*) FROM offer_code_pool WHERE status='reserved'").fetchone()[0]
    conn.close()
    assert reserved == 1


def test_reserve_once_distinct_devices_distinct_codes(client, tmp_db_path):
    _seed_pool(tmp_db_path, ["CODE1", "CODE2"])
    _offer_campaign(client, "off_distinct")
    a = _offer_cta(_resolve(client, "devA"))["action"]["value"]
    b = _offer_cta(_resolve(client, "devB"))["action"]["value"]
    assert a != b
    assert {a, b} == {"CODE1", "CODE2"}


def test_exhaustion_suppresses_cta(client, tmp_db_path):
    _seed_pool(tmp_db_path, ["ONLYCODE"])                  # one code, two takers
    _offer_campaign(client, "off_exhaust")
    got = _offer_cta(_resolve(client, "devA"))["action"]["value"]
    assert got == "ONLYCODE"
    # second device: pool empty -> storekit_offer CTA is dropped, card still served
    r2 = _resolve(client, "devB")
    assert r2["campaign_id"] == "off_exhaust"
    assert _offer_cta(r2) is None                          # suppressed, not empty value
    assert [c["cta_id"] for c in r2["variant"]["native"]["ctas"]] == ["open"]


def test_environment_isolation(client, tmp_db_path):
    # sandbox codes must never satisfy a production CTA
    _seed_pool(tmp_db_path, ["SBX1"], environment="sandbox")
    _offer_campaign(client, "off_prod", environment="production")
    r = _resolve(client, "devA")
    assert _offer_cta(r) is None                           # no prod code -> suppressed


# --- service-level: load_pool idempotency + pool_status --------------------

def _arun(db_path, coro_factory):
    async def _go():
        async with aiosqlite.connect(db_path) as db:
            return await coro_factory(db)
    return asyncio.run(_go())


def test_load_pool_idempotent_and_status(client, tmp_db_path):
    from app.services import offer_dispense

    async def first(db):
        return await offer_dispense.load_pool(
            db, offer_id="ld", environment="production",
            codes=["A", "B", "C", "", "  "], batch_id="bb", product_id="pro")
    r1 = _arun(tmp_db_path, first)
    assert r1["loaded"] == 3 and r1["skipped"] == 0        # blanks dropped, not counted

    async def second(db):  # re-load same batch -> all skipped (idempotent)
        return await offer_dispense.load_pool(
            db, offer_id="ld", environment="production", codes=["A", "B", "C"])
    r2 = _arun(tmp_db_path, second)
    assert r2["loaded"] == 0 and r2["skipped"] == 3

    async def stat(db):
        return await offer_dispense.pool_status(db, offer_id="ld", environment="production")
    st = _arun(tmp_db_path, stat)
    assert st == {"offer_id": "ld", "environment": "production",
                  "available": 3, "reserved": 0, "total": 3}


def test_load_pool_endpoint_validates_and_gates(client):
    # bad environment -> 400 before any Connect call
    bad = client.post("/webhooks/admin/offer-codes/load-pool", headers=ADMIN,
                      json={"offer_id": "x", "environment": "prod", "batch_id": "b"})
    assert bad.status_code == 400
    # valid env but Connect API key unprovisioned in tests -> 400 with clear msg
    gated = client.post("/webhooks/admin/offer-codes/load-pool", headers=ADMIN,
                        json={"offer_id": "x", "environment": "production", "batch_id": "b"})
    assert gated.status_code == 400
    assert "not provisioned" in gated.text.lower()
