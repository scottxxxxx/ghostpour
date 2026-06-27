"""
Context Quilt integration for GhostPour.

Handles two flows:
  1. Recall: Before sending a query to the LLM, fetch relevant context from CQ
  2. Capture: After the LLM responds, send query+response to CQ for learning

Both flows are controlled by the `context_quilt: true` flag in ChatRequest.

Auth: If CQ_CLIENT_SECRET is set, uses JWT bearer tokens (auto-refreshing).
Otherwise falls back to X-App-ID header (legacy, for backwards compat).
"""

import logging
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Shared HTTP client (created on first use)
_client: httpx.AsyncClient | None = None

# JWT token cache, keyed by CQ app_id — per-app identities each cache their own.
_tokens: dict[str, tuple[str, float]] = {}  # cq_app_id -> (token, expires_at)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.cq_base_url,
            timeout=httpx.Timeout(5.0),  # General timeout; recall uses its own
        )
    return _client


def _cq_identity(app_id: str | None) -> tuple[str, str]:
    """The (cq_app_id, cq_client_secret) GP authenticates to CQ with for a given
    GP app. Per-app identities (apps.yml apps.<id>.cq) let a second CQ app (Tech
    Rehearsal, its own CQ app_id) ride GP under its own identity so CQ loads the
    right schema. Falls back to the default (ShoulderSurf / ghostpour) identity."""
    settings = get_settings()
    default = (settings.cq_app_id, settings.cq_client_secret)
    if not app_id:
        return default
    try:
        from app.routers.config import load_apps
        entry = (load_apps().get("apps", {}).get(app_id.strip().lower()) or {}).get("cq") or {}
    except Exception:
        return default
    cq_app = entry.get("app_id")
    if not cq_app:
        return default
    secret = getattr(settings, entry.get("secret_setting") or "", "") or ""
    return (cq_app, secret)


async def _get_auth_headers(app_id: str | None = None) -> dict[str, str]:
    """Auth headers for CQ requests, for the CQ identity of `app_id` (the default
    identity when None). JWT bearer when a secret is configured, else X-App-ID."""
    cq_app, cq_secret = _cq_identity(app_id)

    if not cq_secret:
        # Legacy / not-yet-provisioned: forward the app tag (CQ may accept it).
        return {"X-App-ID": cq_app}

    # JWT auth, cached per CQ app: refresh if expired or within the 30s buffer.
    cached = _tokens.get(cq_app)
    if cached and time.time() < cached[1] - 30:
        return {"Authorization": f"Bearer {cached[0]}"}

    try:
        client = _get_client()
        resp = await client.post(
            "/v1/auth/token",
            data={"username": cq_app, "password": cq_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_data = resp.json()
        token = token_data["access_token"]
        _tokens[cq_app] = (token, time.time() + token_data.get("expires_in", 3600))
        logger.info("cq_token_refreshed", extra={"cq_app": cq_app, "expires_in": token_data.get("expires_in")})
        return {"Authorization": f"Bearer {token}"}

    except Exception as e:
        logger.warning("cq_token_error", extra={"error": str(e), "cq_app": cq_app})
        return {"X-App-ID": cq_app}


async def recall(
    user_id: str,
    text: str,
    metadata: dict | None = None,
    subscription_tier: str | None = None,
) -> dict:
    """
    Fetch relevant context from Context Quilt's graph memory.

    Returns:
        {
            "context": "formatted text block",
            "matched_entities": ["entity names"],
            "patch_count": int
        }
    Returns empty result on timeout, error, or if CQ is not configured.
    """
    settings = get_settings()
    if not settings.cq_base_url:
        return {"context": "", "matched_entities": [], "patch_count": 0}

    timeout_sec = settings.cq_recall_timeout_ms / 1000.0

    try:
        client = _get_client()
        body: dict[str, Any] = {
            "user_id": user_id,
            "text": text,
        }
        merged_metadata = dict(metadata) if metadata else {}
        if subscription_tier:
            merged_metadata["subscription_tier"] = subscription_tier
        if merged_metadata:
            body["metadata"] = merged_metadata

        auth_headers = await _get_auth_headers()
        resp = await client.post(
            "/v1/recall",
            json=body,
            headers=auth_headers,
            timeout=httpx.Timeout(timeout_sec),
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "cq_recall_ok",
            extra={
                "matched": len(result.get("matched_entities", [])),
                "patch_count": result.get("patch_count", 0),
            },
        )
        return result

    except httpx.TimeoutException:
        logger.warning("cq_recall_timeout", extra={"timeout_ms": settings.cq_recall_timeout_ms})
        return {"context": "", "matched_entities": [], "patch_count": 0}
    except Exception as e:
        logger.warning("cq_recall_error", extra={"error": str(e)})
        return {"context": "", "matched_entities": [], "patch_count": 0}


async def capture(
    user_id: str,
    interaction_type: str,
    content: str,
    response: str | None = None,
    origin_id: str | None = None,
    origin_type: str | None = None,
    meeting_id: str | None = None,  # DEPRECATED — use origin_id + origin_type
    project: str | None = None,
    project_id: str | None = None,
    call_type: str | None = None,
    prompt_mode: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    user_identified: bool | None = None,
    user_label: str | None = None,
    identification_source: str | None = None,
    subscription_tier: str | None = None,
    language: str | None = None,
):
    """
    Send query+response to Context Quilt for learning. Fire-and-forget (async).

    This runs in the background after the LLM response is returned to the user.
    Never blocks the response.

    Origin scoping: CQ v1 replaced meeting_id with (origin_id, origin_type).
    Callers should pass origin_id + origin_type directly. The meeting_id arg
    is retained as a deprecated alias — when supplied, it's forwarded as
    origin_id with origin_type="meeting".
    """
    settings = get_settings()
    if not settings.cq_base_url:
        return

    body: dict[str, Any] = {
        "user_id": user_id,
        "interaction_type": interaction_type,
        "content": content,
    }
    if response:
        body["response"] = response

    # Normalize origin: prefer explicit origin_id/origin_type; fall back to
    # translating the deprecated meeting_id alias.
    if origin_id is None and meeting_id is not None:
        origin_id = meeting_id
        origin_type = origin_type or "meeting"

    # Build metadata from available fields
    metadata: dict[str, Any] = {}
    if origin_id:
        metadata["origin_id"] = origin_id
    if origin_type:
        metadata["origin_type"] = origin_type
    if project:
        metadata["project"] = project
    if project_id:
        metadata["project_id"] = project_id
    if call_type:
        metadata["call_type"] = call_type
    if prompt_mode:
        metadata["prompt_mode"] = prompt_mode
    if display_name:
        metadata["display_name"] = display_name
    if email:
        metadata["email"] = email
    if user_identified is not None:
        metadata["user_identified"] = user_identified
    if user_label:
        metadata["user_label"] = user_label
    if identification_source:
        metadata["identification_source"] = identification_source
    if subscription_tier:
        metadata["subscription_tier"] = subscription_tier
    # BCP-47 tag (full tags fine, e.g. "es-US"). CQ writes extracted memory
    # text in this language; when absent it infers from the speaker's words,
    # which guesses wrong in mixed-language meetings.
    if language:
        metadata["language"] = language
    if metadata:
        body["metadata"] = metadata

    try:
        client = _get_client()
        auth_headers = await _get_auth_headers()
        resp = await client.post(
            "/v1/memory",
            json=body,
            headers=auth_headers,
        )
        resp.raise_for_status()
        logger.info("cq_capture_ok", extra={"type": interaction_type})
    except Exception as e:
        logger.warning("cq_capture_error", extra={"error": str(e)})


async def notify_tier_change(
    user_id: str,
    old_tier: str,
    new_tier: str,
    event_type: str,
    occurred_at: str | None = None,
):
    """Notify Context Quilt of a subscription tier transition.

    Fire-and-forget. CQ uses these events to drive retention/soft-delete
    policy without GP having to encode the policy on its side.

    event_type values: "upgrade", "downgrade", "cancellation", "refund",
    "expire", "trial_start", "trial_to_paid". Idempotent on
    (user_id, occurred_at) on the CQ side.
    """
    settings = get_settings()
    if not settings.cq_base_url:
        return

    from datetime import datetime, timezone
    body = {
        "old_tier": old_tier,
        "new_tier": new_tier,
        "event_type": event_type,
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
    }

    try:
        client = _get_client()
        auth_headers = await _get_auth_headers()
        resp = await client.post(
            f"/v1/users/{user_id}/tier-change",
            json=body,
            headers=auth_headers,
        )
        resp.raise_for_status()
        logger.info(
            "cq_tier_change_ok",
            extra={"user_id": user_id, "old": old_tier, "new": new_tier, "event": event_type},
        )
    except Exception as e:
        logger.warning(
            "cq_tier_change_error",
            extra={"user_id": user_id, "event": event_type, "error": str(e)},
        )
