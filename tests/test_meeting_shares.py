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


# --- Card text is percent-encoded UTF-8 (2026-08-22) ------------------------
#
# Found while writing SS's test brief, BEFORE they built the client, which
# is the only reason this is a contract decision rather than a migration.
#
# The card fields ride in X-Share-* headers so the body can be the archive
# bytes with no multipart parsing between SS's zip and our disk. Right call
# for the bytes, trap for the TEXT: an HTTP header value is not a place for
# user-generated Unicode. A meeting titled "四半期レビュー" cannot go in a
# header at all from a strict client (httpx raises UnicodeEncodeError before
# a request is even made), and a client that writes the raw UTF-8 bytes
# anyway gets them back latin-1-decoded here, stored, and rendered onto the
# card and the og:title where a person reads it. Nothing errors. The share
# just says something else.
#
# Contract: UTF-8, percent-encoded. ASCII on the wire, one rule, and a
# no-op for plain English titles.

from html import unescape  # noqa: E402
from urllib.parse import quote  # noqa: E402


def _page_of(client, user, **extra):
    """The page text with HTML entities resolved, so these tests assert what
    a READER sees. The renderer escapes & and ' correctly and that is not
    what is under test here; comparing against the raw markup would make
    every accented fixture fail for the wrong reason."""
    r = _create(client, user, **extra)
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[1]
    page = client.get(f"/s/{token}")
    assert page.status_code == 200
    return unescape(page.text)


@pytest.mark.parametrize("title", [
    "四半期レビュー",                      # the case a strict client cannot send raw
    "Séverine & l'équipe: révision",       # latin-1-able, so mojibake would be subtle
    "Kickoff 🎉 with the whole team",      # outside the BMP
    "Обзор квартала",                      # non-latin, non-CJK
])
def test_a_non_ascii_title_survives_onto_the_card(client, pro_user, title):
    html = _page_of(client, pro_user, **{"X-Share-Title": quote(title)})
    assert title in html, "the title was mangled between SS's header and the page"


def test_a_plain_ascii_title_needs_no_encoding(client, pro_user):
    """Percent-decoding is a no-op on ASCII, which is why an unencoded
    English title still works and why this is one rule rather than two."""
    html = _page_of(client, pro_user, **{"X-Share-Title": "Sunset Canyon kickoff"})
    assert "Sunset Canyon kickoff" in html


def test_a_literal_percent_must_be_encoded_and_then_survives(client, pro_user):
    html = _page_of(client, pro_user, **{"X-Share-Title": quote("50% done, 3% left")})
    assert "50% done, 3% left" in html


def test_a_malformed_sequence_costs_a_character_not_the_share(client, pro_user):
    """errors="replace": a bad byte must not 4xx a share that is otherwise
    fine. The user loses one glyph, not the thing they were trying to send."""
    r = _create(client, pro_user, **{"X-Share-Title": "Broken%E5%9Bend"})
    assert r.status_code == 200, r.text


def test_the_summary_line_gets_the_same_treatment_as_the_title(client, pro_user):
    """Both are card text and both are user-generated, so a fix that only
    covered the title would leave the description mojibake on exactly the
    meetings the title fix was for."""
    summary = "On a décidé: livrer le 3 décembre."
    html = _page_of(client, pro_user, **{"X-Share-Summary-Line": quote(summary)})
    assert summary in html

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


def test_raw_non_ascii_in_the_header_is_refused_not_stored(client, pro_user):
    """CQ asked the right question: what if a client sends raw UTF-8 anyway?

    By the time it reaches us it has already been latin-1 decoded by ASGI,
    so the original is gone and any repair is a guess. The choice is a 400
    now or a wrong title on a card forever, and only the receiving side can
    make it. The client finds out at build time; a user would have found
    out from a bubble that said something else.

    Sent as a pre-encoded byte header, because a strict client library will
    not even let you set this by hand, which is itself the first line of
    defence and the reason this was found at all."""
    # As BYTES, because a strict client library will not let you set a
    # non-ASCII header from a str at all: httpx raises UnicodeEncodeError
    # before a request is made. That refusal is the first line of defence
    # and it is how this whole class was found; the bytes path is what a
    # client that goes around it actually puts on the wire.
    r = _create(client, pro_user, **{"X-Share-Title": "四半期レビュー".encode("utf-8")})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "share_header_not_encoded"


def test_a_latin1_accent_sent_raw_is_refused_too(client, pro_user):
    """The subtle half. "Séverine" raw is VALID latin-1, so it would decode
    to something that looks right in a debugger and is still not what the
    sender typed once any other language shows up. One rule, no exceptions
    for the languages that happen to survive."""
    r = _create(client, pro_user, **{"X-Share-Title": "Séverine".encode("latin-1")})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "share_header_not_encoded"

# --- The transcript is a fact, not a choice (2026-08-22) --------------------
#
# SS's pre-build suspicion pass, before a line of client code, found that
# the contract described `X-Share-Transcript-Included` as "the sender's
# per-share choice, default off" and 403'd on a tier dial. They checked
# their exporter rather than agreeing with the doc: BundlePayloadOptions
# has toggles for audio, images, diarization, chat, report and generated
# files and NO transcript toggle, and applyPayloadFilter strips only chat
# and report. The transcript is a field on the meeting record, so it always
# travels. All twelve real bundles carry one.
#
# So the dial could not restrict a feature. It could only make every share
# from that tier fail, with a message telling the sender to do something
# the app cannot do. It is removed. These tests are what stop it coming
# back as a "restore the tier control" cleanup.

def test_a_share_carrying_a_transcript_is_never_refused(client, pro_user):
    """The header says true because the archive always has one. That must
    not be a 403 on any tier, however the dials are set."""
    r = _create(client, pro_user, **{"X-Share-Transcript-Included": "true"})
    assert r.status_code == 200, r.text


def test_an_old_transcript_allowed_dial_cannot_refuse_a_share(client, pro_user, monkeypatch):
    """The specific regression: someone re-adds the dial, or an operator
    sets the key on a tier because it is still in a dashboard somewhere.
    Setting it false must change nothing, because there is nothing on the
    sender's side that could respond to it."""
    from app.main import app
    tiers = app.state.remote_configs.setdefault("tiers", {}).setdefault("tiers", {})
    fd = tiers.setdefault("pro", {}).setdefault("feature_definitions", {})
    original = fd.get("share")
    fd["share"] = {**(original or {}), "transcript_allowed": False}
    try:
        r = _create(client, pro_user, **{"X-Share-Transcript-Included": "true"})
        assert r.status_code == 200, (
            "a dial the sender cannot answer just refused their share")
    finally:
        if original is None:
            fd.pop("share", None)
        else:
            fd["share"] = original


def test_the_transcript_flag_still_reaches_the_page(client, pro_user):
    """Removing the gate must not remove the SIGNAL: the renderer uses it
    to decide whether to offer tap-to-reveal, which is Scott's 2026-08-22
    ruling. A flag that no longer gates anything is exactly the kind that
    gets deleted next, so the observable is asserted rather than the row."""
    import pathlib as _pl
    bundle = (_pl.Path(__file__).parent / "fixtures/share/fixture-typical.shouldersurf").read_bytes()
    h = {**pro_user["headers"], **HDRS, "X-Share-Transcript-Included": "true",
         "Content-Type": "application/zip"}
    r = client.post("/v1/shares", content=bundle, headers=h)
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[1]
    page = client.get(f"/s/{token}").text
    assert "Show transcript" in page, (
        "the transcript flag stopped reaching the renderer")


# --- Percent-decode and attribute-escape, in that order, through the REAL
# --- route (2026-08-22) -----------------------------------------------------
#
# CQ's analysis and it is the reason this test exists in this shape: the two
# possible bugs MASK each other.
#
#   A. the header is not percent-decoded
#   B. the title is not HTML-escaped into the single-quoted attribute
#
# `%27` is attribute-safe. So if A is present the apostrophe never arrives
# as an apostrophe, never reaches the escaper, and B cannot fire. A missing
# decode HIDES a missing escape, and a hand-typed raw apostrophe tests B
# while being unable to test A at all.
#
# So the input here is what a CLIENT actually sends: percent-encoded UTF-8
# containing %27, driven through POST /v1/shares rather than through the
# service layer. That ordering matters and it is worth stating: the decode
# lives in the ROUTE, so anything that calls create_share directly proves
# nothing about it. Every fixture built by hand from this side of the auth
# boundary skipped it, which is exactly how a contract ends up implemented
# on one side.
#
# Asserted through an HTML parser, so an attribute terminated early comes
# back truncated rather than merely looking wrong in a grep.

@pytest.mark.parametrize("title", [
    "Sarah's team sync",
    "Q3 planning, don't reschedule",
    "L'équipe: Séverine's review 🎉",
    "四半期レビュー: l'équipe",
])
def test_a_client_encoded_title_decodes_then_escapes_onto_the_card(client, pro_user, title):
    from urllib.parse import quote
    encoded = quote(title)
    assert "%27" in encoded or "'" not in title, "fixture is not encoding the apostrophe"

    r = _create(client, pro_user, **{"X-Share-Title": encoded})
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[1]
    page = client.get(f"/s/{token}")
    assert page.status_code == 200

    import html.parser

    class P(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(); self.meta = {}; self.title = None; self._t = False
        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "meta" and "content" in d:
                k = d.get("property") or d.get("name")
                if k: self.meta[k] = d["content"]
            elif tag == "title": self._t = True
        def handle_endtag(self, tag):
            if tag == "title": self._t = False
        def handle_data(self, data):
            if self._t: self.title = (self.title or "") + data

    p = P(); p.feed(page.text)
    assert p.title == title, f"<title> is {p.title!r}"
    assert p.meta.get("og:title") == title, (
        f"og:title is {p.meta.get('og:title')!r}. A literal %27 or %E2 here "
        "means the decode is missing; a truncated value means the escape is.")
    assert p.meta.get("twitter:title") == title
    assert "%27" not in page.text and "%E5" not in page.text, (
        "an encoded sequence reached the page, so the decode did not run")


# --- The upload is bounded DURING the read (2026-08-22) --------------------
#
# CQ asked whether the edge caps request bodies. Bifrost measured the live
# VM: NPM sets client_max_body_size 2000m globally, so it does not. The
# answer to their question was less interesting than what looking for it
# turned up on our side.
#
# `POST /v1/shares` did `await request.body()` and THEN compared the length
# to the tier dial. That is the check-after-read mistake in its original
# habitat, and it is worse here than in the zip reader, because the dial
# looked like protection: by the time a 25 MB limit said no, 25 MB was the
# least of what had already been buffered. With a 2000m proxy in front, an
# authenticated client could make this process allocate two gigabytes and
# then be told the limit was twenty five megabytes.
#
# Two bounds now, and they are different things on purpose. The tier dial is
# a PRODUCT limit and Scott ruled it absent by default. The absolute ceiling
# is a MEMORY bound and is not configurable, because "uncapped" cannot mean
# "whatever an authenticated client feels like sending".

from fastapi import HTTPException  # noqa: E402
from app.routers.shares import ABSOLUTE_MAX_ARCHIVE_BYTES  # noqa: E402


def _post_archive(client, user, body, extra_headers=None):
    h = {**user["headers"], **HDRS, **(extra_headers or {})}
    return client.post("/v1/shares", content=body, headers=h)


def test_a_lying_content_length_does_not_get_past_the_bound(client, pro_user):
    """Content-Length is checked first because it is free, and is not
    trusted, for the same reason a zip's declared entry size is not: it is a
    claim by the sender. A body far larger than a header that understates it
    must still be refused, which is only possible if the streaming total is
    the real bound."""
    from app.main import app
    tiers = app.state.remote_configs.setdefault("tiers", {}).setdefault("tiers", {})
    fd = tiers.setdefault("pro", {}).setdefault("feature_definitions", {})
    original = fd.get("share")
    fd["share"] = {**(original or {}), "max_archive_mb": 1}
    try:
        # 3 MB of body against a 1 MB dial. httpx sets a truthful
        # Content-Length, so this exercises the header path; the streaming
        # path is exercised by the test below where no length is sent.
        r = _post_archive(client, pro_user, b"x" * (3 * 1024 * 1024))
        assert r.status_code == 413, r.text
        assert r.json()["detail"]["code"] == "share_too_large"
        assert r.json()["detail"]["limit_bytes"] == 1024 * 1024
    finally:
        if original is None:
            fd.pop("share", None)
        else:
            fd["share"] = original


def test_the_upload_stops_being_read_the_moment_it_is_too_big():
    """The property, measured exactly: HOW MUCH of the body was consumed
    before the refusal.

    The first version of this test drove the real route and asserted a 413.
    It was green against a `body()`-then-check implementation, because the
    STATUS is identical either way; the difference is memory, not outcome.
    Same trap as the null-byte zip bomb, walked into a second time in the
    same shape.

    Peak memory is not usable here either: TestClient materialises the
    request body on the client side, so tracemalloc in-process measured
    128 MB for a 64 MB upload no matter what the server did. The client
    dominated the number.

    So: call `_read_archive` with a counting stream. It reads only
    `request.headers` and `request.stream()`, which is exactly what a stub
    can supply faithfully. A buffer-then-check implementation drains all 64
    chunks. A streaming one stops at two. That is deterministic, cheap, and
    cannot pass for the wrong reason."""
    import asyncio
    from app.routers.shares import _read_archive

    pulled = 0

    class _Stub:
        headers: dict = {}          # no Content-Length: the free check cannot fire

        async def stream(self):
            nonlocal pulled
            for _ in range(64):
                pulled += 1
                yield b"x" * (1024 * 1024)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_read_archive(_Stub(), 1024 * 1024))

    assert caught.value.status_code == 413
    assert pulled <= 3, (
        f"the whole body was read before refusing it: {pulled} of 64 MB "
        "consumed against a 1 MB limit")


def test_a_chunked_upload_with_no_content_length_is_still_413_at_the_route(client, pro_user):
    """The route-level half. This one asserts the OUTCOME, which the test
    above deliberately does not, so between them the status and the read
    are both pinned."""
    from app.main import app
    tiers = app.state.remote_configs.setdefault("tiers", {}).setdefault("tiers", {})
    fd = tiers.setdefault("pro", {}).setdefault("feature_definitions", {})
    original = fd.get("share")
    fd["share"] = {**(original or {}), "max_archive_mb": 1}

    def chunks():
        for _ in range(6):
            yield b"x" * (512 * 1024)

    try:
        r = client.post("/v1/shares", content=chunks(),
                        headers={**pro_user["headers"], **HDRS})
        assert r.status_code == 413, r.text
        assert r.json()["detail"]["code"] == "share_too_large"
    finally:
        if original is None:
            fd.pop("share", None)
        else:
            fd["share"] = original


def test_the_absolute_ceiling_applies_when_no_tier_dial_is_set(client, pro_user):
    """The default state. Scott ruled no product cap, and that ruling is
    about what a plan may share, not about how much memory a stranger with a
    token may spend. The ceiling is separate, not configurable, and its
    refusal says something DIFFERENT, because "share a shorter meeting" is a
    lie about what went wrong when the upload is a quarter of a gigabyte."""
    from app.routers import shares as shares_router
    original = shares_router.ABSOLUTE_MAX_ARCHIVE_BYTES
    shares_router.ABSOLUTE_MAX_ARCHIVE_BYTES = 512 * 1024
    try:
        r = _post_archive(client, pro_user, b"y" * (1024 * 1024))
        assert r.status_code == 413, r.text
        d = r.json()["detail"]
        assert d["code"] == "share_archive_rejected", d
        assert "shorter meeting" not in d["message"]
        assert d["limit_bytes"] == 512 * 1024
    finally:
        shares_router.ABSOLUTE_MAX_ARCHIVE_BYTES = original


def test_the_ceiling_is_far_above_any_real_bundle(client, pro_user):
    """The bound has to let the real thing through, which is the half of a
    limit people forget to assert. SS's largest measured bundle is 36.9 MB
    and audio runs about 14.4 MB an hour, so a six hour meeting is near
    90 MB."""
    assert ABSOLUTE_MAX_ARCHIVE_BYTES > 100 * 1024 * 1024
    r = _post_archive(client, pro_user, b"z" * (2 * 1024 * 1024))
    assert r.status_code == 200, r.text


def test_an_empty_body_is_still_422_not_413(client, pro_user):
    r = _post_archive(client, pro_user, b"")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "share_empty"


# --- HEAD is answered, and never counts as a view (2026-08-22) -------------
#
# Bifrost found it at the edge, CQ confirmed it from their machine, and it
# reproduced against prod: HEAD on the share routes returned 405 while GET
# returned 410. FastAPI does not add HEAD to a `.get()` route the way plain
# Starlette does, so this was 405 from the day the routes existed.
#
# Why it is worth fixing rather than shrugging at: most unfurlers send GET,
# and most of the ones that HEAD first fall back. The ones that do not
# produce an iMessage bubble or a Slack unfurl that renders EMPTY, with
# nothing in our logs for a GET that never came. It reads as a rendering
# bug for weeks and it is a method bug.

def test_head_on_a_live_share_is_200_not_405(client, pro_user):
    token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
    r = client.head(f"/s/{token}")
    assert r.status_code == 200, f"HEAD got {r.status_code}"
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers.get("x-robots-tag") == "noindex"
    assert r.content == b""


def test_head_on_the_archive_agrees_with_get_and_sends_no_body(client, pro_user):
    """Asserted AGAINST the GET rather than against a hardcoded type, which
    is the property that matters: a HEAD exists to tell a client what a GET
    would return, so any header the two disagree on is a lie. Hardcoding
    the content type here would also have pinned the fixture's rather than
    the route's."""
    token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
    head = client.head(f"/s/{token}/archive")
    get = client.get(f"/s/{token}/archive")
    assert head.status_code == get.status_code == 200
    assert head.headers["content-type"] == get.headers["content-type"]
    assert int(head.headers["content-length"]) == len(get.content) == len(ARCHIVE)
    assert head.headers.get("x-robots-tag") == get.headers.get("x-robots-tag")
    # Deliberately NOT asserting `head.content == b""`. Starlette and httpx
    # strip the body from a HEAD response at the transport layer, so that
    # assertion is true whatever the handler does: sabotaging the route to
    # read the file and return it left this test green. Fourth
    # cannot-fail test caught by sabotage today, and the fix is the same
    # each time, which is to assert the property that is actually
    # observable. See the test below.


def test_head_on_the_archive_never_reads_the_file(client, pro_user):
    """The property the body assertion could not reach, tested exactly.

    Reading tens of megabytes off disk to discard them is the obvious
    implementation of HEAD and the wrong one, and it is invisible in the
    response because the framework strips the body either way. So: remove
    the backing file. A HEAD that answers from the row still succeeds with
    the right size; one that opens the file cannot. Nothing about the
    outcome is a proxy for the behaviour here, it IS the behaviour."""
    import os
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]

    from app.services import meeting_shares as ms
    path = ms.SHARE_DIR / f"{body['share_id']}.bin"
    assert path.exists(), "fixture assumption wrong: no file to remove"
    size = path.stat().st_size
    os.remove(path)

    r = client.head(f"/s/{token}/archive")
    assert r.status_code == 200, (
        f"HEAD got {r.status_code} with the file removed, so it opened it")
    assert int(r.headers["content-length"]) == size


def test_head_on_the_aasa_is_200(client):
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = cc.get("share")
    cc["share"] = {**(original or {}), "aasa_app_ids": ["TEAMID.com.example.app"]}
    try:
        assert client.head("/.well-known/apple-app-site-association").status_code == 200
    finally:
        if original is None:
            cc.pop("share", None)
        else:
            cc["share"] = original


def test_head_on_a_dead_share_is_410_like_get(client, pro_user):
    """The no-distinction property has to hold for HEAD too, or a prober
    learns from the method which tokens were ever real."""
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]
    client.delete(f"/v1/shares/{body['share_id']}", headers=pro_user["headers"])
    assert client.head(f"/s/{token}").status_code == 410
    assert client.head(f"/s/{token}/archive").status_code == 410
    assert client.head("/s/AAAAAAAAAAAAAAAAAAAAAA").status_code == 410
    assert client.head("/s/short").status_code == 410


def test_a_head_never_counts_as_a_view(client, pro_user):
    """Not an optimisation. A HEAD is a probe by definition, so counting it
    would put exactly the fiction in view_count that the preview-fetcher
    filter exists to keep out, and it would arrive through a door that
    filter does not watch: a HEAD from an ordinary user agent passes the UA
    check and would be counted.

    Asserted as a DELTA across a real GET, so the test proves the counter
    still works rather than proving nothing by counting zero of zero."""
    body = _create(client, pro_user).json()
    token = body["url"].rsplit("/", 1)[1]

    def views():
        return client.get(f"/v1/shares/{body['share_id']}/stats",
                          headers=pro_user["headers"]).json()["view_count"]

    start = views()
    for _ in range(5):
        assert client.head(f"/s/{token}").status_code == 200
    assert views() == start, "a HEAD was counted as somebody reading the meeting"

    assert client.get(f"/s/{token}").status_code == 200
    assert views() == start + 1, "the counter stopped working, so the test above proved nothing"


def test_the_app_store_id_actually_reaches_the_page_through_the_route(client, pro_user):
    """The WIRING, which the renderer tests cannot see.

    Predicted and confirmed by sabotage: replacing the route's
    `app_store_id=settings["app_store_id"]` with None left all 91 renderer
    and share tests green. The renderer was well covered and the wiring
    between config and renderer was covered by nothing, which is the same
    seam the percent-decode sat in earlier the same day: a function tested
    directly proves nothing about whether the route calls it.

    Driven through POST /v1/shares and GET /s/{token} with the dial set,
    so this fails if the config key is renamed, the route stops reading it,
    or the renderer stops receiving it."""
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = cc.get("share")
    cc["share"] = {**(original or {}), "app_store_id": "6760098225",
                   "host": "https://share.example.com"}
    try:
        token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
        page = client.get(f"/s/{token}").text
        assert "app-id=6760098225" in page, "the configured id never reached the page"
        assert f"app-argument=https://share.example.com/s/{token}" in page, (
            "the banner points the app at the wrong URL")
        assert "https://apps.apple.com/app/id6760098225" in page
    finally:
        if original is None:
            cc.pop("share", None)
        else:
            cc["share"] = original


def test_no_configured_id_leaves_the_page_without_a_store_route(client, pro_user):
    """The other half of the wiring: the dial being absent has to reach the
    page as absence, not as a broken link."""
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = cc.get("share")
    cc["share"] = {k: v for k, v in (original or {}).items() if k != "app_store_id"}
    try:
        token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
        page = client.get(f"/s/{token}").text
        assert "apple-itunes-app" not in page
        assert "apps.apple.com" not in page
    finally:
        if original is None:
            cc.pop("share", None)
        else:
            cc["share"] = original


# --- The unfurl image (2026-08-23) -----------------------------------------
#
# Scott's first real share, screenshot in hand: the iMessage bubble showed
# the title and the domain with a generic Safari compass where the app
# icon should be. The page served og:title and og:description and no
# og:image, so the messenger fell back. "Doesn't look very polished."
#
# Two images served by GP on the share origin, by NAME from an allowlist
# rather than a StaticFiles mount so there is nothing to walk, and pointed
# at from the page. Dials, defaulting to the share origin's own assets so
# a bare config still produces a bubble with the mark on it.

def test_the_two_share_assets_are_served_with_the_right_type_and_cache(client):
    for name in ("card-1200x630.png", "icon-512.png"):
        r = client.get(f"/share-assets/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
        assert "max-age" in r.headers.get("cache-control", "")
        assert client.head(f"/share-assets/{name}").status_code == 200


def test_the_card_is_actually_1200_by_630():
    """The page declares og:image:width/height; the bytes must agree or the
    messenger lays out a box the image does not fill."""
    import struct
    data = open("app/static/share/card-1200x630.png", "rb").read()
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (1200, 630)


def test_a_real_file_in_the_directory_is_not_served_unless_named(client):
    """The allowlist, tested as an allowlist.

    The first version of this test tried `../admin.html` and passed, and
    sabotage showed it passed for the wrong reason: Starlette decodes the
    path before routing and `{name}` cannot contain a slash, so traversal
    never reaches the handler at all. Good, but it proves nothing about
    the allowlist. So: put a REAL file in the served directory that is not
    on the list, and assert it still 404s. Removing the allowlist turns
    exactly this red and nothing else."""
    import os
    stray = "app/static/share/not-on-the-list.png"
    with open(stray, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    try:
        r = client.get("/share-assets/not-on-the-list.png")
        assert r.status_code == 404, "a file in the directory was served by existing, not by being named"
    finally:
        os.remove(stray)


@pytest.mark.parametrize("bad", ["../admin.html", "admin.html", "card-1200x630.png.bak", "", "x"])
def test_nothing_outside_the_directory_or_list_is_served(client, bad):
    from urllib.parse import quote
    r = client.get(f"/share-assets/{quote(bad, safe='')}")
    assert r.status_code == 404, f"{bad!r} got {r.status_code}"


def test_the_page_carries_the_image_tags_and_the_large_card_type(client, pro_user):
    # The static-mark og:image path (dynamic_card OFF). The per-share card
    # path is covered in tests/test_share_card.py.
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    _orig = cc.get("share")
    cc["share"] = {**(_orig or {}), "dynamic_card": False}
    try:
        token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
        page = client.get(f"/s/{token}").text
    finally:
        if _orig is None:
            cc.pop("share", None)
        else:
            cc["share"] = _orig
    import html.parser

    class P(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(); self.meta = {}; self.links = {}
        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "meta" and "content" in d:
                k = d.get("property") or d.get("name")
                if k: self.meta[k] = d["content"]
            if tag == "link" and d.get("rel") == "apple-touch-icon":
                self.links["apple-touch-icon"] = d.get("href")

    p = P(); p.feed(page)
    og = p.meta.get("og:image")
    assert og and og.endswith("/share-assets/card-1200x630.png"), og
    assert og.startswith("https://"), "absolute URL or the messenger cannot fetch it"
    assert p.meta.get("twitter:image") == og
    assert p.meta.get("og:image:width") == "1200" and p.meta.get("og:image:height") == "630"
    assert p.meta.get("twitter:card") == "summary_large_image"
    assert p.links.get("apple-touch-icon", "").endswith("/share-assets/icon-512.png")


def test_the_image_url_is_a_dial(client, pro_user):
    """A CDN or a redesign must not need a deploy."""
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = cc.get("share")
    cc["share"] = {**(original or {}), "og_image_url": "https://cdn.example.com/new-card.png", "dynamic_card": False}
    try:
        token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
        html = client.get(f"/s/{token}").text
        assert "https://cdn.example.com/new-card.png" in html
        assert "card-1200x630.png" not in html
    finally:
        if original is None: cc.pop("share", None)
        else: cc["share"] = original


def test_the_image_url_defaults_to_the_share_origin(client, pro_user):
    """Pointed at the configured share host, not the API host the page was
    fetched through, so the messenger fetches one origin and the value is
    right on whichever hostname served the page."""
    from app.main import app
    cc = app.state.remote_configs.setdefault("client-config", {})
    original = cc.get("share")
    cc["share"] = {k: v for k, v in (original or {}).items() if k not in ("og_image_url", "icon_url")}
    cc["share"]["host"] = "https://share.example.com"
    cc["share"]["dynamic_card"] = False
    try:
        token = _create(client, pro_user).json()["url"].rsplit("/", 1)[1]
        html = client.get(f"/s/{token}").text
        assert "https://share.example.com/share-assets/card-1200x630.png" in html
        assert "https://share.example.com/share-assets/icon-512.png" in html
    finally:
        if original is None: cc.pop("share", None)
        else: cc["share"] = original


# --- A failed summary is never a title (2026-08-23) -------------------------
#
# Scott's bubble read "Summary: Unable to provide meaningful meeting summary
# / share.shouldersurf.com". The client had stored that failure string in
# BOTH title and summary_line, and the page rendered exactly what it was
# given. SS has since made their title refuse failure text; the rule lives
# here too, at render time, because rows already stored still carry it,
# older builds still send it, and a rule in one client is only sometimes
# true. Ruling: og:title is the meeting title; og:description is the
# summary only when a real one exists, otherwise date and duration.

from app.services.meeting_shares import card_text  # noqa: E402


class _Row(dict):
    """aiosqlite.Row lookalike: missing keys read as None, not KeyError."""
    def __getitem__(self, k):
        return dict.get(self, k)


def test_a_failed_summary_in_the_title_becomes_the_date():
    title, desc = card_text(_Row(
        title="Summary: Unable to provide meaningful meeting summary",
        summary_line="**Summary: Unable to provide meaningful meeting summary**",
        meeting_date="2026-08-22T23:04:05-05:00", duration_seconds=93))
    assert "Unable" not in title and "Unable" not in desc
    assert title == "Aug 22, 2026 at 11:04 PM", title
    assert desc == "Aug 22, 2026 at 11:04 PM · 2 min", desc


def test_the_date_keeps_the_senders_own_offset():
    """23:04 at UTC-5 is 04:04 the NEXT day in UTC. The card must say the
    22nd, which is the day it was for the person who recorded it. Same
    lesson as the report header, on a different surface."""
    title, _ = card_text(_Row(title=None, summary_line=None,
                              meeting_date="2026-08-22T23:04:05-05:00", duration_seconds=60))
    assert title.startswith("Aug 22, 2026")
    assert "Aug 23" not in title


def test_a_real_title_and_summary_pass_through_cleaned():
    title, desc = card_text(_Row(
        title="Northwind rollout, week 3",
        summary_line="**Summary:** Agreed to hold the pilot at two sites.",
        meeting_date="2026-08-21", duration_seconds=2743))
    assert title == "Northwind rollout, week 3"
    assert desc == "Agreed to hold the pilot at two sites.", desc


def test_nothing_at_all_still_yields_a_title_and_an_empty_description():
    """An empty og:title makes some fetchers show the URL as the title; an
    empty description renders as nothing, which is correct. A fabricated
    description is not."""
    title, desc = card_text(_Row(title=None, summary_line=None, meeting_date=None, duration_seconds=None))
    assert title == "Shared meeting"
    assert desc == ""


def test_a_display_string_date_is_kept_as_sent():
    """SS sometimes sends the date already formatted. Do not re-parse it
    into something else."""
    title, _ = card_text(_Row(title="", summary_line="", meeting_date="Aug 23, 2026 at 2:40 AM", duration_seconds=None))
    assert title == "Aug 23, 2026 at 2:40 AM"


def test_the_rule_is_applied_on_the_served_page_for_a_stored_row(client, pro_user):
    """Through the route, for a share whose stored row carries the failure
    string in both fields, which is exactly what Scott's real share holds."""
    h = {**pro_user["headers"], **HDRS,
         "X-Share-Title": quote("Summary: Unable to provide meaningful meeting summary"),
         "X-Share-Summary-Line": quote("**Summary: Unable to provide meaningful meeting summary**"),
         "X-Share-Date": "2026-08-22T23:04:05-05:00", "X-Share-Duration-Seconds": "93"}
    r = client.post("/v1/shares", content=ARCHIVE, headers=h)
    assert r.status_code == 200, r.text
    url = r.json()["url"]; token = url.rsplit("/", 1)[1]
    page = client.get(f"/s/{token}").text
    head = page.split("<body", 1)[0]
    assert "Unable to provide" not in head, "the failure string reached a recipient-visible tag"
    assert "<title>Aug 22, 2026 at 11:04 PM</title>" in head
    assert "og:description' content='Aug 22, 2026 at 11:04 PM · 2 min'" in head
    assert f"og:url' content='{url}'" in head
    assert "<script" not in head.lower(), "iMessage's fetcher runs no scripts; the tags must be static"
