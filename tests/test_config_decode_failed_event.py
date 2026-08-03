"""Client-reported config decode failures (2026-08-02).

SS ships a `config_decode_failed` telemetry event when a served config
will not decode. Before this, the endpoint's event_type was a closed
Literal union, so their shipped instrumentation was rejected outright and
the one signal that names WHICH field broke was being dropped.

This is the precise counterpart to config_stalls: that infers a problem
from a client whose config version never advances, this is the client
saying what broke and where. Both are kept, because build 803 is frozen
and will never send this event.
"""

import sqlite3
import uuid

SS = {"X-App-ID": "shouldersurf"}


def _dev(seed: int = 0) -> str:
    """device_id must be UUID-shaped; the endpoint rejects anything else."""
    return str(uuid.UUID(int=seed, version=4))


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _failures(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM config_decode_failures")]
    finally:
        conn.close()


def _incidents(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT subject, details_json FROM alert_incidents "
            "WHERE category='config_decode_loop'").fetchall()
    finally:
        conn.close()


def _event(client, *, file="llm-providers", reason=None, build="906",
           device=None):
    return client.post("/v1/events/ping", headers=SS, json={
        "event_type": "config_decode_failed",
        "device_id": device or _dev(1),
        "app_version": "1.15",
        "app_build": build,
        "os_version": "18.0",
        "config_decode": {
            "file": file,
            "reason": reason or "typeMismatch expected=String at=providers[0].apiFormat",
            "byte_count": 19197,
        },
    })


def test_event_is_accepted_and_recorded(client, app_env):
    assert _event(client).status_code == 204
    rows = _failures(_db(app_env))
    assert len(rows) == 1
    assert rows[0]["config_name"] == "llm-providers"
    assert rows[0]["app_build"] == "906"
    assert rows[0]["byte_count"] == 19197
    assert "providers[0].apiFormat" in rows[0]["reason"]


def test_it_alerts_immediately(client, app_env):
    """This event is proof, not inference, so it does not wait for
    repetition the way the version-stall detector has to."""
    _event(client)
    rows = _incidents(_db(app_env))
    assert len(rows) == 1
    assert "llm-providers" in rows[0][1]
    assert "client_report" in rows[0][1]


def test_a_fleet_hitting_one_bad_field_is_one_incident(client, app_env):
    """Dedup is on what needs fixing, not on who reported it, so a bad
    field does not produce one email per device."""
    for i in range(5):
        _event(client, device=_dev(i))
    assert len(_failures(_db(app_env))) == 5      # every report kept
    assert len(_incidents(_db(app_env))) == 1     # one thing to fix


def test_different_faults_are_different_incidents(client, app_env):
    _event(client, file="llm-providers", reason="typeMismatch expected=String at=providers[0].apiFormat")
    _event(client, file="tiers", reason="keyNotFound at=feature_definitions.people")
    assert len(_incidents(_db(app_env))) == 2


def test_payload_is_required_for_this_event(client, app_env):
    r = client.post("/v1/events/ping", headers=SS, json={
        "event_type": "config_decode_failed", "device_id": _dev(2)})
    assert r.status_code == 422


def test_payload_is_rejected_on_other_events(client, app_env):
    """Keeps the two shapes from bleeding into each other, same rule the
    onboarding payload already follows."""
    r = client.post("/v1/events/ping", headers=SS, json={
        "event_type": "app_start", "device_id": _dev(3),
        "config_decode": {"file": "tiers", "reason": "x"}})
    assert r.status_code == 422


def test_reason_is_length_capped(client, app_env):
    """The client contract says the reason is schema-only and never carries
    a decoded value. We cap it rather than parse it, so a formatter change
    on their side cannot turn this into a content channel."""
    r = client.post("/v1/events/ping", headers=SS, json={
        "event_type": "config_decode_failed", "device_id": _dev(4),
        "config_decode": {"file": "tiers", "reason": "x" * 5000}})
    assert r.status_code == 422


def test_lifecycle_events_still_work(client, app_env):
    """The union got a new member; the existing ones must be untouched."""
    for et in ("app_start", "meeting_start", "meeting_stop"):
        r = client.post("/v1/events/ping", headers=SS,
                        json={"event_type": et, "device_id": _dev(5)})
        assert r.status_code == 204, et
    assert _failures(_db(app_env)) == []
