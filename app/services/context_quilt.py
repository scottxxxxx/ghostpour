"""
Context Quilt integration for GhostPour.

Handles two flows:
  1. Recall: Before sending a query to the LLM, fetch relevant context from CQ
  2. Capture: After the LLM responds, send query+response to CQ for learning

Both flows are controlled by the `context_quilt: true` flag in ChatRequest.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Shared HTTP client (created on first use)
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.cq_base_url,
            timeout=httpx.Timeout(5.0),  # General timeout; recall uses its own
        )
    return _client


async def recall(user_id: str, text: str, metadata: dict | None = None) -> dict:
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
        if metadata:
            body["metadata"] = metadata

        resp = await client.post(
            "/v1/recall",
            json=body,
            headers={"X-App-ID": settings.cq_app_id},
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
    meeting_id: str | None = None,
    project: str | None = None,
    call_type: str | None = None,
    prompt_mode: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
):
    """
    Send query+response to Context Quilt for learning. Fire-and-forget (async).

    This runs in the background after the LLM response is returned to the user.
    Never blocks the response.
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

    # Build metadata from available fields
    metadata: dict[str, Any] = {}
    if meeting_id:
        metadata["meeting_id"] = meeting_id
    if project:
        metadata["project"] = project
    if call_type:
        metadata["call_type"] = call_type
    if prompt_mode:
        metadata["prompt_mode"] = prompt_mode
    if display_name:
        metadata["display_name"] = display_name
    if email:
        metadata["email"] = email
    if metadata:
        body["metadata"] = metadata

    try:
        client = _get_client()
        resp = await client.post(
            "/v1/memory",
            json=body,
            headers={"X-App-ID": settings.cq_app_id},
        )
        resp.raise_for_status()
        logger.info("cq_capture_ok", extra={"type": interaction_type})
    except Exception as e:
        logger.warning("cq_capture_error", extra={"error": str(e)})
