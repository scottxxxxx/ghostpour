"""Tests for the client-config resolver and the Project Chat
locale-aware char-cap enforcement."""

from __future__ import annotations

from app.services.client_config import project_chat_max_input_chars


def _configs(en_caps: dict | None = None, ja_caps: dict | None = None) -> dict:
    """Build a minimal fake remote_configs dict mirroring what
    `app.routers.config.load_remote_configs` produces."""
    out = {}
    if en_caps is not None:
        out["client-config"] = {
            "version": 1,
            "limits": {"project_chat": {"max_input_chars": en_caps}},
        }
    if ja_caps is not None:
        out["client-config.ja"] = {
            "version": 1,
            "limits": {"project_chat": {"max_input_chars": ja_caps}},
        }
    return out


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def test_default_locale_returns_english_caps():
    cfg = _configs(en_caps={"free": 200000, "plus": 600000, "pro": 720000})
    assert project_chat_max_input_chars(cfg, "free") == 200000
    assert project_chat_max_input_chars(cfg, "plus") == 600000
    assert project_chat_max_input_chars(cfg, "pro") == 720000


def test_explicit_en_locale_uses_default_file():
    cfg = _configs(en_caps={"plus": 600000})
    # "en" is not a special slug — the resolver treats it as "use default"
    assert project_chat_max_input_chars(cfg, "plus", locale="en") == 600000


def test_japanese_locale_uses_locale_specific_caps():
    cfg = _configs(
        en_caps={"free": 200000, "plus": 600000, "pro": 720000},
        ja_caps={"free": 100000, "plus": 300000, "pro": 360000},
    )
    assert project_chat_max_input_chars(cfg, "plus", locale="ja") == 300000
    assert project_chat_max_input_chars(cfg, "pro", locale="ja") == 360000


def test_unknown_locale_falls_back_to_default():
    cfg = _configs(
        en_caps={"plus": 600000},
        ja_caps={"plus": 300000},
    )
    # Korean isn't configured — falls through to default English file.
    assert project_chat_max_input_chars(cfg, "plus", locale="ko") == 600000


def test_unknown_tier_returns_fallback():
    cfg = _configs(en_caps={"free": 200000, "plus": 600000})
    assert (
        project_chat_max_input_chars(cfg, "enterprise", fallback_chars=999_999)
        == 999_999
    )
    assert project_chat_max_input_chars(cfg, "enterprise") is None


def test_missing_client_config_returns_fallback():
    assert (
        project_chat_max_input_chars({}, "plus", fallback_chars=600_000)
        == 600_000
    )
    assert project_chat_max_input_chars({}, "plus") is None


def test_locale_variant_missing_tier_does_not_merge_with_default():
    """A locale variant is self-contained. If it lacks the tier we want,
    we DO NOT walk back to the default file — we go straight to fallback.
    Mirrors how /v1/config/{name} serves whichever locale file exists."""
    cfg = _configs(
        en_caps={"plus": 600000, "pro": 720000},
        ja_caps={"plus": 300000},  # no `pro` in ja file
    )
    # Pro on Japanese: ja file is selected, doesn't have pro, returns fallback.
    assert (
        project_chat_max_input_chars(cfg, "pro", locale="ja", fallback_chars=42)
        == 42
    )


def test_uncapped_minus_one_is_returned_verbatim():
    """-1 means uncapped. Caller compares with `cap != -1`, so the
    resolver must surface -1 not coerce it to None."""
    cfg = _configs(en_caps={"admin": -1})
    assert project_chat_max_input_chars(cfg, "admin") == -1
