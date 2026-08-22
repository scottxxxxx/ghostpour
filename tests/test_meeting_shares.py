"""Meeting share via iMessage, GP half (2026-08-21).

Ruled: payload is SS's .shouldersurf archive stored as uploaded; Variant
A; creation free on every tier; host share.shouldersurf.com. The one
test that matters most is the last group: nothing here ever reaches
Context Quilt, because a share is SS's meeting record and per-user
memory must never travel through a share.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

ARCHIVE = b"PK\x03\x04 fake shouldersurf archive bytes " + b"x" * 2000
HDRS = {"Content-Type": "application/vnd.shouldersurf.archive",
        "X-Share-Title": "Sunset Canyon kickoff", "X-Share-Date": "2026-08-21",
        "X-Share-Duration-Seconds": "763", "X-Share-Summary-Line": "Agreed the Q4 plan.",
        "X-Share-Transcript-Included": "true"}


def _create(client, user, **extra):
    h = {**user["headers"], **HDRS, **extra}
    return client.post("/v1/shares", content=ARCHIVE, headers=h)


def test_create_returns_share_id_url_and_expiry_and_the_url_answers(client, free_user):
    """Free tier on purpose (creation is free everywhere). Check the echo:
    the URL SS receives must be the URL the page answers on."""
    resp = _create(client, free_user)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"share_id", "url", "expires_at"}
    assert body["url"].startswith("https://share.shouldersurf.com/s/")
    token = body["url"].rsplit("/", 1)[1]
    assert len(token) >= 20 and free_user["user_id"] not in token
    page = client.get(f"/s/{token}")
    assert page.status_code == 200
    assert "og:title" in page.text and "Sunset Canyon kickoff" in page.text
    assert "noindex" in page.text


def test_archive_comes_back_byte_for_byte_on_its_own_path(client, pro_user):
    token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
    r = client.get(f"/s/{token}/archive")
    assert r.status_code == 200
    assert r.content == ARCHIVE
    assert r.headers["content-type"].startswith("application/vnd.shouldersurf.archive")


def test_revoke_is_owner_only_and_immediate(client, pro_user, free_user):
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]
    assert client.delete(f"/v1/shares/{body['share_id']}", headers=free_user["headers"]).status_code == 404
    assert client.get(f"/s/{token}").status_code == 200
    assert client.delete(f"/v1/shares/{body['share_id']}", headers=pro_user["headers"]).status_code == 200
    assert client.get(f"/s/{token}").status_code == 410
    assert client.get(f"/s/{token}/archive").status_code == 410
    assert "no longer available" in client.get(f"/s/{token}").text


def test_view_count_excludes_preview_fetchers(client, pro_user):
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]
    client.get(f"/s/{token}", headers={"User-Agent": "facebookexternalhit/1.1"})
    client.get(f"/s/{token}", headers={"User-Agent": "Mozilla/5.0 (iPhone) AppleWebKit Safari"})
    client.get(f"/s/{token}", headers={"User-Agent": "Twitterbot/1.0"})
    stats = client.get(f"/v1/shares/{body['share_id']}/stats", headers=pro_user["headers"]).json()
    assert stats["view_count"] == 1 and stats["live"] is True


def test_unknown_token_is_410_not_404_and_leaks_nothing(client):
    r = client.get("/s/definitely-not-a-token")
    assert r.status_code == 410 and "no longer available" in r.text


def test_empty_is_refused_and_size_is_capped_only_by_the_tier_dial(client, pro_user):
    h = {**pro_user["headers"], **HDRS}
    assert client.post("/v1/shares", content=b"", headers=h).status_code == 422
    # no cap by default: a 40 MB archive is accepted
    with patch("app.routers.shares.shares.share_by_id"):
        pass
    big = b"x" * (40 * 1048576)
    assert client.post("/v1/shares", content=big, headers=h).status_code == 200
    # the cap is a per-tier dial (feature_definitions.share.max_archive_mb)
    fd = client.app.state.remote_configs.setdefault("tiers", {}).setdefault("tiers", {}).setdefault("pro", {}).setdefault("feature_definitions", {})
    fd["share"] = {**(fd.get("share") or {}), "max_archive_mb": 0.0001}  # ~104 bytes
    r = client.post("/v1/shares", content=ARCHIVE, headers=h)
    assert r.status_code == 413
    d = r.json()["detail"]
    assert d["code"] == "share_too_large" and d["size_bytes"] == len(ARCHIVE) and d["limit_bytes"] == 104
    assert "MB" in d["message"]
    fd["share"].pop("max_archive_mb")


def test_daily_creation_cap_is_a_tier_dial(client, pro_user):
    rc = client.app.state.remote_configs
    rc.setdefault("tiers", {}).setdefault("tiers", {}).setdefault("pro", {}).setdefault("feature_definitions", {})["share"] = {"creations_per_day": 2}
    assert _create(client, pro_user).status_code == 200
    assert _create(client, pro_user).status_code == 200
    r = _create(client, pro_user)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "share_rate_limited"


def test_aasa_404s_until_ss_supplies_app_ids_then_serves_json(client):
    rc = client.app.state.remote_configs
    rc.setdefault("client-config", {})["share"] = {"aasa_app_ids": []}
    assert client.get("/.well-known/apple-app-site-association").status_code == 404
    rc["client-config"]["share"] = {"aasa_app_ids": ["F22KGHDYAE.com.shouldersurf.ShoulderSurf"]}
    r = client.get("/.well-known/apple-app-site-association")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    d = r.json()
    assert d["applinks"]["details"][0]["appIDs"] == ["F22KGHDYAE.com.shouldersurf.ShoulderSurf"]
    assert d["applinks"]["details"][0]["components"] == [{"/": "/s/*"}]


def test_token_never_appears_in_a_log_line(client, pro_user, caplog):
    caplog.set_level("DEBUG")
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]
    client.get(f"/s/{token}")
    client.delete(f"/v1/shares/{body['share_id']}", headers=pro_user["headers"])
    joined = "\n".join(f"{r.getMessage()} {getattr(r, '__dict__', {})}" for r in caplog.records
                       if r.name.startswith("ghostpour"))
    assert token not in joined


# --- the one that matters: no share ever reaches Context Quilt ---------------

def test_no_share_route_ever_touches_context_quilt(client, pro_user):
    from app.services import context_quilt as cq
    with patch.object(cq, "capture", new_callable=AsyncMock) as cap, \
         patch.object(cq, "recall", new_callable=AsyncMock) as rec, \
         patch.object(cq, "quilt_dossier", new_callable=AsyncMock) as dos:
        body = _create(client, pro_user).json()
        token = body["url"].rsplit("/", 1)[1]
        client.get(f"/s/{token}")
        client.get(f"/s/{token}/archive")
        client.get(f"/v1/shares/{body['share_id']}/stats", headers=pro_user["headers"])
        client.delete(f"/v1/shares/{body['share_id']}", headers=pro_user["headers"])
    assert cap.await_count == 0 and rec.await_count == 0 and dos.await_count == 0
    src = open("app/routers/shares.py").read() + open("app/services/meeting_shares.py").read()
    assert "context_quilt" not in src and "cq_proxy" not in src


def test_account_deletion_removes_shares_and_their_bytes(client, pro_user):
    """A share must not outlive the person who made it (the hosted copy is
    their meeting content at a public URL)."""
    import asyncio, aiosqlite
    from pathlib import Path
    from app.services.account_deletion import delete_user_data
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]
    assert client.get(f"/s/{token}").status_code == 200
    db_path = client.app.state.settings.database_url.split("///")[-1]
    async def run():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT storage_path FROM meeting_shares WHERE id = ?", (body["share_id"],))).fetchone()
            path = Path(row["storage_path"]); assert path.exists()
            counts = await delete_user_data(db, pro_user["user_id"], None)
            await db.commit()
            assert counts.get("meeting_shares", 0) == 1 and counts.get("meeting_shares_disk", 0) == 1
            assert not path.exists()
    asyncio.run(run())
    assert client.get(f"/s/{token}").status_code == 410


# --- The token shape is enforced here, for every client (2026-08-22) --------
#
# SS sabotaged their own universal-link parser and found that widening
# "exactly two segments" to "two or more, take the last" turns
# `/s/abc/../secret` into the token "secret", which on their side is a
# traversal fragment walking into the next request's URL. GP is the only
# side that can enforce the shape for EVERY client rather than trusting
# each one to parse correctly.
#
# Nothing downstream was exploitable: the token is only ever a bound
# parameter in a SELECT and storage_path comes from the row, never from the
# URL. This keeps a malformed token from reaching the database at all, and
# it answers identically to a wrong one, so the deliberate
# 410-for-everything property is untouched.

# Two groups, because they are stopped in two different places and saying
# so is more useful than forcing one number. Anything that is not a single
# path segment never matches the route at all and the router answers 404;
# anything that IS one segment but is not token-shaped reaches the handler
# and is answered 410, identically to a wrong token, which is what keeps
# the deliberate no-distinction-between-expired-revoked-unknown property.

@pytest.mark.parametrize("bad", [
    "secret", "short", "a" * 23, "a" * 21,
    "has spaces here here12", "plus+chars=here123456", "tilde~and.dots1234567",
])
def test_a_one_segment_token_of_the_wrong_shape_is_410(client, bad):
    from urllib.parse import quote
    r = client.get(f"/s/{quote(bad, safe='')}")
    assert r.status_code == 410, f"{bad!r} got {r.status_code}"
    assert "no longer available" in r.text
    assert client.get(f"/s/{quote(bad, safe='')}/archive").status_code == 410


@pytest.mark.parametrize("bad", ["..", "../etc/passwd", "abc/../secret", "a/b", ""])
def test_a_traversal_shaped_path_never_reaches_the_handler(client, bad):
    """SS's `/s/abc/../secret` case. It is not one path segment, so the
    router does not match it and nothing of ours runs. Asserted so that a
    future route change to `{token:path}`, which WOULD match it, is a red
    test rather than a quiet widening."""
    from urllib.parse import quote
    r = client.get(f"/s/{quote(bad, safe='')}")
    assert r.status_code == 404, f"{bad!r} reached a handler and got {r.status_code}"


def test_a_real_token_still_works(client, pro_user):
    """The bound has to let the actual thing through, which is the half of a
    validation people forget to assert."""
    token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
    from app.services.meeting_shares import is_token_shaped
    assert is_token_shaped(token), f"we minted a token our own check rejects: {token!r}"
    assert client.get(f"/s/{token}").status_code == 200


def test_the_shape_check_matches_what_new_token_actually_mints():
    """Pinned against the generator rather than against the regex, so a
    change to token length cannot pass its own test. A hundred draws,
    because token_urlsafe output length varies with padding."""
    from app.services.meeting_shares import is_token_shaped, new_token
    for _ in range(100):
        assert is_token_shaped(new_token())
