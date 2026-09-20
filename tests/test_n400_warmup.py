"""The interviewer lane's warm up: it must warm the entry a real turn READS.

The failure this file exists to catch is quiet: a warm up that builds "the
same prompt" by its own path, writes a cache entry nothing ever reads, costs
six cents and reports success. So the central test compares what the
Anthropic adapter would actually SEND for a warm up against what it sent for
the real turn before it, field by field.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from app.models.chat import ChatRequest, ChatResponse
from app.services import n400_warmup as w
from tests.test_n400_sentence_stream_route import ENVELOPE, _metadata

LANE_ROUTE = "app.services.anthropic_or_fallback.route_with_fallback"
HDR = {"X-App-ID": "n400"}


@pytest.fixture(autouse=True)
def fresh_state():
    for d in (w._PREFIXES, w._TOUCHED, w._LAST_WARMED_SHA, w._LAST_WARMUP_BY_USER):
        d.clear()
    yield


def _adapter():
    from app.services.providers.anthropic import AnthropicAdapter
    return AnthropicAdapter(api_key="t", base_url="https://api.anthropic.com/v1/messages",
                            auth_header="x-api-key", auth_prefix="")


def _real_turn(client, free_user, locale="en"):
    """One real interviewer turn. Returns the request the provider was handed."""
    from tests.conftest import chat_request
    seen = {}

    async def fake(provider_router, request, db, settings):
        seen["request"] = request
        return ChatResponse(text=json.dumps(ENVELOPE), input_tokens=100, output_tokens=50,
                            model="claude-sonnet-5", provider="anthropic",
                            usage={"input_tokens": 100, "output_tokens": 50})
    client.app.state.rate_limiter._buckets.clear()
    with patch(LANE_ROUTE, fake):
        r = client.post("/v1/chat", headers={**free_user["headers"], **HDR}, json=chat_request(
            system_prompt="", user_content="on my own, about six years",
            metadata=_metadata(locale=locale)))
    assert r.status_code == 200, r.text
    return seen["request"]


def _warm(client, free_user, locale="en", **extra):
    from tests.conftest import chat_request
    body = chat_request(system_prompt="", user_content="[warm up]",
                        metadata={"call_type": w.WARMUP_CALL_TYPE, "locale": locale,
                                  "form_code": "N-400", "jurisdiction": "US-TX"})
    body.update(extra)
    return client.post("/v1/chat", headers={**free_user["headers"], **HDR}, json=body)


def _cached(write=0, read=0):
    return ChatResponse(text="", input_tokens=9, output_tokens=0, model="claude-sonnet-5",
                        provider="anthropic",
                        usage={"input_tokens": 9, "output_tokens": 0,
                               "cache_creation_input_tokens": write,
                               "cache_read_input_tokens": read})


# --- the prefix ----------------------------------------------------------------------

def test_the_warm_up_sends_the_prefix_the_real_turn_sent(client, free_user, mock_provider):
    real = _real_turn(client, free_user)
    w._TOUCHED.clear()                     # let the cache look cold so the provider is called
    mock_provider.return_value = _cached(write=23893)
    r = _warm(client, free_user)
    assert r.json()["outcome"] == "written" and r.json()["cache_write_tokens"] == 23893

    (warm_request,) = [c.args[-1] for c in mock_provider.await_args_list]
    real_body, _ = _adapter()._build_body(real)
    warm_body, _ = _adapter()._build_body(warm_request)
    # Everything that decides which cache entry is touched:
    assert warm_body["model"] == real_body["model"]
    assert warm_body["system"] == real_body["system"], "a different system block is a different entry"
    assert warm_body.get("thinking") == real_body.get("thinking")
    assert warm_body.get("tools") == real_body.get("tools")
    assert warm_body["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # And what makes it a warm up and not a turn:
    assert warm_body["max_tokens"] == 0 and real_body["max_tokens"] > 0
    assert warm_request.stream is False


def test_a_plain_zero_would_have_been_a_full_generation():
    # The adapter reads `max_tokens or 4096`. Without the prewarm flag a warm
    # up is a 4,096 token reply, which is the bug the flag exists for.
    req = ChatRequest(provider="anthropic", model="claude-sonnet-5", system_prompt="s",
                      user_content="[warm up]", max_tokens=0)
    assert _adapter()._build_body(req)[0]["max_tokens"] == 4096
    assert _adapter()._build_body(req.model_copy(update={"prewarm": True}))[0]["max_tokens"] == 0


def test_a_client_cannot_set_prewarm(client, free_user):
    real = _real_turn(client, free_user)
    assert real.prewarm is False
    from tests.conftest import chat_request
    seen = {}

    async def fake(provider_router, request, db, settings):
        seen["request"] = request
        return ChatResponse(text=json.dumps(ENVELOPE), input_tokens=1, output_tokens=1,
                            model="claude-sonnet-5", provider="anthropic", usage={})
    client.app.state.rate_limiter._buckets.clear()
    with patch(LANE_ROUTE, fake):
        client.post("/v1/chat", headers={**free_user["headers"], **HDR}, json=chat_request(
            system_prompt="", user_content="x", prewarm=True, metadata=_metadata()))
    assert seen["request"].prewarm is False


def test_the_prefix_is_per_locale_and_survives_a_restart(client, free_user, mock_provider, tmp_db_path):
    _real_turn(client, free_user, locale="en")
    rows = sqlite3.connect(tmp_db_path).execute(
        "select lane_key, length(system_prompt) from warm_prefixes").fetchall()
    assert [k for k, _ in rows] == ["n400|n400_interviewer_turn|en"] and rows[0][1] > 1000
    # Spanish has had no real turn: nothing to copy, nothing spent.
    assert _warm(client, free_user, locale="es").json() == {"warmed": False, "reason": "no_prefix_yet"}
    mock_provider.assert_not_awaited()
    # A restart forgets process memory. The table does not.
    for d in (w._PREFIXES, w._TOUCHED, w._LAST_WARMUP_BY_USER):
        d.clear()
    mock_provider.return_value = _cached(read=23893)
    assert _warm(client, free_user, locale="en").json()["outcome"] == "already_warm"
    mock_provider.assert_awaited_once()


# --- the three ways it spends nothing -------------------------------------------------------

def test_a_real_turn_minutes_ago_means_already_warm_and_no_provider_call(client, free_user, mock_provider):
    _real_turn(client, free_user)
    body = _warm(client, free_user).json()
    assert body["warmed"] is True and body["outcome"] == "already_warm"
    assert "seconds_since_last_call" in body
    mock_provider.assert_not_awaited()


def test_one_warm_up_per_user_per_throttle_window(client, free_user, mock_provider):
    _real_turn(client, free_user)
    w._TOUCHED.clear()
    mock_provider.return_value = _cached(write=23893)
    assert _warm(client, free_user).json()["outcome"] == "written"
    w._TOUCHED.clear()                     # even if the cache looked cold again
    body = _warm(client, free_user).json()
    assert body["outcome"] == "skipped_recent" and 0 < body["retry_after_seconds"] <= w.THROTTLE_SECONDS
    mock_provider.assert_awaited_once()


def test_nothing_recorded_yet_spends_nothing(client, free_user, mock_provider):
    assert _warm(client, free_user).json() == {"warmed": False, "reason": "no_prefix_yet"}
    mock_provider.assert_not_awaited()


# --- it is not a turn ------------------------------------------------------------------------------

def test_it_is_not_rate_limited_and_does_not_use_up_the_limit(client, free_user, mock_provider):
    limiter = client.app.state.rate_limiter
    limiter._buckets.clear()
    while limiter.check(free_user["user_id"], 5)[0]:
        pass
    assert _warm(client, free_user).status_code == 200     # a real turn here is a 429
    limiter._buckets.clear()
    for _ in range(3):
        _warm(client, free_user)
    assert limiter._buckets.get(free_user["user_id"], []) == []


def test_it_writes_no_turn_row_and_its_spend_is_logged_under_its_own_name(
        client, free_user, mock_provider, tmp_db_path):
    _real_turn(client, free_user)
    before = sqlite3.connect(tmp_db_path).execute(
        "select monthly_used_usd from users where id=?", (free_user["user_id"],)).fetchone()[0]
    w._TOUCHED.clear()
    mock_provider.return_value = _cached(write=23893)
    _warm(client, free_user, turn_id="c1-t_001")
    conn = sqlite3.connect(tmp_db_path)
    assert conn.execute("select count(*) from chat_turns").fetchone()[0] == 0
    rows = conn.execute("select call_type, output_tokens, status from usage_log "
                        "where call_type like 'n400_interviewer_w%'").fetchall()
    assert rows == [(w.WARMUP_CALL_TYPE, 0, "success")]
    after = conn.execute("select monthly_used_usd from users where id=?",
                         (free_user["user_id"],)).fetchone()[0]
    assert after == before, "the warm up must not be deducted from her allocation"


def test_it_never_falls_back_to_another_provider(client, free_user, mock_provider):
    _real_turn(client, free_user)
    w._TOUCHED.clear()
    mock_provider.side_effect = RuntimeError("anthropic is down")
    with patch(LANE_ROUTE) as fallback:
        r = _warm(client, free_user)
    assert r.status_code == 200 and r.json() == {"warmed": False, "reason": "failed"}
    fallback.assert_not_called()


def test_a_write_of_zero_and_a_read_of_zero_is_not_called_warm(client, free_user, mock_provider, caplog):
    _real_turn(client, free_user)
    w._TOUCHED.clear()
    mock_provider.return_value = _cached()
    body = _warm(client, free_user).json()
    assert body["warmed"] is False and body["outcome"] == "cached_nothing"
    assert any("n400_warmup_cached_nothing" in r.getMessage() for r in caplog.records)


# --- the drop is audible ---------------------------------------------------------------------------------

def test_a_real_turn_whose_prefix_differs_from_the_one_warmed_says_so(client, free_user, mock_provider, caplog):
    _real_turn(client, free_user)
    w._TOUCHED.clear()
    mock_provider.return_value = _cached(write=23893)
    _warm(client, free_user)
    # The next real turn sends a different prefix: a new prompt cut, or drift.
    key = w.lane_key("n400", "en")
    w._LAST_WARMED_SHA[key] = "0" * 32
    _real_turn(client, free_user)
    assert any("n400_warmup_prefix_mismatch" in r.getMessage() for r in caplog.records)


def test_a_failure_in_the_bookkeeping_cannot_fail_a_real_turn(client, free_user, monkeypatch):
    monkeypatch.setattr(w, "_sha", lambda *a: (_ for _ in ()).throw(KeyError("bug")))
    _real_turn(client, free_user)          # asserts 200 inside
