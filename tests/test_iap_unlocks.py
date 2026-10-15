"""One-time IAP unlocks (Scott, 2026-10-13; spec SS docs/MODEL_UNLOCKS_IAP.md).

Two packs, unlock_apple_cloud and unlock_byok, are per-USER features, not
tiers. They live in `purchases`, never in users.tier or the one subscription
slot. Scott's rulings: the $4.99 "complete" pack grants the missing pack;
with zero packs it waits as pending, with both it grants nothing and is
flagged for refund. A refund of an unlock revokes the unlock and NOTHING
else: the bug this build fixed first was a webhook that downgraded any
refund to free, so a refunded unlock would have stripped a subscriber.
Free Project Chat on new builds: SS AI gets one conversation a month,
signed-in only; Apple Cloud or own key needs the matching pack; on-device
gets only the switch to SS AI. Old builds keep today's behaviour.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from tests.conftest import _insert_user, _jwt_token

BUNDLE = "com.test.app"
APPLE = "com.weirtech.shouldersurf.unlock.applecloud"
BYOK = "com.weirtech.shouldersurf.unlock.byok"
BOTH = "com.weirtech.shouldersurf.unlock.bundle"
UP_APPLE = "com.weirtech.shouldersurf.unlock.applecloud.upgrade"
UP_BYOK = "com.weirtech.shouldersurf.unlock.byok.upgrade"
PRO_SUB = "com.weirtech.shouldersurf.sub.pro.monthly"


def _user(db, uid=None, tier="free", anonymous=False):
    uid = uid or "u-" + uuid.uuid4().hex[:8]
    _insert_user(db, user_id=uid, tier=tier, monthly_limit=2.0)
    if anonymous:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET apple_sub = ? WHERE id = ?", (f"anonymous:{uid}", uid))
        conn.commit(); conn.close()
    return uid, {"Authorization": f"Bearer {_jwt_token(uid)}"}


def _buy(client, monkeypatch, headers, product, txn=None, signed=True):
    from app.services import receipt_verification as _rv
    txn = txn or str(2000000000000000 + uuid.uuid4().int % 10**12)
    monkeypatch.setattr(_rv, "verify_signed_transaction", lambda jws, bundle_ids: {
        "bundleId": "com.weirtech.shouldersurf", "originalTransactionId": txn,
        "transactionId": txn, "environment": "Sandbox", "productId": product})
    body = {"product_id": product, "transaction_id": txn}
    if signed:
        body["signed_transaction"] = "j.w.s"
    return client.post("/v1/verify-receipt", json=body, headers=headers), txn


def _features(client, headers):
    r = client.get("/v1/usage/me", headers=headers)
    assert r.status_code == 200, r.text
    f = r.json()["features"]
    return f.get("unlock_apple_cloud"), f.get("unlock_byok")


def _row(db, sql, *args):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


# --- purchase and the gate -----------------------------------------------------

def test_a_pack_grants_its_feature_and_leaves_the_tier_alone(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path)
    assert _features(client, h) == ("disabled", "disabled")
    r, _ = _buy(client, monkeypatch, h, APPLE)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "granted", "unlocks": ["unlock_apple_cloud"]}
    assert _features(client, h) == ("enabled", "disabled")
    assert _row(tmp_db_path, "SELECT tier, original_transaction_id FROM users WHERE id=?", uid) == ("free", None)


def test_the_bundle_grants_both(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    r, _ = _buy(client, monkeypatch, h, BOTH)
    assert r.json()["unlocks"] == ["unlock_apple_cloud", "unlock_byok"]
    assert _features(client, h) == ("enabled", "enabled")


def test_an_unsigned_unlock_is_refused(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    r, _ = _buy(client, monkeypatch, h, APPLE, signed=False)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "signed_transaction_required"
    assert _features(client, h) == ("disabled", "disabled")


def test_reposting_on_restore_is_idempotent(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _, txn = _buy(client, monkeypatch, h, BYOK)
    r, _ = _buy(client, monkeypatch, h, BYOK, txn=txn)
    assert r.json() == {"status": "granted", "unlocks": ["unlock_byok"]}
    assert _row(tmp_db_path, "SELECT COUNT(*) FROM purchases")[0] == 1


def test_restore_on_another_account_moves_the_unlock(client, tmp_db_path, monkeypatch):
    _, h1 = _user(tmp_db_path)
    _, h2 = _user(tmp_db_path)
    _, txn = _buy(client, monkeypatch, h1, APPLE)
    _buy(client, monkeypatch, h2, APPLE, txn=txn)
    assert _features(client, h2) == ("enabled", "disabled")
    assert _features(client, h1) == ("disabled", "disabled")


# --- Scott's upgrade products (replacing "complete", 2026-10-13) ------------------

def test_an_upgrade_grants_its_pack_to_an_owner_of_the_other(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, APPLE)
    r, _ = _buy(client, monkeypatch, h, UP_BYOK)
    assert r.json() == {"status": "granted", "unlocks": ["unlock_apple_cloud", "unlock_byok"]}


def test_an_upgrade_without_the_other_pack_waits_then_grants(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    r, _ = _buy(client, monkeypatch, h, UP_APPLE)
    assert r.json() == {"status": "pending", "unlocks": []}
    _buy(client, monkeypatch, h, BYOK)
    assert _features(client, h) == ("enabled", "enabled")


def test_an_upgrade_for_a_pack_already_owned_is_flagged(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, BOTH)
    r, txn = _buy(client, monkeypatch, h, UP_BYOK)
    assert r.json()["status"] == "refund_flagged"
    assert _row(tmp_db_path, "SELECT status, grants FROM purchases WHERE transaction_id=?", txn) == ("refund_flagged", "[]")


def test_an_upgrade_never_grants_the_other_pack(client, tmp_db_path, monkeypatch):
    """The applecloud upgrade grants Apple Cloud, never own key, even pending."""
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, UP_APPLE)
    _buy(client, monkeypatch, h, APPLE)
    assert _features(client, h) == ("enabled", "disabled")


# --- the webhook: an unlock refund never touches the subscription ------------------

def _notify(client, monkeypatch, ntype, product, txn, token=None):
    import app.routers.apple_webhooks as W
    n = {"notificationType": ntype, "subtype": "", "data": {
        "bundleId": BUNDLE, "environment": "Sandbox",
        "signedTransactionInfo": {"originalTransactionId": txn, "transactionId": txn,
                                  "productId": product, "appAccountToken": token,
                                  "purchaseDate": 1760000000000}}}
    monkeypatch.setattr(W, "decode_notification", lambda payload, bid: n)
    return client.post("/v1/apple-notifications", json={"signedPayload": "x.y.z"})


def test_refunding_an_unlock_revokes_it_and_keeps_the_subscription(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path, tier="pro")
    _, txn = _buy(client, monkeypatch, h, APPLE)
    r = _notify(client, monkeypatch, "REFUND", APPLE, txn)
    assert r.json()["action"] == "unlock_revoked"
    assert _row(tmp_db_path, "SELECT tier FROM users WHERE id=?", uid) == ("pro",)
    assert _features(client, h) == ("disabled", "disabled")


def test_a_subscription_refund_still_downgrades(client, tmp_db_path, monkeypatch):
    """The unlock branch must not swallow the subscription path."""
    uid, _ = _user(tmp_db_path, tier="pro")
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE users SET original_transaction_id='sub-otid' WHERE id=?", (uid,))
    conn.commit(); conn.close()
    r = _notify(client, monkeypatch, "REFUND", PRO_SUB, "sub-otid")
    assert r.json()["action"] == "refunded"
    assert _row(tmp_db_path, "SELECT tier FROM users WHERE id=?", uid) == ("free",)


def test_one_time_charge_records_by_app_account_token(client, tmp_db_path, monkeypatch):
    uid, h = _user(tmp_db_path)
    r = _notify(client, monkeypatch, "ONE_TIME_CHARGE", BYOK, "2000000999999999", token=uid.upper())
    assert r.json()["action"] == "unlock_granted"
    assert _features(client, h) == ("disabled", "enabled")


# --- anonymous merge ------------------------------------------------------------

def test_merge_takes_the_union_of_unlocks(client, tmp_db_path, monkeypatch):
    import asyncio
    import aiosqlite
    from app.services.anonymous_merge import merge_into
    anon, ha = _user(tmp_db_path, anonymous=True)
    apple, hp = _user(tmp_db_path)
    _buy(client, monkeypatch, ha, APPLE)
    _buy(client, monkeypatch, hp, BYOK)

    async def go():
        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            a = dict(await (await db.execute("SELECT * FROM users WHERE id=?", (anon,))).fetchone())
            p = dict(await (await db.execute("SELECT * FROM users WHERE id=?", (apple,))).fetchone())
            return await merge_into(db, a, p)
    out = asyncio.run(go())
    assert out["moved"]["purchases"] == 1
    assert _features(client, hp) == ("enabled", "enabled")


# --- Project Chat for free, new builds ----------------------------------------------

def _check(client, headers, provider=None, session=None, model="external"):
    body = {"selected_model": model}
    if provider:
        body["selected_provider"] = provider
    if session:
        body["session_id"] = session
    r = client.post("/v1/features/project-chat/check", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_old_builds_keep_todays_verdicts(client, tmp_db_path):
    _, h = _user(tmp_db_path)
    assert _check(client, h)["verdict"] == "send_to_gp"


def test_free_on_apple_cloud_needs_the_pack(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    out = _check(client, h, "apple_cloud")
    assert out["verdict"] == "unlock_required"
    assert out["cta"]["unlock"] == "unlock_apple_cloud" and out["cta"]["switch_to_ssai"] is True
    _buy(client, monkeypatch, h, APPLE)
    assert _check(client, h, "apple_cloud")["verdict"] == "send_to_user_model"
    assert _check(client, h, "byok")["cta"]["unlock"] == "unlock_byok"


def test_free_on_device_is_offered_only_the_switch(client, tmp_db_path, monkeypatch):
    _, h = _user(tmp_db_path)
    _buy(client, monkeypatch, h, BOTH)
    out = _check(client, h, "on_device")
    assert out["verdict"] == "unlock_required" and out["cta"]["unlock"] is None


def test_anonymous_free_gets_no_taste(client, tmp_db_path):
    _, h = _user(tmp_db_path, anonymous=True)
    assert _check(client, h, "ssai", model="ssai")["verdict"] == "login_required"


def test_the_free_taste_is_one_conversation_a_month(client, tmp_db_path):
    from app.services.project_chat_policy import record_taste
    import asyncio
    import aiosqlite
    uid, h = _user(tmp_db_path)
    first = _check(client, h, "ssai", "S1", model="ssai")
    assert first["verdict"] == "send_to_gp" and first["quota"]["used"] == 0

    async def count(session):
        async with aiosqlite.connect(tmp_db_path) as db:
            await record_taste(db, uid, session)
    asyncio.run(count("S1"))
    assert _check(client, h, "ssai", "S1", model="ssai")["verdict"] == "send_to_gp"
    second = _check(client, h, "ssai", "S2", model="ssai")
    assert second["verdict"] == "quota_exhausted" and second["cta"]["kind"] == "quota_exhausted"


def test_paid_tiers_are_untouched_by_provider(client, tmp_db_path):
    _, h = _user(tmp_db_path, tier="plus")
    assert _check(client, h, "apple_cloud")["verdict"] == "send_to_user_model"
    assert _check(client, h, "ssai", model="ssai")["verdict"] == "send_to_gp"


def test_tiers_serves_the_pack_copy(client):
    r = client.get("/v1/tiers")
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()["unlocks"]]
    assert ids == ["unlock_apple_cloud", "unlock_byok", "bundle", "unlock_apple_cloud_upgrade", "unlock_byok_upgrade"]


# --- /v1/chat agrees with the preflight --------------------------------------------

def _pc_send(client, headers, provider, session, model="external"):
    return client.post("/v1/chat", headers=headers, json={
        "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
        "system_prompt": "BASE", "user_content": "what did we decide?",
        "metadata": {"prompt_mode": "ProjectChat", "selected_model": model,
                     "selected_provider": provider, "session_id": session}})


def test_chat_refuses_apple_cloud_without_the_pack(client, tmp_db_path):
    _, h = _user(tmp_db_path)
    r = _pc_send(client, h, "apple_cloud", "S9")
    assert r.status_code == 402, r.text
    assert r.json()["detail"] == {"code": "unlock_required", "unlock": "unlock_apple_cloud",
                                  "switch_to_ssai": True}


def test_chat_refuses_a_second_free_conversation(client, tmp_db_path):
    import asyncio
    import aiosqlite
    from app.services.project_chat_policy import record_taste
    uid, h = _user(tmp_db_path)

    async def used():
        async with aiosqlite.connect(tmp_db_path) as db:
            await record_taste(db, uid, "S1")
    asyncio.run(used())
    r = _pc_send(client, h, "ssai", "S2", model="ssai")
    assert r.status_code == 402, r.text
    assert r.json()["detail"] == {"code": "quota_exhausted"}


def test_pack_names_are_sentence_case_like_the_store():
    """Scott, 2026-10-13: the ASC names and the app use sentence case.
    Renamed 2026-10-15 (Scott, via SS) to the App Store Connect names."""
    import json
    from pathlib import Path
    d = json.loads((Path(__file__).resolve().parent.parent / "config/remote/tiers.json").read_text())
    assert {u["id"]: u["name"] for u in d["unlocks"]} == {
        "unlock_apple_cloud": "Enhanced features for Apple Cloud",
        "unlock_byok": "Enhanced features for your own key",
        "bundle": "Enhanced features for both", "unlock_apple_cloud_upgrade": "Add Apple Cloud",
        "unlock_byok_upgrade": "Add your own key"}


def test_apple_cloud_file_attachments_are_a_pack_bullet_in_every_language():
    """Scott, 2026-10-15: file attachments on Apple Cloud belong to the Apple
    Cloud pack (the client enforces it on unlock_apple_cloud). Both products
    that grant it say so, in every served language; the own key pack does not."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "config/remote"
    words = {"": "Send text, Markdown, PDF, Word and RTF files on Apple Cloud",
             ".es": "Envía archivos de texto, Markdown, PDF, Word y RTF en Apple Cloud",
             ".fr": "Envoyez des fichiers texte, Markdown, PDF, Word et RTF sur Apple Cloud",
             ".ja": "Apple Cloudでテキスト、Markdown、PDF、Word、RTFファイルを送信"}
    for suf, bullet in words.items():
        d = json.loads((root / f"tiers{suf}.json").read_text(encoding="utf-8"))
        by = {u["id"]: u for u in d["unlocks"]}
        for pid in ("unlock_apple_cloud", "unlock_apple_cloud_upgrade"):
            assert by[pid]["bullets"].count(bullet) == 1, (suf, pid)
        for pid in ("unlock_byok", "unlock_byok_upgrade"):
            assert bullet not in by[pid]["bullets"], (suf, pid)
        for u in d["unlocks"]:
            for text in [u["name"]] + u["bullets"]:
                assert "\u2014" not in text and "\u2013" not in text, (suf, text)


def test_the_anthropic_note_names_the_current_models():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "config/remote"
    for suf in ("", ".es", ".fr", ".ja"):
        d = json.loads((root / f"llm-providers{suf}.json").read_text())
        note = next(p for p in d["providers"] if p["id"] == "anthropic")["notes"]
        assert "Fable 5.1" in note and "Opus 5.5" in note and "Haiku 4.5" in note, suf
        assert "Opus 4.7 and Sonnet 4.6" not in note and "—" not in note and "–" not in note


def test_quota_exhausted_copy_is_served_filled(client, tmp_db_path):
    """SS shows cta.text verbatim and never re-templates (SS, 2026-10-14):
    prod served a literal {total} until this. Scott picked the copy."""
    from app.services.project_chat_policy import record_taste
    import asyncio
    import aiosqlite
    uid, h = _user(tmp_db_path)

    async def count(session):
        async with aiosqlite.connect(tmp_db_path) as db:
            await record_taste(db, uid, session)
    asyncio.run(count("S1"))
    text = _check(client, h, "ssai", "S2", model="ssai")["cta"]["text"]
    assert text == "You've used your free Project Chat for this month. Upgrade to Plus for unlimited."


def test_counts_are_filled_wherever_the_copy_carries_them():
    from app.routers.features import _fill_counts
    assert _fill_counts("{remaining} of {total} left", {"taste_used": 0}) == "1 of 1 left"
    assert _fill_counts("{remaining} of {total} left", {"taste_used": 3}) == "0 of 1 left"
    assert _fill_counts("used your {total}", {}) == "used your 1"


def test_no_served_project_chat_copy_keeps_an_unknown_placeholder():
    import json, re
    from pathlib import Path
    for f in sorted(Path("config/remote").glob("tiers*.json")):
        cta = json.loads(f.read_text())["feature_definitions"]["project_chat"]["cta_strings"]
        for kind, text in cta.items():
            left = set(re.findall(r"\{[^}]*\}", text)) - {"{total}", "{remaining}"}
            assert not left, (f.name, kind, left)


def test_upgrade_bullets_drop_the_old_pack_naming():
    """SS, 2026-10-15: with the packs renamed, "pack" in an upgrade bullet no
    longer names anything the buyer owns."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "config/remote"
    d = json.loads((root / "tiers.json").read_text())
    by = {u["id"]: u for u in d["unlocks"]}
    assert by["unlock_apple_cloud_upgrade"]["bullets"][0] == \
        "Adds the Apple Cloud enhanced features to the own key ones you already have"
    assert by["unlock_byok_upgrade"]["bullets"][0] == \
        "Adds the own key enhanced features to the Apple Cloud ones you already have"
    for suf, word in (("", "pack"), (".es", "paquete"), (".fr", "pack"), (".ja", "パック")):
        d = json.loads((root / f"tiers{suf}.json").read_text(encoding="utf-8"))
        for u in d["unlocks"]:
            for text in [u["name"]] + u["bullets"]:
                assert word not in text.lower(), (suf, text)
