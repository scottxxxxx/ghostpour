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


# --- why the user was blocked (2026-08-04, SS) -----------------------
#
# The shape could tell campaign arm from baseline but not why the block
# happened. People is served as an intelligence feature with "Sign in to
# build your People list", so a signed-out user hitting it is a legitimate
# impression under our definition, and it arrived looking identical to a
# Free user hitting Project Chat. The funnel ends in "subscribed" and
# signing in is not subscribing, so those are different denominators.


def _reasons(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT block_reason FROM promo_events ORDER BY created_at")]
    finally:
        conn.close()


def test_a_signed_out_block_is_distinguishable_from_a_tier_block(client, app_env):
    client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "feature": "people", "block_reason": "signed_out"})
    client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "feature": "project_chat", "block_reason": "tier"})
    assert _reasons(_db(app_env)) == ["signed_out", "tier"]


def test_quota_is_its_own_reason(client, app_env):
    """The plan includes the feature and this period is spent. Neither an
    upgrade nor a sign-in is the right ask, so it cannot share either
    denominator."""
    client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "feature": "context_quilt", "block_reason": "quota"})
    assert _reasons(_db(app_env)) == ["quota"]


def test_the_reason_is_optional_and_open(client, app_env):
    """A plain string, not an enum: widening a vocabulary is safe for every
    shipped build, retyping a field is not. An unknown value is recorded
    rather than rejected so a client can ship a new reason before we do."""
    r = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV, "feature": "search"})
    assert r.status_code == 204
    r2 = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "feature": "search", "block_reason": "something_new"})
    assert r2.status_code == 204
    assert _reasons(_db(app_env)) == [None, "something_new"]


# --- which key names the block (2026-08-04, SS) -----------------------
#
# SS asked whether `feature` is validated against the entitlement keys.
# It is not, and it must not be: a zero-credit user stopped at the first
# orb tap is a budget block, not an entitlement, and there is no
# entitlement key for "asked the AI". A closed vocabulary there would
# make budget gates dark, which is the gate that stops a free user
# soonest.
#
# The key to send is the one our own block response already puts in
# `feature_state.feature`, so neither side has a list to keep in sync.


def _features(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT feature FROM promo_events ORDER BY created_at")]
    finally:
        conn.close()


def test_an_unrecognized_feature_is_recorded_not_rejected(client, app_env):
    """Widenable like block_reason. A client must be able to report a gate
    we have not named yet; the alternative is silence, and silence reads
    as "nobody hit that gate"."""
    r = client.post("/v1/promo/events", headers=SS, json={
        "event_type": "impression", "device_id": DEV,
        "feature": "a_gate_we_have_not_named_yet", "block_reason": "quota"})
    assert r.status_code == 204
    assert _features(_db(app_env)) == ["a_gate_we_have_not_named_yet"]


def test_the_budget_block_names_its_own_feature(client, app_env):
    """The half that makes the answer above safe rather than sloppy. Our
    402-equivalent block payload carries feature_state.feature, so the
    client echoes a key we chose instead of inventing one. If this ever
    stops being emitted, the advice in the wire contract is wrong."""
    import ast
    import pathlib
    src = pathlib.Path("app/routers/chat.py").read_text()
    assert '"feature": "chat" if not is_project_chat_pre else "project_chat"' in src, (
        "the budget block no longer names its feature; docs/wire-contracts/"
        "gate-events.md tells SS to echo feature_state.feature")
    for key in ("chat", "project_chat", "meeting_report", "search"):
        r = client.post("/v1/promo/events", headers=SS, json={
            "event_type": "impression", "device_id": DEV,
            "feature": key, "block_reason": "quota"})
        assert r.status_code == 204, key
