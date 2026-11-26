"""Help and tour telemetry (Shoulder Surf 1.21, 2026-11-25). Two events on
the anonymous ping, `help_viewed` and `tour_viewed`, each with its own
block, stored in help_tour_events and read by
/webhooks/admin/telemetry/help-tour. Same pattern as the onboarding event
(tests/test_onboarding_telemetry.py).

Each test here was sabotaged once (the code broken, this test and only this
one seen red, the code restored); the list is in the PR."""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
import uuid
from pathlib import Path

import pytest

from tests.conftest import _insert_user

ADMIN = {"X-Admin-Key": "test-admin-key"}
DAY_MS = 86_400_000
ROOT = Path(__file__).resolve().parents[1]


def _uuid() -> str:
    return str(uuid.uuid4())


def _help(**over) -> dict:
    block = {
        "entry": "settings",
        "duration_ms": 95000,
        "articles": [{"id": "acct-plans", "dwell_ms": 41000},
                     {"id": "listen.start_2", "dwell_ms": 9000}],
        "searched": True,
        "searches": [{"q": "how do i delete", "opened": True},
                     {"q": "portuguese", "opened": False}],
        "asked": False,
        "whats_new_opened": True,
    }
    block.update(over)
    return block


def _tour(**over) -> dict:
    block = {
        "entry": "settings",
        "duration_ms": 64000,
        "slides_total": 12,
        "slides_seen": 3,
        "last_slide": "tour-03",
        "completed": False,
        "narration_on": True,
        "language": "pt-BR",
    }
    block.update(over)
    return block


def _envelope(event_type: str, **over) -> dict:
    body = {
        "event_type": event_type,
        "device_id": _uuid(),
        "user_id": "user-abc",
        "app_version": "1.21",
        "app_build": "912",
        "os_version": "26.1",
        "device_model": "iPhone17,3",
        "app_locale": "pt_BR",
    }
    body.update(over)
    return body


def _help_event(block: dict | None = None, **over) -> dict:
    return _envelope("help_viewed", help=_help() if block is None else block, **over)


def _tour_event(block: dict | None = None, **over) -> dict:
    return _envelope("tour_viewed", tour=_tour() if block is None else block, **over)


def _post(client, body: dict):
    return client.post("/v1/events/ping", json=body,
                       headers={"X-App-ID": "shouldersurf"})


def _stored(db_path: str, device_id: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT event_type, user_id, app_id, app_version, app_build,
                  os_version, device_model, app_locale, payload
           FROM help_tour_events WHERE device_id = ?""", (device_id,)).fetchall()
    conn.close()
    return rows


def _payloads(db_path: str, device_id: str) -> list[dict]:
    return [json.loads(r[-1]) for r in _stored(db_path, device_id)]


def _dashboard(client, query: str = "days=30") -> dict:
    r = client.get(f"/webhooks/admin/telemetry/help-tour?{query}", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


# --- request side: what was sent is what is stored ------------------------


def test_help_block_is_stored_exactly_as_sent(client, tmp_db_path):
    body = _help_event()
    sent = copy.deepcopy(body["help"])
    assert _post(client, body).status_code == 204
    rows = _stored(tmp_db_path, body["device_id"])
    assert len(rows) == 1
    assert rows[0][:8] == ("help_viewed", "user-abc", "shouldersurf", "1.21", "912",
                           "26.1", "iPhone17,3", "pt_BR")
    assert json.loads(rows[0][8]) == sent


def test_tour_block_is_stored_exactly_as_sent(client, tmp_db_path):
    body = _tour_event()
    sent = copy.deepcopy(body["tour"])
    assert _post(client, body).status_code == 204
    rows = _stored(tmp_db_path, body["device_id"])
    assert len(rows) == 1
    assert rows[0][:8] == ("tour_viewed", "user-abc", "shouldersurf", "1.21", "912",
                           "26.1", "iPhone17,3", "pt_BR")
    assert json.loads(rows[0][8]) == sent


def test_help_and_tour_stay_out_of_the_lifecycle_table(client, tmp_db_path):
    """telemetry_events feeds COUNT(*) KPIs; a Help visit is not an app event."""
    h, t = _help_event(), _tour_event()
    assert _post(client, h).status_code == 204
    assert _post(client, t).status_code == 204
    conn = sqlite3.connect(tmp_db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM telemetry_events WHERE device_id IN (?, ?)",
        (h["device_id"], t["device_id"])).fetchone()[0]
    conn.close()
    assert n == 0


def test_values_at_the_upper_bounds_are_accepted(client, tmp_db_path):
    h = _help_event(_help(
        duration_ms=DAY_MS,
        articles=[{"id": "a" * 64, "dwell_ms": DAY_MS}] * 50,
        searches=[{"q": f"{'x' * 97} {i:02d}", "opened": False} for i in range(20)]))
    t = _tour_event(_tour(duration_ms=DAY_MS, slides_total=200, slides_seen=200,
                          last_slide="t" * 64, language="zh-Hans-CN-x-ab1"))
    assert _post(client, h).status_code == 204
    assert _post(client, t).status_code == 204
    assert len(_payloads(tmp_db_path, h["device_id"])[0]["searches"]) == 20
    assert _payloads(tmp_db_path, t["device_id"])[0]["language"] == "zh-Hans-CN-x-ab1"


def test_optional_fields_may_be_left_out(client, tmp_db_path):
    h = _help(); del h["articles"]; del h["searches"]
    t = _tour(); del t["last_slide"]; del t["language"]
    hb, tb = _help_event(h), _tour_event(t)
    assert _post(client, hb).status_code == 204
    assert _post(client, tb).status_code == 204
    stored_h = _payloads(tmp_db_path, hb["device_id"])[0]
    stored_t = _payloads(tmp_db_path, tb["device_id"])[0]
    assert stored_h["articles"] == [] and stored_h["searches"] == []
    assert stored_t["last_slide"] is None and stored_t["language"] is None


# --- validation bounds, one case per bound ---------------------------------

_BAD_HELP = {
    "entry_unknown": {"entry": "onboarding"},
    "duration_negative": {"duration_ms": -1},
    "duration_over_24h": {"duration_ms": DAY_MS + 1},
    "articles_over_50": {"articles": [{"id": "a", "dwell_ms": 1}] * 51},
    "article_id_uppercase": {"articles": [{"id": "Acct-Plans", "dwell_ms": 1}]},
    "article_id_has_space": {"articles": [{"id": "acct plans", "dwell_ms": 1}]},
    "article_id_leading_dash": {"articles": [{"id": "-acct", "dwell_ms": 1}]},
    "article_id_65_chars": {"articles": [{"id": "a" * 65, "dwell_ms": 1}]},
    "article_id_empty": {"articles": [{"id": "", "dwell_ms": 1}]},
    "article_id_trailing_newline": {"articles": [{"id": "acct\n", "dwell_ms": 1}]},
    "article_dwell_negative": {"articles": [{"id": "a", "dwell_ms": -1}]},
    "article_dwell_over_24h": {"articles": [{"id": "a", "dwell_ms": DAY_MS + 1}]},
    "searched_missing": {"searched": None},
    "asked_missing": {"asked": None},
    "whats_new_opened_missing": {"whats_new_opened": None},
    "searches_over_20": {"searches": [{"q": f"q {i}", "opened": False} for i in range(21)]},
    "search_q_101_chars": {"searches": [{"q": "x" * 101, "opened": False}]},
    "search_q_blank_after_trim": {"searches": [{"q": "   ", "opened": False}]},
    "search_q_newline": {"searches": [{"q": "how do\ni delete", "opened": False}]},
    "search_q_tab": {"searches": [{"q": "how\tdo", "opened": False}]},
    "search_q_control_char": {"searches": [{"q": "how\x07do", "opened": False}]},
    "search_q_line_separator": {"searches": [{"q": "how do", "opened": False}]},
    "search_q_missing": {"searches": [{"opened": False}]},
    "search_opened_missing": {"searches": [{"q": "delete"}]},
    "search_extra_key": {"searches": [{"q": "delete", "opened": False, "note": "x"}]},
    "searches_without_searched": {"searched": False},
}

_BAD_TOUR = {
    "entry_onboarding": {"entry": "onboarding"},
    "entry_unknown": {"entry": "listen_mode"},
    "duration_negative": {"duration_ms": -1},
    "duration_over_24h": {"duration_ms": DAY_MS + 1},
    "slides_total_negative": {"slides_total": -1},
    "slides_total_over_200": {"slides_total": 201, "slides_seen": 1},
    "slides_seen_negative": {"slides_seen": -1},
    # No case for slides_seen over 200 on its own: with slides_total capped
    # at 200 and slides_seen <= slides_total, that bound cannot be reached
    # alone, so a test for it could not fail.
    "slides_seen_over_total": {"slides_total": 12, "slides_seen": 13},
    "last_slide_free_text": {"last_slide": "Tour 03"},
    "last_slide_65_chars": {"last_slide": "t" * 65},
    "completed_missing": {"completed": None},
    "narration_on_missing": {"narration_on": None},
    "language_17_chars": {"language": "zh-Hans-CN-x-abc1"},
    "language_free_text": {"language": "português"},
    "language_has_space": {"language": "pt BR"},
}


@pytest.mark.parametrize("case", sorted(_BAD_HELP))
def test_help_block_bound_is_enforced(client, tmp_db_path, case):
    body = _help_event(_help(**_BAD_HELP[case]))
    r = _post(client, body)
    assert r.status_code == 422, r.text
    assert _stored(tmp_db_path, body["device_id"]) == []


@pytest.mark.parametrize("case", sorted(_BAD_TOUR))
def test_tour_block_bound_is_enforced(client, tmp_db_path, case):
    body = _tour_event(_tour(**_BAD_TOUR[case]))
    r = _post(client, body)
    assert r.status_code == 422, r.text
    assert _stored(tmp_db_path, body["device_id"]) == []


def test_rejection_body_names_the_field(client):
    r = _post(client, _tour_event(_tour(slides_total=12, slides_seen=13)))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["loc"] == ["body", "tour"]
    assert detail[0]["msg"] == "Value error, slides_seen must not exceed slides_total"
    r = _post(client, _help_event(_help(duration_ms=DAY_MS + 1)))
    assert r.json()["detail"][0]["loc"] == ["body", "help", "duration_ms"]
    assert r.json()["detail"][0]["type"] == "less_than_equal"


# --- a block belongs to its own event --------------------------------------


@pytest.mark.parametrize("event_type", ["app_start", "meeting_start", "tour_viewed"])
def test_stray_help_block_is_rejected(client, tmp_db_path, event_type):
    body = _envelope(event_type, help=_help())
    if event_type == "tour_viewed":
        body["tour"] = _tour()
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Value error, help payload only valid on help_viewed"
    assert _stored(tmp_db_path, body["device_id"]) == []


@pytest.mark.parametrize("event_type", ["app_start", "meeting_start", "help_viewed"])
def test_stray_tour_block_is_rejected(client, tmp_db_path, event_type):
    body = _envelope(event_type, tour=_tour())
    if event_type == "help_viewed":
        body["help"] = _help()
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Value error, tour payload only valid on tour_viewed"
    assert _stored(tmp_db_path, body["device_id"]) == []


def test_help_viewed_requires_its_block(client):
    r = _post(client, _envelope("help_viewed"))
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Value error, help payload required for help_viewed"


def test_tour_viewed_requires_its_block(client):
    r = _post(client, _envelope("tour_viewed"))
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Value error, tour payload required for tour_viewed"


def test_unknown_key_inside_a_block_is_accepted_and_not_stored(client, tmp_db_path):
    """What the onboarding block does today: pydantic's default, ignored."""
    h, t = _help_event(_help(note="free text here")), _tour_event(_tour(note="free text here"))
    assert _post(client, h).status_code == 204
    assert _post(client, t).status_code == 204
    assert "note" not in _payloads(tmp_db_path, h["device_id"])[0]
    assert "note" not in _payloads(tmp_db_path, t["device_id"])[0]
    conn = sqlite3.connect(tmp_db_path)
    dump = " ".join(str(r) for r in conn.execute("SELECT * FROM help_tour_events"))
    conn.close()
    assert "free text here" not in dump


# --- Help search text -------------------------------------------------------


def _searches_stored(client, db_path, searches) -> list[dict]:
    body = _help_event(_help(searches=searches))
    assert _post(client, body).status_code == 204
    return _payloads(db_path, body["device_id"])[0]["searches"]


def test_search_text_is_normalized_before_it_is_stored(client, tmp_db_path):
    got = _searches_stored(client, tmp_db_path,
                           [{"q": "  How do  I   DELETE ", "opened": True}])
    assert got == [{"q": "how do i delete", "opened": True}]


def test_search_length_is_measured_after_normalizing(client, tmp_db_path):
    got = _searches_stored(client, tmp_db_path,
                           [{"q": "  " + "x" * 50 + "     " + "y" * 49 + "  ", "opened": False}])
    assert got == [{"q": "x" * 50 + " " + "y" * 49, "opened": False}]


def test_an_email_search_is_dropped_and_the_event_kept(client, tmp_db_path):
    got = _searches_stored(client, tmp_db_path, [
        {"q": "Jane.Doe@Example.com", "opened": False},
        {"q": "email jane@work.co please", "opened": True},
        # Email shaped but not link shaped: only the email pattern drops it.
        {"q": "bob@10.0.0.5", "opened": False},
        {"q": "how do i delete", "opened": True}])
    assert got == [{"q": "how do i delete", "opened": True}]


def test_a_phone_number_search_is_dropped_and_model_numbers_kept(client, tmp_db_path):
    got = _searches_stored(client, tmp_db_path, [
        {"q": "+1 (555) 123-4567", "opened": False},
        {"q": "call 555.123.4567", "opened": False},
        {"q": "5551234", "opened": False},
        {"q": "iphone 17 pro", "opened": True},
        {"q": "ios 26.1", "opened": False},
        {"q": "error 123456", "opened": False}])
    assert got == [{"q": "iphone 17 pro", "opened": True},
                   {"q": "ios 26.1", "opened": False},
                   {"q": "error 123456", "opened": False}]


def test_a_url_search_is_dropped(client, tmp_db_path):
    got = _searches_stored(client, tmp_db_path, [
        {"q": "https://example.com/reset?u=jane", "opened": False},
        {"q": "www.example.com", "opened": False},
        {"q": "see example.org", "opened": False},
        {"q": "ftp://10.0.0.5/x", "opened": False},
        {"q": "how do i delete", "opened": False},
        {"q": "what is new in 1.21", "opened": False}])
    assert got == [{"q": "how do i delete", "opened": False},
                   {"q": "what is new in 1.21", "opened": False}]


def test_when_every_search_is_dropped_the_event_still_lands(client, tmp_db_path):
    body = _help_event(_help(searches=[{"q": "jane@example.com", "opened": False}]))
    assert _post(client, body).status_code == 204
    stored = _payloads(tmp_db_path, body["device_id"])[0]
    assert stored["searches"] == [] and stored["searched"] is True
    conn = sqlite3.connect(tmp_db_path)
    dump = " ".join(str(r) for r in conn.execute("SELECT * FROM help_tour_events"))
    conn.close()
    assert "jane@example.com" not in dump


def test_duplicate_searches_merge_and_opened_wins(client, tmp_db_path):
    got = _searches_stored(client, tmp_db_path, [
        {"q": "portuguese", "opened": False},
        {"q": "delete", "opened": False},
        {"q": " Portuguese ", "opened": True},
        {"q": "delete", "opened": False}])
    assert got == [{"q": "portuguese", "opened": True},
                   {"q": "delete", "opened": False}]


def test_search_text_never_reaches_the_application_log(client, caplog):
    """Accepted, dropped and rejected searches alike, at the level the app
    runs at (main.py sets the root logger to INFO). At DEBUG the aiosqlite
    driver prints every statement's parameters, for every table."""
    with caplog.at_level(logging.INFO):
        assert _post(client, _help_event(_help(searches=[
            {"q": "zebra crossing query", "opened": False},
            {"q": "zebra@example.com", "opened": False}]))).status_code == 204
        assert _post(client, _help_event(_help(
            searches=[{"q": "zebra\nrejected", "opened": False}]))).status_code == 422
    assert "POST /v1/events/ping" in caplog.text  # the capture is live
    assert "zebra" not in caplog.text


# --- admin read side ---------------------------------------------------------


def _seed_help(client) -> dict[str, str]:
    d = {"a": _uuid(), "b": _uuid()}
    views = [
        (d["a"], _help(entry="settings", duration_ms=60000, searched=True, asked=False,
                       whats_new_opened=True,
                       articles=[{"id": "acct-plans", "dwell_ms": 10000},
                                 {"id": "listen-start", "dwell_ms": 4999}],
                       searches=[{"q": "delete", "opened": True},
                                 {"q": "portuguese", "opened": False}])),
        (d["a"], _help(entry="settings", duration_ms=20000, searched=True, asked=True,
                       whats_new_opened=False,
                       articles=[{"id": "acct-plans", "dwell_ms": 30000},
                                 {"id": "privacy", "dwell_ms": 5000}],
                       searches=[{"q": "delete", "opened": False},
                                 {"q": "odd one", "opened": False}])),
        (d["b"], _help(entry="listen_mode", duration_ms=100000, searched=True, asked=False,
                       whats_new_opened=False,
                       articles=[{"id": "acct-plans", "dwell_ms": 20000}],
                       searches=[{"q": "delete", "opened": True}])),
        (d["b"], _help(entry="other", duration_ms=40000, searched=False, asked=False,
                       whats_new_opened=False, articles=[], searches=[])),
    ]
    for device, block in views:
        assert _post(client, _help_event(block, device_id=device)).status_code == 204
    return d


def _seed_tour(client) -> dict[str, str]:
    d = {"a": _uuid(), "b": _uuid(), "c": _uuid()}
    views = [
        (d["a"], _tour(slides_seen=12, last_slide="tour-12", completed=True,
                       narration_on=True, language="pt-BR")),
        (d["a"], _tour(slides_seen=3, last_slide="tour-03", completed=False,
                       narration_on=False, language="pt_BR")),
        (d["b"], _tour(slides_seen=3, last_slide="tour-03", completed=False,
                       narration_on=True, language="en", entry="other")),
        (d["c"], _tour(slides_seen=1, last_slide="tour-01", completed=False,
                       narration_on=False, language=None)),
        (d["c"], _tour(slides_seen=7, last_slide="tour-07", completed=False,
                       narration_on=False, language="en")),
    ]
    for device, block in views:
        assert _post(client, _tour_event(block, device_id=device)).status_code == 204
    return d


def _sql(db_path: str, statement: str, *args) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def test_dashboard_help_aggregates(client):
    _seed_help(client)
    _seed_tour(client)  # must not leak into the help numbers
    h = _dashboard(client)["help"]
    assert h["views"] == 4
    assert h["distinct_devices"] == 2
    assert h["median_duration_ms"] == 50000.0  # of 20000, 40000, 60000, 100000
    assert h["by_entry"] == [{"entry": "settings", "n": 2},
                             {"entry": "listen_mode", "n": 1},
                             {"entry": "other", "n": 1}]
    assert h["searched_rate"] == 75.0
    assert h["asked_rate"] == 25.0
    assert h["whats_new_opened_rate"] == 25.0


def test_dashboard_visits_with_no_article(client):
    _seed_help(client)
    h = _dashboard(client)["help"]
    assert h["no_article_views"] == 1 and h["no_article_rate"] == 25.0


def test_dashboard_top_articles_with_devices_and_fast_exit(client):
    _seed_help(client)
    assert _dashboard(client)["help"]["top_articles"] == [
        {"id": "acct-plans", "opens": 3, "distinct_devices": 2,
         "median_dwell_ms": 20000.0, "fast_exit": False},
        # 4999 ms is under the 5 s line, 5000 ms is on it.
        {"id": "listen-start", "opens": 1, "distinct_devices": 1,
         "median_dwell_ms": 4999.0, "fast_exit": True},
        {"id": "privacy", "opens": 1, "distinct_devices": 1,
         "median_dwell_ms": 5000.0, "fast_exit": False}]


def test_dashboard_top_searches(client):
    _seed_help(client)
    assert _dashboard(client)["help"]["top_searches"] == [
        {"q": "delete", "count": 3, "distinct_devices": 2, "opened_rate": 66.7,
         "single_device": False},
        {"q": "odd one", "count": 1, "distinct_devices": 1, "opened_rate": 0.0,
         "single_device": True},
        {"q": "portuguese", "count": 1, "distinct_devices": 1, "opened_rate": 0.0,
         "single_device": True}]


def test_dashboard_help_gaps(client):
    _seed_help(client)
    extra = _uuid()
    assert _post(client, _help_event(_help(
        searches=[{"q": "portuguese", "opened": False},
                  # Always led to an article, so it is not a gap.
                  {"q": "mac app", "opened": True}]), device_id=extra)).status_code == 204
    # "delete" went unopened once, on one device, though two devices searched
    # it; "portuguese" went unopened on two.
    assert _dashboard(client)["help"]["unopened_searches"] == [
        {"q": "portuguese", "count": 2, "distinct_devices": 2, "single_device": False},
        {"q": "delete", "count": 1, "distinct_devices": 1, "single_device": True},
        {"q": "odd one", "count": 1, "distinct_devices": 1, "single_device": True}]


def test_dashboard_tour_aggregates(client):
    _seed_tour(client)
    _seed_help(client)  # must not leak into the tour numbers
    t = _dashboard(client)["tour"]
    assert t["views"] == 5
    assert t["distinct_devices"] == 3
    assert t["completed"] == 1
    assert t["completion_rate"] == 20.0
    assert t["median_slides_seen"] == 3.0  # of 1, 3, 3, 7, 12
    assert t["by_entry"] == [{"entry": "settings", "n": 4}, {"entry": "other", "n": 1}]
    assert t["narration_on_rate"] == 40.0


def test_dashboard_tour_drop_off_is_early_exits_in_slide_order(client):
    _seed_tour(client)
    # Only the four who left early; the finished one is not a drop.
    assert _dashboard(client)["tour"]["drop_off"] == [
        {"last_slide": "tour-01", "n": 1},
        {"last_slide": "tour-03", "n": 2},
        {"last_slide": "tour-07", "n": 1}]


def test_dashboard_tour_languages_fold_the_separator(client):
    _seed_tour(client)
    assert _dashboard(client)["tour"]["by_language"] == [
        {"language": "en", "n": 2}, {"language": "pt-BR", "n": 2},
        {"language": "unknown", "n": 1}]


def test_dashboard_daily_series_has_every_day_of_the_window(client, tmp_db_path):
    from datetime import datetime, timedelta, timezone

    h = _seed_help(client)
    _seed_tour(client)
    _sql(tmp_db_path, "UPDATE help_tour_events SET received_at = "
         "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-2 days') WHERE device_id = ?", h["b"])
    daily = _dashboard(client, "days=7")["daily"]
    today = datetime.now(timezone.utc).date()
    assert [x["day"] for x in daily] == [
        (today - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
    assert daily[-1] == {"day": today.isoformat(), "help": 2, "tour": 5}
    assert daily[-3] == {"day": (today - timedelta(days=2)).isoformat(), "help": 2, "tour": 0}
    assert sum(x["help"] for x in daily) == 4 and sum(x["tour"] for x in daily) == 5


def test_dashboard_reach_is_a_share_of_active_devices(client):
    h = _seed_help(client)       # 2 devices
    t = _seed_tour(client)       # 3 other devices
    for device in (*h.values(), *t.values(), _uuid(), _uuid(), _uuid()):
        assert _post(client, _envelope("app_start", device_id=device)).status_code == 204
    assert _dashboard(client, "days=90")["reach"] == {
        # telemetry_events only goes back 30 days, so the window says 30.
        "window_days": 30, "active_devices": 8,
        "help_devices": 2, "help_rate": 25.0,
        "tour_devices": 3, "tour_rate": 37.5}


def test_dashboard_reach_counts_a_help_device_with_no_lifecycle_ping(client):
    _seed_help(client)
    assert _post(client, _envelope("app_start")).status_code == 204
    r = _dashboard(client)["reach"]
    assert (r["active_devices"], r["help_devices"], r["help_rate"]) == (3, 2, 66.7)


def test_dashboard_first_look_is_bucketed_by_days_since_first_seen(client, tmp_db_path):
    ages = {"day_0": 0, "days_1_3": 3, "days_4_14": 14, "later": 15}
    for bucket, age in ages.items():
        body = _help_event()
        assert _post(client, body).status_code == 204
        _sql(tmp_db_path, "UPDATE telemetry_devices SET first_seen_at = "
             "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?) WHERE device_id = ?",
             f"-{age} days", body["device_id"])
    # One more "later" device: its tour view is today, its Help view too.
    both = _uuid()
    assert _post(client, _help_event(device_id=both)).status_code == 204
    assert _post(client, _tour_event(device_id=both)).status_code == 204
    _sql(tmp_db_path, "UPDATE telemetry_devices SET first_seen_at = "
         "datetime('now', '-40 days') WHERE device_id = ?", both)
    # And one whose first-seen row is gone: counted apart, never guessed.
    lost = _help_event()
    assert _post(client, lost).status_code == 204
    _sql(tmp_db_path, "DELETE FROM telemetry_devices WHERE device_id = ?", lost["device_id"])
    assert _dashboard(client)["install_timing"] == {
        "buckets": ["day_0", "days_1_3", "days_4_14", "later"],
        "help": {"day_0": 1, "days_1_3": 1, "days_4_14": 1, "later": 2},
        "tour": {"day_0": 0, "days_1_3": 0, "days_4_14": 0, "later": 1},
        "help_unknown": 1, "tour_unknown": 0}


def test_dashboard_first_look_uses_the_first_view_not_the_first_in_the_window(client, tmp_db_path):
    """Opened Help 20 days ago on day 0, and again today: still day 0."""
    device = _uuid()
    assert _post(client, _help_event(device_id=device)).status_code == 204
    _sql(tmp_db_path, "UPDATE help_tour_events SET received_at = "
         "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-20 days') WHERE device_id = ?", device)
    _sql(tmp_db_path, "UPDATE telemetry_devices SET first_seen_at = "
         "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-20 days', '-1 hours') "
         "WHERE device_id = ?", device)
    assert _post(client, _help_event(device_id=device)).status_code == 204
    timing = _dashboard(client, "days=7")["install_timing"]
    assert timing["help"] == {"day_0": 1, "days_1_3": 0, "days_4_14": 0, "later": 0}


def _onboarding(client, **block) -> None:
    body = {"event_type": "onboarding_completed", "device_id": _uuid(),
            "app_version": "1.21", "onboarding": block}
    assert _post(client, body).status_code == 204


def _steps(*names) -> list[dict]:
    return [{"step": s, "dwell_ms": 1000} for s in names]


def test_dashboard_onboarding_tour_from_what_the_event_records(client):
    # Went through: tour shown, no skip, finished onboarding.
    _onboarding(client, completed=True, steps=_steps("highlights", "tour_1", "tour_2", "complete"))
    # Went through the tour, then left on a later, non-tour step.
    _onboarding(client, completed=False, abandoned_at_step="voice_enrollment",
                steps=_steps("tour_1", "tour_2", "voice_enrollment"))
    # Tapped Skip on slide 2 (twice) and on slide 1.
    _onboarding(client, completed=True, tour_skipped=True, steps=_steps("tour_1", "tour_2", "complete"))
    _onboarding(client, completed=True, tour_skipped=True, steps=_steps("tour_1", "tour_2"))
    _onboarding(client, completed=True, tour_skipped=True, steps=_steps("tour_1"))
    # Left the app on slide 10 (sorted as a number, after 2).
    _onboarding(client, completed=False, abandoned_at_step="tour_10",
                steps=_steps("tour_1", "tour_10"))
    # Never saw the tour: left on the first page.
    _onboarding(client, completed=False, abandoned_at_step="highlights",
                steps=_steps("highlights"))
    assert _dashboard(client)["onboarding_tour"] == {
        "onboardings": 7, "tour_shown": 6,
        "tour_skipped": 3, "tour_skipped_rate": 50.0,
        "went_through": 2, "went_through_rate": 33.3,
        "stops": [{"slide": "tour_1", "skipped": 1, "left_app": 0},
                  {"slide": "tour_2", "skipped": 2, "left_app": 0},
                  {"slide": "tour_10", "skipped": 0, "left_app": 1}]}


def test_dashboard_takeaways_wait_for_ten_views(client):
    gap = [{"q": "export", "opened": False}]
    devices = [_uuid() for _ in range(3)]
    for i in range(9):
        assert _post(client, _help_event(
            _help(searches=gap, articles=[{"id": "acct-plans", "dwell_ms": 9000}]),
            device_id=devices[i % 3])).status_code == 204
        assert _post(client, _tour_event(device_id=devices[i % 3])).status_code == 204
    d = _dashboard(client)
    assert d["takeaway_min_views"] == 10
    assert d["takeaways"] == []
    assert _post(client, _help_event(
        _help(searches=gap, articles=[{"id": "acct-plans", "dwell_ms": 9000}]),
        device_id=devices[0])).status_code == 204
    assert _post(client, _tour_event(device_id=devices[0])).status_code == 204
    assert _dashboard(client)["takeaways"] == [
        {"title": "100.0% of active devices opened Help",
         "body": "3 of 3 devices in the last 30 days."},
        {"title": "Most opened article: acct-plans",
         "body": "Opened 10 times on 3 devices."},
        {"title": 'Top search with no article opened: "export"',
         "body": "10 times across 3 devices. Help may be missing an answer here."},
        {"title": "Most people who leave the tour early stop at tour-03",
         "body": "10 of 10 early exits. 0.0% of tour views reach the end."}]


def test_takeaways_never_name_a_search_from_one_device(client):
    device = _uuid()
    for _ in range(10):
        assert _post(client, _help_event(
            _help(searches=[{"q": "one person only", "opened": False}], articles=[]),
            device_id=device)).status_code == 204
    d = _dashboard(client)
    assert d["help"]["unopened_searches"][0]["q"] == "one person only"
    assert [t["title"] for t in d["takeaways"]] == ["100.0% of active devices opened Help"]


def test_dashboard_empty_state(client):
    d = _dashboard(client)
    daily = d.pop("daily")
    assert len(daily) == 30 and all(x["help"] == 0 and x["tour"] == 0 for x in daily)
    assert d == {
        "days": 30,
        "filters": {"app": None, "app_version": None, "os_version": None,
                    "device_model": None},
        "has_activity": False,
        "takeaway_min_views": 10,
        "takeaways": [],
        "reach": {"window_days": 30, "active_devices": 0, "help_devices": 0,
                  "help_rate": 0.0, "tour_devices": 0, "tour_rate": 0.0},
        "install_timing": {
            "buckets": ["day_0", "days_1_3", "days_4_14", "later"],
            "help": {"day_0": 0, "days_1_3": 0, "days_4_14": 0, "later": 0},
            "tour": {"day_0": 0, "days_1_3": 0, "days_4_14": 0, "later": 0},
            "help_unknown": 0, "tour_unknown": 0},
        "help": {"views": 0, "distinct_devices": 0, "median_duration_ms": None,
                 "by_entry": [], "searched_rate": 0.0, "asked_rate": 0.0,
                 "whats_new_opened_rate": 0.0, "no_article_views": 0,
                 "no_article_rate": 0.0, "top_articles": [],
                 "top_searches": [], "unopened_searches": []},
        "tour": {"views": 0, "distinct_devices": 0, "completed": 0,
                 "completion_rate": 0.0, "median_slides_seen": None, "by_entry": [],
                 "drop_off": [], "narration_on_rate": 0.0, "by_language": []},
        "onboarding_tour": {"onboardings": 0, "tour_shown": 0, "tour_skipped": 0,
                            "tour_skipped_rate": 0.0, "went_through": 0,
                            "went_through_rate": 0.0, "stops": []},
    }


def test_dashboard_with_only_onboarding_data_has_no_activity(client):
    _onboarding(client, completed=True, steps=_steps("tour_1", "complete"))
    d = _dashboard(client)
    assert d["has_activity"] is False
    assert d["onboarding_tour"]["tour_shown"] == 1


@pytest.mark.parametrize("field,param", [("app_version", "app_version"),
                                         ("os_version", "os_version"),
                                         ("device_model", "device_model")])
def test_dashboard_telemetry_filters(client, field, param):
    assert _post(client, _help_event(**{field: "keep-me"})).status_code == 204
    assert _post(client, _help_event(**{field: "other"})).status_code == 204
    assert _post(client, _tour_event(**{field: "other"})).status_code == 204
    assert _post(client, _envelope("app_start", **{field: "keep-me"})).status_code == 204
    assert _post(client, _envelope("app_start", **{field: "other"})).status_code == 204
    d = _dashboard(client, f"days=30&{param}=keep-me")
    assert d["filters"][field] == "keep-me"
    assert d["help"]["views"] == 1 and d["tour"]["views"] == 0
    assert d["reach"]["active_devices"] == 2  # the Help device and one app_start


def test_dashboard_app_filter(client):
    assert _post(client, _help_event()).status_code == 204
    assert _dashboard(client, "days=30&app=shouldersurf")["help"]["views"] == 1
    assert _dashboard(client, "days=30&app=n400")["help"]["views"] == 0


def test_dashboard_leaves_out_stats_excluded_accounts(client, tmp_db_path):
    """The lifecycle telemetry rule: flagged accounts out, device-only rows in."""
    _insert_user(tmp_db_path, user_id="flagged-tester")
    _sql(tmp_db_path, "UPDATE users SET exclude_from_stats = 1 WHERE id = 'flagged-tester'")
    assert _post(client, _help_event(user_id="flagged-tester")).status_code == 204
    assert _post(client, _tour_event(user_id="flagged-tester")).status_code == 204
    assert _post(client, _help_event(user_id=None)).status_code == 204
    assert _post(client, _help_event(user_id="real-person")).status_code == 204
    d = _dashboard(client)
    assert d["help"]["views"] == 2 and d["tour"]["views"] == 0
    assert d["install_timing"]["help"]["day_0"] == 2
    assert d["install_timing"]["tour"]["day_0"] == 0


def test_dashboard_window_excludes_older_rows(client, tmp_db_path):
    body = _help_event()
    assert _post(client, body).status_code == 204
    assert _post(client, _help_event()).status_code == 204
    _sql(tmp_db_path, "UPDATE help_tour_events SET received_at = datetime('now', '-10 days') "
         "WHERE device_id = ?", body["device_id"])
    assert _dashboard(client, "days=7")["help"]["views"] == 1
    assert _dashboard(client, "days=30")["help"]["views"] == 2


def test_dashboard_requires_admin(client):
    r = client.get("/webhooks/admin/telemetry/help-tour?days=30",
                   headers={"X-Admin-Key": "wrong-key"})
    assert r.status_code != 200


def test_install_bucket_edges():
    from datetime import datetime, timedelta, timezone

    from app.services.help_tour_stats import install_bucket

    seen = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

    def at(**delta):
        return install_bucket(seen, seen + timedelta(**delta))

    assert at(hours=23, minutes=59) == "day_0"
    assert at(days=1) == "days_1_3"
    assert at(days=3, hours=23) == "days_1_3"
    assert at(days=4) == "days_4_14"
    assert at(days=14, hours=23) == "days_4_14"
    assert at(days=15) == "later"
    assert at(hours=-5) == "day_0"  # a view stamped before first-seen is day 0
    assert install_bucket(None, seen) is None


# --- retention and deletion --------------------------------------------------


def test_rows_past_the_window_are_swept(client, tmp_db_path):
    import asyncio

    import aiosqlite

    from app.services.retention import purge_expired

    old, fresh = _help_event(), _tour_event()
    assert _post(client, old).status_code == 204
    assert _post(client, fresh).status_code == 204
    conn = sqlite3.connect(tmp_db_path)
    conn.execute("UPDATE help_tour_events SET received_at = datetime('now', '-181 days') "
                 "WHERE device_id = ?", (old["device_id"],))
    conn.execute("UPDATE help_tour_events SET received_at = datetime('now', '-179 days') "
                 "WHERE device_id = ?", (fresh["device_id"],))
    conn.commit()
    conn.close()

    async def _sweep() -> dict:
        async with aiosqlite.connect(tmp_db_path) as db:
            return await purge_expired(db)

    assert asyncio.run(_sweep())["help_tour_events"] == 1
    assert _stored(tmp_db_path, old["device_id"]) == []
    assert len(_stored(tmp_db_path, fresh["device_id"])) == 1


# --- the dashboard section -----------------------------------------------------


def _admin_src() -> str:
    return (ROOT / "app" / "static" / "admin.html").read_text()


def _render_src() -> str:
    src = _admin_src()
    return src[src.index("async function _loadHelpTour("):src.index("async function _loadCases(app)")]


def test_section_is_served_in_its_empty_state_and_makes_one_request():
    src = _admin_src()
    # The served markup already carries the empty state, so the section reads
    # right before any fetch and when no client has sent a view.
    assert ('<div id="helptour-section"><div class="dash-grid"><div class="card dash-card span-all">'
            '<div class="share-empty">No Help or tour activity yet. iOS 1.21 starts sending it.'
            '</div></div></div></div>') in src
    assert '<h3 style="margin:28px 0 8px">Help and learning</h3>' in src
    assert "_loadHelpTour(days, app, {version, os, device});" in src
    assert src.count("/webhooks/admin/telemetry/help-tour") == 1


def test_section_with_no_activity_draws_one_card_not_empty_charts():
    render = _render_src()
    assert "const _HELPTOUR_EMPTY = 'No Help or tour activity yet. iOS 1.21 starts sending it.';" in render
    body = render[render.index("function _renderHelpTour(d)"):]
    empty = body[body.index("if (!d.has_activity) {"):body.index("const n = (v)")]
    assert "${_HELPTOUR_EMPTY}" in empty and "return;" in empty
    # Only the onboarding tour may be drawn beside it, and only with data.
    assert "hasOnb ? _helpTourOnboardingCard(o, null) : ''" in empty
    assert "_renderChart(" not in empty and "_renderShareBars(" not in empty


def test_section_sends_the_telemetry_filters():
    render = _render_src()
    for line in ("if (app) params.set('app', app);",
                 "if (f.version) params.set('app_version', f.version);",
                 "if (f.os) params.set('os_version', f.os);",
                 "if (f.device) params.set('device_model', f.device);"):
        assert line in render, line


def test_section_answers_the_three_questions_in_order_with_captions():
    render = _render_src()
    order = ["Key takeaways", "1. When do people go to learn?", "Help and tour views per day",
             "<h4>Reach</h4>", "First look, by days since install", "Where Help was opened from",
             "2. What do they use Help for?", "<h4>Help visits</h4>", "<h4>Top Help articles</h4>",
             "3. What do they search for?", "<h4>Top searches</h4>",
             "Help gaps: searches with no article opened", "Settings tour: where people leave",
             "<h4>Tour language</h4>"]
    body = render[render.index("function _renderHelpTour(d)"):]
    at = [body.index(x) for x in order]
    assert at == sorted(at)
    # Every card in the populated section opens with an h4 and a caption.
    populated = body[body.index("sec.innerHTML = `${takeaways}"):body.index("_renderChart('helptour-chart-daily'")]
    assert populated.count('<div class="card dash-card') == populated.count("${cap(") == 10
    for key in ("fast_exit", "single_device", "no_article_rate", "asked_rate",
                "whats_new_opened_rate", "median_duration_ms", "narration_on_rate"):
        assert key in body, key


def test_section_escapes_search_text_and_article_ids():
    render = _render_src()
    assert "${esc(x.q)}" in render and "${esc(a.id)}" in render
    assert "${x.q}" not in render and "${a.id}" not in render
    assert "${esc(x.title)}" in render and "${esc(x.body)}" in render


# --- the live log never keeps what was typed ---------------------------------
# The admin live log buffers raw request and response bodies BEFORE the guards
# above run, so an email typed into Help search would sit there in the clear.

def _live_log_text() -> str:
    import json as _json
    from app.middleware.request_logging import get_recent_logs
    return _json.dumps(get_recent_logs(50), ensure_ascii=False)


def test_the_live_log_never_holds_the_search_text_of_a_stored_event(client, tmp_db_path):
    body = _help_event(_help(searches=[
        {"q": "zebra crossing marker", "opened": True},
        {"q": "someone@example.com", "opened": False}]))
    assert _post(client, body).status_code == 204

    text = _live_log_text()
    assert "/v1/events/ping" in text, "positive control: the request was captured at all"
    assert "acct-plans" in text, "positive control: the body was captured, not skipped"
    assert "zebra crossing marker" not in text
    assert "someone@example.com" not in text


def test_the_live_log_never_holds_the_search_text_a_422_echoes(client, tmp_db_path):
    too_long = "quokka " * 30
    body = _help_event(_help(searches=[{"q": too_long, "opened": True}]))
    r = _post(client, body)
    assert r.status_code == 422
    assert "quokka" in r.text, "positive control: the 422 does echo the text to the caller"

    assert "quokka" not in _live_log_text()


def test_the_live_log_hides_search_text_when_a_422_echoes_the_whole_block(client, tmp_db_path):
    body = _help_event(_help(searched=False,
                                  searches=[{"q": "wombat settings", "opened": True}]))
    r = _post(client, body)
    assert r.status_code == 422
    assert "wombat settings" in r.text, "positive control"

    assert "wombat settings" not in _live_log_text()
