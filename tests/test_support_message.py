"""POST /v1/support/message: SS Help "Report a problem" (SS Dev, 2026-11-19)."""
import pytest

from app.routers import support
from app.services.email_send import SendResult

BODY = {"message": "  The memory tab crashes when I tap a voice.  ", "reply_email": "me@example.com",
        "source": "help_article", "article_id": "memory-voice-id", "app": "ios",
        "app_version": "1.20 (2617)", "os_version": "iOS 26.1", "device": "iPhone", "locale": "en-US"}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(db, **kw):
        calls.append(kw)
        return SendResult(sent=True, resend_id="r1")
    monkeypatch.setattr(support, "send_email", fake_send)
    support._hits.clear()
    return calls


def test_anonymous_send_echoes_reply_email_and_sets_reply_to(client, sent):
    r = client.post("/v1/support/message", json=BODY)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "sent" and j["reply_email"] == "me@example.com" and j["id"]
    kw = sent[0]
    assert kw["to"] == "support@shouldersurf.com" and kw["reply_to"] == "me@example.com"
    assert kw["subject"].startswith("[SS Help] The memory tab crashes")
    assert "memory-voice-id" in kw["text"] and "user_id" not in kw["text"]


def test_signed_in_attaches_user_and_tier(client, sent, pro_user):
    r = client.post("/v1/support/message", json=BODY, headers=pro_user["headers"])
    assert r.status_code == 200
    assert f"user_id: {pro_user['user_id']}" in sent[0]["text"] and "tier: pro" in sent[0]["text"]


def test_bad_token_is_anonymous_not_401(client, sent):
    r = client.post("/v1/support/message", json=BODY, headers={"Authorization": "Bearer junk"})
    assert r.status_code == 200 and "user_id" not in sent[0]["text"]


def test_no_reply_email_echoes_null(client, sent):
    r = client.post("/v1/support/message", json={**BODY, "reply_email": None})
    assert r.json()["reply_email"] is None and sent[0]["reply_to"] is None


@pytest.mark.parametrize("msg", ["", "   ", "x" * 4001, 5])
def test_invalid_message(client, sent, msg):
    r = client.post("/v1/support/message", json={**BODY, "message": msg})
    assert r.status_code == 400 and r.json() == {"error": "invalid_message"} and not sent


@pytest.mark.parametrize("em", ["nope", "a@b", "two@@x.com", ""])
def test_invalid_email(client, sent, em):
    r = client.post("/v1/support/message", json={**BODY, "reply_email": em})
    assert r.status_code == 400 and r.json() == {"error": "invalid_email"} and not sent


def test_rate_limited_after_five_an_hour(client, sent):
    codes = [client.post("/v1/support/message", json=BODY).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_send_failure_is_5xx(client, monkeypatch):
    async def fail(db, **kw):
        return SendResult(sent=False, skipped_reason="no_api_key")
    monkeypatch.setattr(support, "send_email", fail)
    support._hits.clear()
    assert client.post("/v1/support/message", json=BODY).status_code == 502


def test_message_body_never_logged(client, sent, caplog):
    caplog.set_level("INFO")
    client.post("/v1/support/message", json=BODY)
    assert "memory tab crashes" not in caplog.text
