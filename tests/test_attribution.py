"""Apple Ads install attribution: POST /v1/attribution + exchange sweep.

Ingest is anonymous-friendly and upserts on (device_id, app_id); the
authenticated token-less call links the device row to a user. The exchange
runs only in the sweep (app/services/apple_ads_attribution.py); HTTP is
patched at _post_token, the single Apple touchpoint.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _uuid() -> str:
    return str(uuid.uuid4())


def _post(client, device_id, token="tok-abc123", headers=None, **over):
    body = {"device_id": device_id, "app_version": "1.1"}
    if token is not None:
        body["attribution_token"] = token
    body.update(over)
    h = {"X-App-ID": "shouldersurf"}
    if headers:
        h.update(headers)
    return client.post("/v1/attribution", json=body, headers=h)


def _row(db_path, device_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ad_attribution WHERE device_id = ?", (device_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def test_ingest_creates_pending_row(client, tmp_db_path):
    dev = _uuid()
    r = _post(client, dev)
    assert r.status_code == 202
    assert r.json() == {"status": "received"}
    row = _row(tmp_db_path, dev)
    assert row["status"] == "pending"
    assert row["token"] == "tok-abc123"
    assert row["app_id"] == "shouldersurf"
    assert row["user_id"] is None
    assert row["app_version"] == "1.1"


def test_authed_first_call_sets_user(client, tmp_db_path, free_user):
    dev = _uuid()
    r = _post(client, dev, headers=free_user["headers"])
    assert r.status_code == 202
    assert _row(tmp_db_path, dev)["user_id"] == free_user["user_id"]


def test_link_call_attaches_user_to_anonymous_row(client, tmp_db_path, free_user):
    dev = _uuid()
    _post(client, dev)  # anonymous, token
    r = _post(client, dev, token=None, headers=free_user["headers"])  # link form
    assert r.status_code == 202
    row = _row(tmp_db_path, dev)
    assert row["user_id"] == free_user["user_id"]
    assert row["status"] == "pending"  # link call must not disturb the exchange
    assert row["token"] == "tok-abc123"


def test_upsert_no_duplicate_rows(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    _post(client, dev)
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM ad_attribution WHERE device_id = ?", (dev,)
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_link_only_call_creates_no_token_row(client, tmp_db_path, free_user):
    dev = _uuid()
    r = _post(client, dev, token=None, headers=free_user["headers"])
    assert r.status_code == 202
    row = _row(tmp_db_path, dev)
    assert row["status"] == "no_token"
    assert row["user_id"] == free_user["user_id"]


def test_invalid_device_id_rejected(client):
    r = _post(client, "not-a-uuid")
    assert r.status_code == 400


def test_completed_exchange_not_overwritten_by_new_token(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', token=NULL,"
        " campaign_id=111 WHERE device_id=?",
        (dev,),
    )
    conn.commit()
    conn.close()
    _post(client, dev, token="tok-later")
    row = _row(tmp_db_path, dev)
    assert row["status"] == "attributed"
    assert row["campaign_id"] == 111
    assert row["token"] is None


# ---------------------------------------------------------------------------
# Exchange sweep
# ---------------------------------------------------------------------------


async def _sweep(tmp_db_path):
    import aiosqlite

    from app.services.apple_ads_attribution import sweep_pending

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        return await sweep_pending(db)


_ATTRIBUTED_PAYLOAD = {
    "attribution": True,
    "orgId": 40669820,
    "campaignId": 542370539,
    "conversionType": "Download",
    "clickDate": "2026-07-21T10:01Z",
    "adGroupId": 542317095,
    "countryOrRegion": "US",
    "keywordId": 87675432,
    "adId": 542317136,
}


@pytest.mark.asyncio
async def test_sweep_persists_attributed_payload(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(200, _ATTRIBUTED_PAYLOAD),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["attributed"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "attributed"
    assert row["campaign_id"] == 542370539
    assert row["ad_group_id"] == 542317095
    assert row["keyword_id"] == 87675432
    assert row["conversion_type"] == "Download"
    assert row["country_or_region"] == "US"
    assert row["standard_payload"] == 0
    assert row["token"] is None
    assert row["exchanged_at"] is not None


@pytest.mark.asyncio
async def test_sweep_marks_organic(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(200, {"attribution": False}),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["organic"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "organic"
    assert row["attribution"] == 0
    assert row["token"] is None


@pytest.mark.asyncio
async def test_sweep_flags_standard_payload(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    placeholder = {k: (1234567890 if isinstance(v, int) else v)
                   for k, v in _ATTRIBUTED_PAYLOAD.items()}
    placeholder["attribution"] = True
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(200, placeholder),
    ):
        await _sweep(tmp_db_path)
    row = _row(tmp_db_path, dev)
    assert row["status"] == "attributed"
    assert row["standard_payload"] == 1


@pytest.mark.asyncio
async def test_sweep_retries_on_404(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(404, None),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["pending"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "pending"
    assert row["token"] == "tok-abc123"  # kept for the next sweep


@pytest.mark.asyncio
async def test_sweep_errors_on_400(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(400, None),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["error"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "error"
    assert row["token"] is None


@pytest.mark.asyncio
async def test_sweep_expires_past_ttl(client, tmp_db_path):
    dev = _uuid()
    _post(client, dev)
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET created_at=? WHERE device_id=?", (stale, dev)
    )
    conn.commit()
    conn.close()
    exchange = AsyncMock(return_value=(200, _ATTRIBUTED_PAYLOAD))
    with patch("app.services.apple_ads_attribution._post_token", exchange):
        counts = await _sweep(tmp_db_path)
    assert counts["expired"] == 1
    exchange.assert_not_awaited()  # expired rows never hit Apple
    row = _row(tmp_db_path, dev)
    assert row["status"] == "expired"
    assert row["token"] is None


# ---------------------------------------------------------------------------
# Admin report
# ---------------------------------------------------------------------------


def test_admin_acquisition_report(client, tmp_db_path, free_user):
    dev_attr, dev_org, dev_pending = _uuid(), _uuid(), _uuid()
    _post(client, dev_attr, headers=free_user["headers"])
    _post(client, dev_org)
    _post(client, dev_pending)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', attribution=1,"
        " campaign_id=542370539, keyword_id=87675432, token=NULL"
        " WHERE device_id=?",
        (dev_attr,),
    )
    conn.execute(
        "UPDATE ad_attribution SET status='organic', attribution=0, token=NULL"
        " WHERE device_id=?",
        (dev_org,),
    )
    conn.execute(
        "UPDATE users SET ever_subscribed=1 WHERE id=?", (free_user["user_id"],)
    )
    conn.commit()
    conn.close()

    r = client.get(
        "/webhooks/admin/acquisition?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["kpis"]["total"] == 3
    assert d["kpis"]["attributed"] == 1
    assert d["kpis"]["organic"] == 1
    assert d["kpis"]["pending"] == 1
    assert d["kpis"]["linked"] == 1
    assert d["kpis"]["started"] == 1
    assert d["campaigns"][0]["campaign_id"] == 542370539
    assert d["campaigns"][0]["installs"] == 1
    assert d["campaigns"][0]["started"] == 1
    assert d["keywords"][0]["keyword_id"] == 87675432


def _attributed_linked_device(client, tmp_db_path, user):
    """One attributed, keyword-level install linked to `user`."""
    dev = _uuid()
    _post(client, dev, headers=user["headers"])
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', attribution=1,"
        " campaign_id=542370539, keyword_id=87675432, token=NULL"
        " WHERE device_id=?",
        (dev,),
    )
    conn.commit()
    conn.close()
    return dev


def _acq(client):
    r = client.get(
        "/webhooks/admin/acquisition?days=30",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert r.status_code == 200
    return r.json()


def _exec(db_path, sql, args):
    conn = sqlite3.connect(db_path)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def test_lapsed_trial_counts_as_started_but_never_trialing_or_paid(
    client, tmp_db_path, free_user
):
    """THE case the split exists for.

    A seven-day trial that lapsed: Apple wrote a `subscribed` event at trial
    START, so ever_subscribed is 1 and sticky. The downgrade cleared is_trial
    to 0, and no renewal was ever billed. Counting this as a subscriber
    flatters the keyword that produced it, which is the direction nobody
    catches by eye.
    """
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='free' WHERE id=?",
        (free_user["user_id"],),
    )

    d = _acq(client)
    assert d["kpis"]["started"] == 1, "trial start is a subscription record"
    assert d["kpis"]["trialing"] == 0, "the trial lapsed; not trialing now"
    assert d["kpis"]["paid"] == 0, "never billed a renewal; NOT a customer"
    assert d["keywords"][0]["started"] == 1
    assert d["keywords"][0]["trialing"] == 0
    assert d["keywords"][0]["paid"] == 0
    assert d["campaigns"][0]["paid"] == 0


def test_current_trial_counts_as_trialing_not_paid(client, tmp_db_path, free_user):
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=1, tier='pro' WHERE id=?",
        (free_user["user_id"],),
    )

    d = _acq(client)
    assert d["kpis"]["started"] == 1
    assert d["kpis"]["trialing"] == 1
    assert d["kpis"]["paid"] == 0, "a trial in progress has not been billed"
    assert d["keywords"][0]["trialing"] == 1
    assert d["keywords"][0]["paid"] == 0


def test_renewed_user_counts_as_paid(client, tmp_db_path, free_user):
    """A renewal is the only signal here that means money moved."""
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='pro' WHERE id=?",
        (free_user["user_id"],),
    )
    _exec(
        tmp_db_path,
        "INSERT INTO subscription_events (id, user_id, event_type, source,"
        " effective_at, recorded_at) VALUES (?,?,'renewed','assn',?,?)",
        (_uuid(), free_user["user_id"], "2026-09-08T00:00:00+00:00",
         "2026-09-08T00:00:00+00:00"),
    )

    d = _acq(client)
    assert d["kpis"]["paid"] == 1
    assert d["kpis"]["trialing"] == 0
    assert d["keywords"][0]["paid"] == 1
    assert d["campaigns"][0]["paid"] == 1


def test_subscribed_event_alone_is_not_paid(client, tmp_db_path, free_user):
    """The trap, pinned: a `subscribed` row is written at TRIAL START too, so
    it must not qualify anyone as paid on its own. If this test goes green
    after someone adds 'subscribed' to the paid event set, the lapsed-trial
    case above silently starts counting as a customer again."""
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='free' WHERE id=?",
        (free_user["user_id"],),
    )
    _exec(
        tmp_db_path,
        "INSERT INTO subscription_events (id, user_id, event_type, source,"
        " effective_at, recorded_at) VALUES (?,?,'subscribed','assn',?,?)",
        (_uuid(), free_user["user_id"], "2026-09-01T00:00:00+00:00",
         "2026-09-01T00:00:00+00:00"),
    )

    d = _acq(client)
    assert d["kpis"]["started"] == 1
    assert d["kpis"]["paid"] == 0, "a `subscribed` row is a trial start too"


def test_outright_purchase_is_counted_as_paid_unconfirmed(
    client, tmp_db_path, free_user
):
    """The documented undercount MEASURES ITSELF rather than staying a caveat.

    Someone on a paid tier, not trialing, with no renewal row yet is either an
    outright purchase inside its first period or a conversion whose renewal is
    not recorded. `paid` cannot claim them and must not; `paid_unconfirmed`
    counts them so the size of the gap is observable. If this stays 0 in
    production the undercount never fires and no App Store Connect answer is
    needed to close the question.
    """
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='pro' WHERE id=?",
        (free_user["user_id"],),
    )

    d = _acq(client)
    assert d["kpis"]["paid"] == 0, "no renewal, so no proof money moved"
    assert d["kpis"]["paid_unconfirmed"] == 1, "but it is VISIBLE, not dropped"
    assert d["kpis"]["trialing"] == 0
    assert d["keywords"][0]["paid_unconfirmed"] == 1
    assert d["campaigns"][0]["paid_unconfirmed"] == 1


def test_renewal_moves_a_user_out_of_paid_unconfirmed(
    client, tmp_db_path, free_user
):
    """The two columns must not double-count: once a renewal lands the user is
    `paid` and must leave the ambiguous bucket entirely."""
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='pro' WHERE id=?",
        (free_user["user_id"],),
    )
    _exec(
        tmp_db_path,
        "INSERT INTO subscription_events (id, user_id, event_type, source,"
        " effective_at, recorded_at) VALUES (?,?,'renewed','assn',?,?)",
        (_uuid(), free_user["user_id"], "2026-09-08T00:00:00+00:00",
         "2026-09-08T00:00:00+00:00"),
    )

    d = _acq(client)
    assert d["kpis"]["paid"] == 1
    assert d["kpis"]["paid_unconfirmed"] == 0, "proved, so no longer ambiguous"


def test_lapsed_trial_is_not_paid_unconfirmed(client, tmp_db_path, free_user):
    """A lapsed trial sits on the FREE tier, so it must not leak into the
    ambiguous bucket either. Otherwise the split would launder the exact case
    it exists to exclude into a column a reader might add to `paid`."""
    _attributed_linked_device(client, tmp_db_path, free_user)
    _exec(
        tmp_db_path,
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='free' WHERE id=?",
        (free_user["user_id"],),
    )

    d = _acq(client)
    assert d["kpis"]["started"] == 1
    assert d["kpis"]["paid"] == 0
    assert d["kpis"]["paid_unconfirmed"] == 0, "free tier is not ambiguous"


def test_admin_acquisition_requires_key(client):
    r = client.get(
        "/webhooks/admin/acquisition",
        headers={"X-Admin-Key": "wrong"},
    )
    assert r.status_code == 403


# --- per-country breakdown (2026-09-08) --------------------------------------
# The LatAm campaign spans five storefronts and its whole question is which
# country converts to paid. country_or_region was written on every row since
# the table shipped and was a dimension on nothing.

def _country_device(client, tmp_db_path, country, campaign, user=None):
    dev = _uuid()
    _post(client, dev, headers=(user or {}).get("headers"))
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', attribution=1,"
        " campaign_id=?, keyword_id=99, country_or_region=?, token=NULL"
        " WHERE device_id=?",
        (campaign, country, dev),
    )
    conn.commit()
    conn.close()
    return dev


def test_countries_are_grouped_and_crossed_with_campaign(client, tmp_db_path):
    """One campaign over several storefronts is the LatAm shape; one storefront
    under several campaigns is the US shape (exact and discovery). Crossing
    them answers both, and a bare country rollup answers neither."""
    _country_device(client, tmp_db_path, "MX", 3001)
    _country_device(client, tmp_db_path, "MX", 3001)
    _country_device(client, tmp_db_path, "CL", 3001)
    _country_device(client, tmp_db_path, "US", 2144631799)
    _country_device(client, tmp_db_path, "US", 4002)

    rows = _acq(client)["countries"]
    got = {(r["country_or_region"], r["campaign_id"]): r["installs"] for r in rows}
    assert got == {("MX", 3001): 2, ("CL", 3001): 1,
                   ("US", 2144631799): 1, ("US", 4002): 1}


def test_country_rows_carry_the_full_funnel(client, tmp_db_path, free_user):
    """A country row is useless for the test unless it reaches `paid`: the
    question is which country CONVERTS, not which country installs."""
    _country_device(client, tmp_db_path, "MX", 3001, user=free_user)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE users SET ever_subscribed=1, is_trial=0, tier='pro' WHERE id=?",
        (free_user["user_id"],))
    conn.execute(
        "INSERT INTO subscription_events (id, user_id, event_type, source,"
        " effective_at, recorded_at) VALUES (?,?,'renewed','assn',?,?)",
        (_uuid(), free_user["user_id"], "2026-09-08T00:00:00+00:00",
         "2026-09-08T00:00:00+00:00"))
    conn.commit()
    conn.close()

    row = _acq(client)["countries"][0]
    assert row["country_or_region"] == "MX"
    assert row["installs"] == 1 and row["linked"] == 1
    assert row["started"] == 1 and row["paid"] == 1
    assert row["trialing"] == 0 and row["paid_unconfirmed"] == 0


def test_limited_ads_installs_stay_out_of_the_country_table(client, tmp_db_path):
    """A limited-ads row carries the literal placeholder as its campaign id, so
    it cannot be attributed to a campaign and would add a row under a
    meaningless one. It must still be counted in the KPI row, or the exclusion
    would be a silent drop rather than a scoped one."""
    dev = _uuid()
    _post(client, dev)
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "UPDATE ad_attribution SET status='attributed', attribution=1,"
        " campaign_id=1234567890, keyword_id=1234567890, standard_payload=1,"
        " country_or_region='MX', token=NULL WHERE device_id=?",
        (dev,))
    conn.commit()
    conn.close()

    d = _acq(client)
    assert d["countries"] == [], "placeholder campaign is not a campaign"
    assert d["kpis"]["attributed_limited"] == 1, "but it is NOT dropped"


# --- sweep liveness (2026-09-08) ---------------------------------------------
# Apple's tokens are exchangeable for 24 hours and unrecoverable after that,
# so a sweep that quietly stops is a countdown, not a backlog. Before this
# there was NO alert of any kind: run_daemon logged a warning if an iteration
# threw, and nothing at all fired if the task died or never started.

def _backdate_pending(db_path, device_id, minutes):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE ad_attribution SET created_at=? WHERE device_id=?",
        ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
         device_id),
    )
    conn.commit()
    conn.close()


def _incidents(db_path, category):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM alert_incidents WHERE category = ?", (category,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_a_stalled_sweep_is_reported_from_the_INGEST_path(client, tmp_db_path):
    """THE design point, and the reason this is not in the daemon: a dead
    sweep cannot report its own death. The next token to arrive is what
    notices, which is also the first moment the silence starts costing
    something."""
    old = _uuid()
    _post(client, old)
    _backdate_pending(tmp_db_path, old, minutes=40)

    _post(client, _uuid())          # a fresh token arrives; this is the trigger

    found = _incidents(tmp_db_path, "attribution_sweep_stalled")
    assert len(found) == 1, "a stalled sweep must be reported"
    assert found[0]["subject"] == "shouldersurf"


def test_a_healthy_sweep_reports_nothing(client, tmp_db_path):
    """An alarm that fires while things are fine gets ignored when they are
    not, so the quiet case is pinned as hard as the loud one."""
    _post(client, _uuid())
    _post(client, _uuid())
    assert _incidents(tmp_db_path, "attribution_sweep_stalled") == []


def test_no_pending_rows_reports_nothing(client, tmp_db_path):
    """Nothing waiting means nothing to lose, however long the sweep has been
    idle. Age of the OLDEST PENDING row is the signal, not time since a run.

    ⚠ The existing row is inserted DIRECTLY, and the trigger is a TOKEN-LESS
    link call which lands as `no_token`. Both details are load-bearing and two
    earlier drafts got this wrong: the check runs on EVERY ingest, so creating
    the first row with an ordinary post meant a fresh pending row existed
    during that request, and the test then caught a sabotage through a path
    its own docstring did not describe. As written now, no ingest in this test
    ever sees a pending row, so it genuinely exercises "nothing is waiting".
    """
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        "INSERT INTO ad_attribution (id, device_id, app_id, status, token,"
        " created_at) VALUES (?,?,?,'organic',NULL,?)",
        (_uuid(), _uuid(), "shouldersurf",
         (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()))
    conn.commit()
    conn.close()

    _post(client, _uuid(), token=None)   # link-only: no pending row created
    rows = _incidents(tmp_db_path, "attribution_sweep_stalled")
    assert rows == []


def test_a_failing_alert_never_breaks_ingest(client, tmp_db_path, monkeypatch):
    """The request that noticed the problem must still succeed."""
    old = _uuid()
    _post(client, old)
    _backdate_pending(tmp_db_path, old, minutes=40)

    async def _boom(*a, **k):
        raise RuntimeError("mail down")

    import app.services.alerting as alerting
    monkeypatch.setattr(alerting, "report_incident", _boom)
    r = _post(client, _uuid())
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_expired_tokens_raise_the_damage_alert(client, tmp_db_path):
    """The other half. Stalled is the warning; expired is the loss, and the
    sweep writing that number is the only thing that ever sees it.

    Takes `client` for the schema, like the other sweep tests: the tables are
    created by app startup, not by the tmp path.
    """
    dev = _uuid()
    _post(client, dev)
    _backdate_pending(tmp_db_path, dev, minutes=30 * 60)

    counts = await _sweep(tmp_db_path)

    assert counts["expired"] == 1
    found = _incidents(tmp_db_path, "attribution_tokens_expired")
    assert len(found) == 1
    assert found[0]["subject"] == "apple_ads"


def _insert_pending(db_path, *, age_minutes, last_attempt_minutes=None,
                    app_id="shouldersurf"):
    """A waiting row built in SQL. `last_attempt_minutes` is how long ago the
    sweep last got an answer from Apple for it; None means never attempted."""
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ad_attribution (id, device_id, app_id, status, token,"
        " created_at, last_attempt_at) VALUES (?,?,?,'pending','tok',?,?)",
        (_uuid(), _uuid(), app_id,
         (now - timedelta(minutes=age_minutes)).isoformat(),
         None if last_attempt_minutes is None
         else (now - timedelta(minutes=last_attempt_minutes)).isoformat()))
    conn.commit()
    conn.close()


def _insert_exchanged(db_path, *, exchanged_minutes_ago, app_id="shouldersurf"):
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ad_attribution (id, device_id, app_id, status, attribution,"
        " token, created_at, exchanged_at) VALUES (?,?,?,'organic',0,NULL,?,?)",
        (_uuid(), _uuid(), app_id,
         (now - timedelta(minutes=exchanged_minutes_ago + 1)).isoformat(),
         (now - timedelta(minutes=exchanged_minutes_ago)).isoformat()))
    conn.commit()
    conn.close()


def test_one_stuck_row_does_not_speak_for_a_healthy_sweep(client, tmp_db_path):
    """THE false positive, measured on production 2026-09-09 (v1 signal).

    One row Apple has no attribution record for 404s every 60 seconds by
    design and sits there forever. On prod that was 1 stuck row against 232
    exchanged, with the most recent exchange 19 SECONDS after arrival, and the
    alert announced that token exchange had stopped. It nearly cost a campaign
    launch date.

    An old pending row is not evidence of anything on its own. What decides
    is whether the SWEEP has been touching it: a 404 retry stamps
    `last_attempt_at`, and a stamp from a minute ago is a live sweep.

    ⚠ Rows are inserted DIRECTLY. The check runs on EVERY ingest, so building
    state through the endpoint fires the check mid-setup. Build state in SQL
    and use exactly one post as the trigger.
    """
    _insert_pending(tmp_db_path, age_minutes=40, last_attempt_minutes=1)
    _insert_exchanged(tmp_db_path, exchanged_minutes_ago=0)

    _post(client, _uuid())          # the next token arrives; check runs

    assert _incidents(tmp_db_path, "attribution_sweep_stalled") == [], (
        "a healthy sweep with one unexchangeable token is not a stall")


def test_no_new_tokens_for_hours_is_not_a_stall_while_the_sweep_is_retrying(
        client, tmp_db_path):
    """THE SECOND false positive, production 2026-09-09 20:02:57Z (v2 signal),
    thirty minutes after v2 deployed.

    v2 required a waiting row AND no recent successful exchange. Exchanges
    only happen when tokens ARRIVE. No install landed between 05:26Z and
    20:02Z, so the last exchange was 14.6 hours old when the next organic
    token arrived, the check read that as 14.6 hours of dead sweep, and it
    emailed. The container log showed the sweep retrying the stuck 1.15 row
    every 60 seconds the entire time, and the new token exchanged 50 seconds
    later.

    A sweep's health cannot be inferred from whether work arrived for it.
    Under v2 this test fails with one incident raised; that is the point.
    """
    _insert_pending(tmp_db_path, age_minutes=20 * 60, last_attempt_minutes=1)
    _insert_exchanged(tmp_db_path, exchanged_minutes_ago=14 * 60)

    _post(client, _uuid())

    assert _incidents(tmp_db_path, "attribution_sweep_stalled") == [], (
        "the sweep touched the waiting row a minute ago; nothing is stalled")


def test_a_waiting_row_nobody_has_attempted_in_15_minutes_IS_a_stall(
        client, tmp_db_path):
    """The other half. Without it, never alerting would pass the two tests
    above and be exactly as wrong, which is how v1 got shipped.

    The stamp is 20 minutes old while the sweep runs every 60 seconds, so it
    has missed at least fifteen passes. A successful exchange one minute ago
    on a DIFFERENT row does not excuse it: the alert reads the footprint on
    the waiting work, nothing else.
    """
    _insert_pending(tmp_db_path, age_minutes=40, last_attempt_minutes=20)
    _insert_exchanged(tmp_db_path, exchanged_minutes_ago=1)

    _post(client, _uuid())

    found = _incidents(tmp_db_path, "attribution_sweep_stalled")
    assert len(found) == 1, "a waiting row unattended for 20 minutes IS a stall"
    details = found[0].get("details_json") or ""
    assert "seconds_since_last_attempt" in details


@pytest.mark.asyncio
async def test_a_404_retry_leaves_the_sweeps_footprint(client, tmp_db_path):
    """The stamp the liveness check reads. A 404 used to write NOTHING, so a
    sweep retrying one token every minute and a sweep that was not running
    left identical rows. Under sabotage of the stamp, the healthy-sweep tests
    above still pass (their stamps are inserted by SQL), so this is the only
    test that proves the sweep actually produces the evidence they rely on."""
    dev = _uuid()
    _post(client, dev)
    assert _row(tmp_db_path, dev)["last_attempt_at"] is None
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        return_value=(404, None),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["pending"] == 1
    row = _row(tmp_db_path, dev)
    assert row["status"] == "pending"
    assert row["token"] == "tok-abc123"
    assert row["last_attempt_at"] is not None


@pytest.mark.asyncio
async def test_a_transport_failure_is_not_an_attempt(client, tmp_db_path):
    """A sweep that runs every minute but cannot reach Apple has stopped
    exchanging just as surely as a dead one, and the tokens expire at 24h
    either way. So no stamp, and the alert still fires once the row has
    waited long enough."""
    import httpx

    dev = _uuid()
    _post(client, dev)
    with patch(
        "app.services.apple_ads_attribution._post_token",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("no route to apple"),
    ):
        counts = await _sweep(tmp_db_path)
    assert counts["pending"] == 1
    assert _row(tmp_db_path, dev)["last_attempt_at"] is None
