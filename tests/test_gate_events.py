"""Feature-gate CTA events (2026-08-04, SS ask).

Gate CTAs reported nothing, so the copy we most wanted to A/B test was the
copy we could not measure. Campaigns already had weighted variants and
four event types; feature gates had a single string and no telemetry.

SS could not build it from our side of the contract: campaign_id was
required AND is the conflict key for the frequency-cap row, so a gate
event had nothing to write against.

The impression definition is deliberately the narrow one: the user tried
to use a gated feature, we blocked them, and showed the ask. Not the
paywall opening (a user browsing from Settings never wanted the feature
and pollutes the denominator) and not CTA text rendering in a comparison
table (a glance, not an ask). The only rate that means anything is "of the
people who wanted this and were told no, how many tapped".
"""

import sqlite3

SS = {"X-App-ID": "shouldersurf"}
DEV = "11111111-1111-4111-8111-111111111111"


def _db(app_env: dict) -> str:
    return app_env["CZ_DATABASE_URL"].split("///")[-1]


def _events(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT event_type, campaign_id, variant_id, feature, surface "
            "FROM promo_events ORDER BY created_at")]
    finally:
        conn.close()


def _presentations(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM promo_presentations").fetchone()[0]
    finally:
        conn.close()


def test_a_gate_event_needs_no_campaign(client, app_env):
    r = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "feature": "context_quilt", "surface": "memory_tab"})
    assert r.status_code == 204
    rows = _events(_db(app_env))
    assert len(rows) == 1
    assert rows[0]["feature"] == "context_quilt"
    assert rows[0]["surface"] == "memory_tab"
    assert rows[0]["campaign_id"] is None


def test_a_gate_event_does_not_touch_frequency_capping(client, app_env):
    """Capping answers 'have we shown this card enough'. A gate has no card
    and no cap, so writing a presentations row would invent a campaign that
    does not exist and could suppress a real one."""
    client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV, "feature": "project_chat"})
    assert _presentations(_db(app_env)) == 0


def test_campaign_events_still_cap_as_before(client, app_env):
    """The existing path must be untouched: this is additive."""
    r = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "campaign_id": "camp-1", "variant_id": "native"})
    assert r.status_code == 204
    assert _presentations(_db(app_env)) == 1


def test_a_campaign_supplied_gate_cta_carries_both_keys(client, app_env):
    """When a campaign supplies the copy for a gate, the event carries the
    campaign AND the feature, which is what makes the variant arm and the
    baseline arm comparable on one key."""
    client.post("/v1/promo/events", headers=SS, json={
        "event_type": "click", "device_id": DEV, "campaign_id": "camp-1",
        "variant_id": "b", "feature": "context_quilt", "surface": "memory_tab"})
    row = _events(_db(app_env))[-1]
    assert row["campaign_id"] == "camp-1"
    assert row["variant_id"] == "b"
    assert row["feature"] == "context_quilt"


def test_an_event_about_nothing_is_rejected(client, app_env):
    """Worse than no event: it inflates a denominator and cannot be
    attributed to anything."""
    r = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV})
    assert r.status_code == 422
    assert _events(_db(app_env)) == []


def test_the_event_vocabulary_is_unchanged(client, app_env):
    r = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "not_a_thing", "device_id": DEV, "feature": "search"})
    assert r.status_code == 400
