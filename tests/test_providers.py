"""Unit tests for provider request building (not live API calls)."""

from app.models.chat import ChatRequest
from app.services.providers.openai_compat import OpenAICompatAdapter


def test_openai_text_only_content():
    """Text-only request should produce a string user content, not array."""
    request = ChatRequest(
        provider="openai",
        model="gpt-5.2",
        system_prompt="You are helpful.",
        user_content="Hello world",
    )
    content = OpenAICompatAdapter._build_user_content(request)
    assert isinstance(content, str)
    assert content == "Hello world"


def test_openai_image_content():
    """Request with images should produce multipart content array."""
    request = ChatRequest(
        provider="openai",
        model="gpt-5.2",
        system_prompt="You are helpful.",
        user_content="Describe this image",
        images=["abc123base64"],
    )
    content = OpenAICompatAdapter._build_user_content(request)
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert "abc123base64" in content[1]["image_url"]["url"]


def test_base64_redaction():
    """Long base64 strings should be redacted in raw JSON."""
    from app.services.providers.base import ProviderAdapter

    long_b64 = "A" * 200
    json_str = f'{{"data": "{long_b64}"}}'
    redacted = ProviderAdapter._redact_base64(json_str)
    assert "[BASE64_REDACTED]" in redacted
    assert long_b64 not in redacted


def test_short_data_not_redacted():
    """Short data strings should not be redacted."""
    from app.services.providers.base import ProviderAdapter

    json_str = '{"data": "short"}'
    redacted = ProviderAdapter._redact_base64(json_str)
    assert redacted == json_str
