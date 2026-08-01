"""Redaction behavior of the request-logging middleware.

Secrets must be redacted in full (no prefix kept — a 20-char prefix of a
password can be the whole value), and key matching is substring-based so new
sensitive wire fields are redacted by default.

User-authored content (résumés, job descriptions, transcripts) is redacted
on the same terms as of 2026-08-01: GP persists none of it, and the debug
ring buffer was the one place it survived a request.
"""

from app.middleware.request_logging import _format_body_parsed, _redact_sensitive


def test_known_sensitive_fields_fully_redacted():
    body = {
        "identity_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjEifQ.payload.sig",
        "access_token": "at_1234567890abcdefghijklmnop",
        "refresh_token": "rt_1234567890abcdefghijklmnop",
        "client_secret": "cs_secret_value_here",
        "password": "hunter2",
        "signed_transaction": "MIIBIjANBgkqhkiG9w0BAQ",
    }
    _redact_sensitive(body)
    for key in body:
        assert body[key] == "<redacted>", f"{key} not fully redacted: {body[key]}"


def test_short_password_leaves_no_prefix():
    body = {"password": "shortpw"}
    _redact_sensitive(body)
    assert "shortpw" not in str(body)


def test_substring_match_catches_new_fields():
    body = {
        "api_key": "sk-or-v1-abc123",
        "providerKey": "sk-ant-xyz",
        "X-Admin-Key": "adminsecret",
        "sessionToken": "tok_abc",
        "webhook_secret": "whsec_123",
    }
    _redact_sensitive(body)
    for key in body:
        assert body[key] == "<redacted>", f"{key} not redacted: {body[key]}"


def test_non_string_values_untouched():
    body = {"max_tokens": 4096, "usage": {"prompt_tokens": 12, "completion_tokens": 34}}
    _redact_sensitive(body)
    assert body == {"max_tokens": 4096, "usage": {"prompt_tokens": 12, "completion_tokens": 34}}


def test_non_sensitive_strings_untouched():
    body = {"model": "claude-haiku-4-5", "provider": "anthropic", "user_id": "u_123"}
    _redact_sensitive(body)
    assert body == {"model": "claude-haiku-4-5", "provider": "anthropic", "user_id": "u_123"}


def test_user_authored_content_redacted():
    """Résumés, JDs and transcripts are never persisted by GP; they must
    not survive in the debug ring buffer either (TR privacy review,
    2026-08-01)."""
    body = {
        "user_content": "SCOTT GUIDA — Staff Engineer — scott@example.com",
        "system_prompt": "You are rehearsing an interview for...",
        "reference_text": "Job description: we are hiring a...",
        "transcript": "hello world",
        "raw_transcript": "uh, hello, world",
        "raw_request_json": '{"messages":[{"content":"my resume"}]}',
        "raw_response_json": '{"content":"feedback on my resume"}',
    }
    _redact_sensitive(body)
    for key in body:
        assert body[key] == "<redacted>", f"{key} not redacted: {body[key]}"


def test_attachment_payloads_replaced_with_count():
    """Base64 document/image bytes (a résumé PDF arrives this way) are
    swapped for a count — enough to debug, nothing to leak."""
    body = {
        "images": ["iVBORw0KGgoAAAANS", "iVBORw0KGgoAAAANT"],
        "documents": [{"name": "resume.pdf", "media_type": "application/pdf",
                       "data": "JVBERi0xLjcKJc..."}],
    }
    _redact_sensitive(body)
    assert body["images"] == "<redacted: 2 item(s)>"
    assert body["documents"] == "<redacted: 1 item(s)>"
    assert "JVBERi0xLjcK" not in str(body)


def test_nested_dicts_and_lists_redacted():
    body = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"metadata": {"refresh_token": "rt_nested_secret"}},
        ]
    }
    _redact_sensitive(body)
    assert body["messages"][1]["metadata"]["refresh_token"] == "<redacted>"
    assert body["messages"][0]["content"] == "hi"


def test_format_body_parsed_redacts_json():
    out = _format_body_parsed('{"password": "hunter2", "name": "scott"}')
    assert out == {"password": "<redacted>", "name": "scott"}
