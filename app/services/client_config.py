"""Resolver for the client-config remote config file.

`config/remote/client-config.json` (and locale variants) holds runtime
tunables that iOS reads at app launch — char caps, feature flags,
intervals — anything we want to change without an iOS update.

Locale resolution mirrors the `/v1/config/{name}` endpoint:
- If the request carries `Accept-Language: ja`, prefer
  `client-config.ja` over the default `client-config`.
- iOS fetches `/v1/config/client-config` with its own
  `Accept-Language` header and gets the right variant served back.
- Server enforcement (e.g., Project Chat 413) calls these helpers
  with the same locale so server limits and iOS gauge stay aligned.

Each locale file is self-contained — no overlay/merge logic. Keeps
edits in the dashboard simple (change one file, see one number).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _resolve_config(
    remote_configs: dict[str, dict],
    name: str,
    locale: str | None,
) -> dict | None:
    """Return the locale-specific config if present, else the default.

    `locale` is a 2-letter language code (e.g. "ja", "es"). None or "en"
    falls through to the default `name`. If the locale variant exists
    but lacks the value we're looking for, callers do NOT merge with
    the default — they fall back to whatever fallback was passed in.
    """
    if locale and locale != "en":
        loc_key = f"{name}.{locale}"
        if loc_key in remote_configs:
            return remote_configs[loc_key]
    return remote_configs.get(name)


def project_chat_max_input_chars(
    remote_configs: dict[str, dict],
    tier: str,
    locale: str | None = None,
    *,
    fallback_chars: int | None = None,
) -> int | None:
    """Per-tier Project Chat character cap, locale-aware.

    Path: `limits.project_chat.max_input_chars[tier]`.

    Returns `fallback_chars` (which may be None) if the cap can't be
    resolved. -1 means uncapped — preserve it; the caller compares
    `len(text) > cap_chars` with a `cap_chars != -1` guard.
    """
    cfg = _resolve_config(remote_configs, "client-config", locale)
    if cfg:
        cap = (
            cfg.get("limits", {})
            .get("project_chat", {})
            .get("max_input_chars", {})
            .get(tier)
        )
        if cap is not None:
            return int(cap)
    return fallback_chars
