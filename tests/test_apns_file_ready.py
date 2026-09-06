"""APNs "your file is ready": the token store, the payload, and Apple's
two answers that mean something about the row.

Built on the real schema through init_db, so the migration is exercised.
"""

import json
import os
import tempfile

import pytest

from app.services import apns, device_tokens as dt


class _Settings:
    def __init__(self, key="", kid="k1", team="T1", db=""):
        self.apns_private_key_b64 = key
        self.apns_key_id = kid
        self.apns_team_id = team
        self.database_url = db


class _Resp:
    """Stands in for an httpx.Response, INCLUDING its headers.

    It had no `headers` until the per-send log line needed `apns-id`,
    which is the header that proves Apple took the notification. A fake
    that is missing an attribute the real object always has does not test
    the code, it tests the fake; this one failed loudly, which is the
    good version of that.
    """

    def __init__(self, status, reason=None, apns_id="8A8E5C1B-0000-TEST"):
        self.status_code = status
        self._reason = reason
        self.headers = {"apns-id": apns_id} if apns_id else {}

    def json(self):
        return {"reason": self._reason} if self._reason else {}


class _Client:
    """Records every call and answers from a scripted list."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def post(self, url, headers=None, content=None):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(content)})
        return self.answers.pop(0) if self.answers else _Resp(200)


async def _db():
    import aiosqlite
    from app.database import init_db
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    await init_db(f"sqlite+aiosqlite:///{path}")
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    return db


# --- the token store --------------------------------------------------------

@pytest.mark.asyncio
async def test_the_migration_creates_the_table_keyed_by_token_alone():
    db = await _db()
    cols = {r[1]: r for r in await (await db.execute("PRAGMA table_info(device_tokens)")).fetchall()}
    assert set(cols) == {"device_token", "user_id", "app_id", "environment", "bundle_id",
                         "app_build", "created_at", "last_seen_at"}
    assert cols["device_token"][5] == 1, "device_token is the primary key"
    assert cols["user_id"][5] == 0, "user_id is NOT part of the key"
    await db.close()


@pytest.mark.asyncio
async def test_a_token_is_app_scoped_for_account_deletion():
    """Deleting the Shoulder Surf account must not retire the same phone's
    N-400 push token, and a token left behind could still take that app's
    pushes. The account-deletion schema pin caught this table unclassified,
    which is what it is for."""
    from app.services.account_deletion import (
        ACCOUNT_TABLES, APP_OWNED_TABLES, APP_SCOPED_TABLES,
    )
    assert "device_tokens" in APP_SCOPED_TABLES
    assert "device_tokens" not in ACCOUNT_TABLES
    assert not any("device_tokens" in v for v in APP_OWNED_TABLES.values())
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="production",
                      bundle_id="com.ss", app_id="shouldersurf")
    assert (await dt.for_user(db, "u1"))[0]["app_id"] == "shouldersurf"
    await db.close()


@pytest.mark.asyncio
async def test_a_token_that_moves_to_another_user_replaces_the_row():
    """SS's own rule, and the reason the key is the token alone: one phone
    can never be two rows taking two pushes."""
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="sandbox",
                      bundle_id="com.ss", app_build="1")
    await dt.register(db, user_id="u2", device_token="abc", environment="production",
                      bundle_id="com.ss", app_build="2")
    assert await dt.for_user(db, "u1") == []
    rows = await dt.for_user(db, "u2")
    assert len(rows) == 1 and rows[0]["environment"] == "production" and rows[0]["app_build"] == "2"
    await db.close()


@pytest.mark.asyncio
async def test_the_per_user_cap_prunes_the_oldest_and_keeps_the_newest():
    db = await _db()
    for i in range(dt.MAX_TOKENS_PER_USER + 3):
        await dt.register(db, user_id="u1", device_token=f"t{i:02d}", environment="production",
                          bundle_id="com.ss")
    rows = await dt.for_user(db, "u1")
    assert len(rows) == dt.MAX_TOKENS_PER_USER
    assert rows[0]["device_token"] == "t12"
    assert not any(r["device_token"] in ("t00", "t01", "t02") for r in rows)
    await db.close()


@pytest.mark.asyncio
async def test_unregister_is_owner_scoped_and_forget_is_not():
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="production", bundle_id="com.ss")
    await dt.unregister(db, user_id="u2", device_token="abc")      # not yours
    assert len(await dt.for_user(db, "u1")) == 1
    await dt.unregister(db, user_id="u1", device_token="abc")
    assert await dt.for_user(db, "u1") == []
    await dt.register(db, user_id="u1", device_token="abc", environment="production", bundle_id="com.ss")
    await dt.forget(db, "abc")                                     # Apple said 410
    assert await dt.for_user(db, "u1") == []
    await db.close()


# --- the payload ------------------------------------------------------------

def test_the_alert_uses_loc_keys_so_ios_localizes_it():
    p = apns.build_payload(generation_id="g1", project_id="p1", meeting_id=None,
                           session_id="s1", file_name=None)
    assert p["aps"]["alert"] == {"title-loc-key": apns.TITLE_LOC_KEY, "loc-key": apns.BODY_LOC_KEY}
    assert p["generation_id"] == "g1" and p["project_id"] == "p1" and p["session_id"] == "s1"
    assert "meeting_id" not in p, "absent context keys are omitted, not sent null"


def test_a_file_name_rides_as_a_plain_body_because_it_is_not_translatable():
    p = apns.build_payload(generation_id="g1", project_id=None, meeting_id=None,
                           session_id=None, file_name="abm_exec_briefing_090126.docx")
    assert p["aps"]["alert"]["body"] == "abm_exec_briefing_090126.docx"
    assert "loc-key" not in p["aps"]["alert"]
    assert p["aps"]["alert"]["title-loc-key"] == apns.TITLE_LOC_KEY


# --- Apple's answers --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_410_retires_the_token(monkeypatch):
    monkeypatch.setattr(apns, "provider_token", lambda s: "tok")
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="production", bundle_id="com.ss")
    row = (await dt.for_user(db, "u1"))[0]
    c = _Client(_Resp(410, "Unregistered"))
    out = await apns.send_to_token(c, db, row=row, settings=_Settings(key="x"),
                                   payload={"aps": {}}, expiration=1, collapse_id="g1")
    assert out == "unregistered" and await dt.for_user(db, "u1") == []
    await db.close()


@pytest.mark.asyncio
async def test_bad_device_token_retries_the_other_host_and_corrects_the_row(monkeypatch):
    """A phone that moves between an Xcode install and TestFlight gets a
    different token in each; the stale one is otherwise indistinguishable."""
    monkeypatch.setattr(apns, "provider_token", lambda s: "tok")
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="production", bundle_id="com.ss")
    row = (await dt.for_user(db, "u1"))[0]
    c = _Client(_Resp(400, "BadDeviceToken"), _Resp(200))
    out = await apns.send_to_token(c, db, row=row, settings=_Settings(key="x"),
                                   payload={"aps": {}}, expiration=1, collapse_id="g1")
    assert out == "sent_after_environment_correction_to_sandbox"
    assert (await dt.for_user(db, "u1"))[0]["environment"] == "sandbox"
    assert c.calls[0]["url"].startswith(apns.HOSTS["production"])
    assert c.calls[1]["url"].startswith(apns.HOSTS["sandbox"])
    await db.close()


@pytest.mark.asyncio
async def test_the_headers_carry_the_rows_topic_and_the_generation_as_collapse_id(monkeypatch):
    monkeypatch.setattr(apns, "provider_token", lambda s: "tok")
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="sandbox",
                      bundle_id="com.weirtech.n400helper")
    row = (await dt.for_user(db, "u1"))[0]
    c = _Client(_Resp(200))
    await apns.send_to_token(c, db, row=row, settings=_Settings(key="x"),
                             payload={"aps": {}}, expiration=1757000000, collapse_id="gen-abc")
    h = c.calls[0]["headers"]
    assert h["apns-topic"] == "com.weirtech.n400helper", "topic comes from the row, not a constant"
    assert h["apns-collapse-id"] == "gen-abc" and h["apns-expiration"] == "1757000000"
    assert h["apns-push-type"] == "alert" and h["apns-priority"] == "10"
    await db.close()


# --- the send decision ------------------------------------------------------

@pytest.mark.asyncio
async def test_dormant_while_the_key_is_blank():
    db = await _db()
    out = await apns.notify_generation_done(db, settings=_Settings(key=""), user_id="u1",
                                            generation_id="g1")
    assert out["skipped"] == "apns_not_configured" and out["sent"] == 0
    assert apns.configured(_Settings(key="")) is False
    assert apns.configured(_Settings(key="x")) is True
    await db.close()


@pytest.mark.asyncio
async def test_an_acked_row_is_not_pushed_and_the_check_happens_at_send_time():
    """The client can ack in the seconds between the file landing and this
    running, so acked_at is read HERE, not when the task was scheduled."""
    from app.services import generation_turns as gt
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc", environment="production", bundle_id="com.ss")
    gt.begin("u1", "g1")
    await gt.record_start(db, user_id="u1", app_id="ss", generation_id="g1", project_id="p1")
    await gt.finish(db, user_id="u1", app_id="ss", generation_id="g1", status="done", text="t")
    await gt.ack(db, "u1", "g1")                       # the ack lands first
    out = await apns.notify_generation_done(db, settings=_Settings(key="x"), user_id="u1",
                                            generation_id="g1")
    assert out["skipped"] == "already_acked" and out["sent"] == 0
    await db.close()


@pytest.mark.asyncio
async def test_a_failed_row_and_a_user_with_no_devices_are_both_skipped():
    from app.services import generation_turns as gt
    db = await _db()
    gt.begin("u1", "g2")
    await gt.record_start(db, user_id="u1", app_id="ss", generation_id="g2", project_id="p1")
    await gt.finish(db, user_id="u1", app_id="ss", generation_id="g2", status="failed",
                    error={"code": "x"})
    assert (await apns.notify_generation_done(db, settings=_Settings(key="x"), user_id="u1",
                                              generation_id="g2"))["skipped"] == "not_a_done_row"
    gt.begin("u1", "g3")
    await gt.record_start(db, user_id="u1", app_id="ss", generation_id="g3", project_id="p1")
    await gt.finish(db, user_id="u1", app_id="ss", generation_id="g3", status="done", text="t")
    assert (await apns.notify_generation_done(db, settings=_Settings(key="x"), user_id="u1",
                                              generation_id="g3"))["skipped"] == "no_registered_devices"
    await db.close()


# --- wiring -----------------------------------------------------------------

def test_the_routes_are_mounted_and_the_send_is_not_inside_finish():
    main = open("app/main.py").read()
    assert 'devices_router.router, prefix="/v1"' in main
    gt = open("app/services/generation_turns.py").read()
    assert "apns" not in gt, "finish() stays a pure row write"
    chat = open("app/routers/chat.py").read()
    i = chat.index("_push_file_ready")
    assert chat.index('status="done", text=(response.text or "")') < i
    assert "asyncio.create_task(_push_file_ready(" in chat


def test_h2_is_pinned_because_apns_is_http2_only():
    """It was in the local venv and ABSENT from the prod image, which is
    the shape that passes every local test and fails on the first push."""
    assert "h2==" in open("requirements.txt").read()


@pytest.mark.asyncio
async def test_a_response_without_headers_still_sends(monkeypatch):
    """A push must never fail because a log line could not read a header.
    `apns-id` is diagnostic, not load-bearing, and Apple has returned
    sparse responses before."""
    monkeypatch.setattr(apns, "provider_token", lambda s: "tok")
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc",
                      environment="sandbox", bundle_id="com.ss")
    row = (await dt.for_user(db, "u1"))[0]

    class _Bare:
        status_code = 200

        def json(self):
            return {}

    out = await apns.send_to_token(_Client(_Bare()), db, row=row,
                                   settings=_Settings(key="x"),
                                   payload={"aps": {}}, expiration=1,
                                   collapse_id="g1")
    assert out == "sent"
    await db.close()


# --- the production path, which no real device has exercised yet ------------
#
# Everything sent on 2026-09-06 was SANDBOX, because every install so far has
# come from Xcode. A production token appears only from TestFlight or the App
# Store, and that path runs code neither team has run: SS's profile read
# returning `production` (an App Store build embeds no provisioning profile
# at all, so it takes a different branch from the one that ran), and GP's
# production host. The key covers both environments so the path is
# survivable, but survivable is not proved. These pin GP's half.

@pytest.mark.parametrize("environment,expected_host", [
    ("production", "https://api.push.apple.com"),
    ("sandbox", "https://api.sandbox.push.apple.com"),
])
@pytest.mark.asyncio
async def test_the_row_environment_picks_the_host_on_the_FIRST_attempt(
        monkeypatch, environment, expected_host):
    """Not via the retry. The retry path was already covered; the plain
    path for a production row was not, and it is the one a TestFlight
    build will take."""
    monkeypatch.setattr(apns, "provider_token", lambda s: "tok")
    db = await _db()
    await dt.register(db, user_id="u1", device_token="abc",
                      environment=environment, bundle_id="com.ss")
    row = (await dt.for_user(db, "u1"))[0]
    c = _Client(_Resp(200))
    out = await apns.send_to_token(c, db, row=row, settings=_Settings(key="x"),
                                   payload={"aps": {}}, expiration=1, collapse_id="g1")
    assert out == "sent", "a plain send must not go through the retry"
    assert len(c.calls) == 1, "exactly one request; a second means it retried"
    assert c.calls[0]["url"].startswith(expected_host), (
        f"{environment} row went to {c.calls[0]['url']}, not {expected_host}")
    await db.close()


def test_the_two_apple_hosts_are_spelled_correctly():
    """A typo here is invisible until a real device fails, and it would
    fail only in the environment that was not tested. Apple's hostnames,
    pinned literally."""
    assert apns.HOSTS == {
        "production": "https://api.push.apple.com",
        "sandbox": "https://api.sandbox.push.apple.com",
    }


def test_other_environment_flips_both_ways():
    """The correction depends on this being an involution."""
    assert dt.other_environment("sandbox") == "production"
    assert dt.other_environment("production") == "sandbox"
    # An unknown or missing value must resolve to production, matching the
    # send path's own default, or a corrupt row would retry against itself.
    assert dt.other_environment(None) == "production"
    assert dt.other_environment("") == "production"
    assert dt.other_environment("nonsense") == "production"
