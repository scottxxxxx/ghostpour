"""Meeting report generation and retrieval.

POST /v1/meetings/{meeting_id}/report — generate a new report (LLM call, charges allocation)
GET  /v1/meetings/{meeting_id}/report — retrieve cached report (no LLM, free)

Reports are cached for 30 days for recovery (e.g., timeout during generation).
SS should persist the report locally once received — GP is not long-term storage.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest
from app.models.user import UserRecord
from app.services.ai_tier import tier_to_ai_tier
from app.services.meeting_report import (
    build_report_prompt,
    format_duration,
    gather_meeting_data,
    render_report_html,
)

logger = logging.getLogger("ghostpour.reports")

router = APIRouter()


class ReportRequest(BaseModel):
    duration_seconds: int
    project: str | None = None
    attendees: list[str] | None = None
    tag_taxonomy: list[str] | None = None  # Custom tags; defaults to built-in 8
    meeting_start_iso: str | None = None  # ISO 8601 with timezone, e.g. "2026-04-14T13:01:00-05:00"
    timezone_abbr: str | None = None  # e.g. "CST", "EST", "IST" — from device locale
    quality: str | None = None  # "fast" = lighter model, "best" = premium model (default)


@router.post("/meetings/{meeting_id}/report")
async def generate_report(
    meeting_id: str,
    body: ReportRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Generate a shareable HTML meeting report.

    Gathers all data GP has for this meeting (transcript, queries, summaries,
    analysis), runs one LLM call to produce structured JSON, and renders it
    into HTML.
    """
    provider_router = request.app.state.provider_router
    pricing = request.app.state.pricing
    usage_tracker = request.app.state.usage_tracker
    rate_limiter = request.app.state.rate_limiter
    tier_config = request.app.state.tier_config

    tier = tier_config.tiers.get(user.effective_tier)
    if not tier:
        raise HTTPException(status_code=500, detail="Unknown tier")

    # Enforce rate limit and monthly allocation BEFORE expensive work.
    # Without these, an exhausted user could keep generating reports and
    # GP would eat the cost (cost is recorded post-call).
    allowed, retry_after = rate_limiter.check(user.id, tier.requests_per_minute)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                "details": {"retry_after": retry_after},
            },
        )
    await usage_tracker.check_quota(db, user, tier)

    # 1. Gather meeting data
    meeting_data = await gather_meeting_data(db, user.id, meeting_id)

    if not meeting_data["transcript"] and not meeting_data["summary"]:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "no_meeting_data",
                "message": f"No transcript or summary found for meeting {meeting_id}. "
                           "Ensure capture-transcript was called with this meeting_id.",
            },
        )

    # 2. Build LLM prompt
    system_prompt, user_message = build_report_prompt(
        meeting_data,
        attendees=body.attendees,
        tag_taxonomy=body.tag_taxonomy,
    )

    # 3. Select model — respect explicit quality from client, else tier-based
    if body.quality == "fast":
        report_model = "claude-haiku-4-5-20251001"
    elif body.quality == "best":
        report_model = "claude-sonnet-4-6"
    else:
        # No explicit quality: free tier gets lighter model, paid tiers get premium
        report_model = "claude-haiku-4-5-20251001" if user.effective_tier == "free" else "claude-sonnet-4-6"
    report_provider = "anthropic"

    chat_request = ChatRequest(
        provider=report_provider,
        model=report_model,
        system_prompt=system_prompt,
        user_content=user_message,
        max_tokens=4096,
        call_type="report",
        prompt_mode="MeetingReport",
        meeting_id=meeting_id,
    )

    import time
    start = time.monotonic()
    try:
        response = await provider_router.route(chat_request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Report LLM call failed for meeting %s: %s", meeting_id, e)
        raise HTTPException(status_code=502, detail={
            "code": "provider_error",
            "message": f"Report generation failed: {e}",
        })

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # 4. Calculate cost and log usage
    request_cost = 0.0
    if pricing.is_loaded:
        cost = pricing.calculate_cost(
            provider=report_provider,
            model=report_model,
            usage=response.usage,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        response.cost = cost
        request_cost = cost.get("total_cost", 0.0)

    await usage_tracker.record_cost(db, user.id, request_cost, tier, user=user)
    await usage_tracker.log_usage(db, user.id, chat_request, response, elapsed_ms)

    # 5. Parse the LLM response as JSON and render
    # Wrapped in try/except to log the actual error — unhandled exceptions here
    # result in bare "Internal Server Error" with no request_id.
    try:
        return await _build_report_response(
            response, body, db, user, report_model, request_cost, elapsed_ms, meeting_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Report post-processing failed for meeting %s: %s", meeting_id, e, exc_info=True)
        raise HTTPException(status_code=502, detail={
            "code": "report_render_error",
            "message": f"Report generated but post-processing failed: {e}",
        })


async def _build_report_response(response, body, db, user, report_model, request_cost, elapsed_ms, meeting_id):
    report_text = response.text.strip()
    # Strip markdown fencing if the model added it despite instructions
    if report_text.startswith("```"):
        report_text = report_text.split("\n", 1)[1] if "\n" in report_text else report_text[3:]
        if report_text.endswith("```"):
            report_text = report_text[:-3]
        report_text = report_text.strip()

    try:
        report_json = json.loads(report_text)
    except json.JSONDecodeError as e:
        logger.error("Report JSON parse failed for meeting %s: %s\nRaw: %s", meeting_id, e, report_text[:500])
        raise HTTPException(status_code=502, detail={
            "code": "report_parse_error",
            "message": "The LLM returned invalid JSON for the report. Please try again.",
        })

    # 6. Render HTML — use meeting start time if provided, else fall back to now
    if body.meeting_start_iso:
        try:
            meeting_dt = datetime.fromisoformat(body.meeting_start_iso)
        except (ValueError, TypeError):
            meeting_dt = datetime.now(timezone.utc)
    else:
        meeting_dt = datetime.now(timezone.utc)

    # Format timezone — prefer explicit abbreviation from client (CST, EST, IST),
    # fall back to UTC offset if not provided
    tz_label = ""
    if body.timezone_abbr:
        tz_label = f" {body.timezone_abbr}"
    elif meeting_dt.tzinfo and meeting_dt.utcoffset() is not None:
        offset = meeting_dt.utcoffset()
        total_seconds = int(offset.total_seconds())
        hours, remainder = divmod(abs(total_seconds), 3600)
        sign = "+" if total_seconds >= 0 else "-"
        tz_label = f" UTC{sign}{hours}"
        if remainder:
            tz_label += f":{remainder // 60:02d}"

    metadata = {
        "meeting_date": meeting_dt.strftime("%B %-d, %Y"),
        "meeting_time": meeting_dt.strftime("%-I:%M %p") + tz_label,
        "meeting_duration": format_duration(body.duration_seconds),
        "project_name": body.project or "",
    }

    report_html = render_report_html(report_json, metadata)

    # 7. Cache the report for recovery (30-day retention, purged on startup)
    report_json_str = json.dumps(report_json, ensure_ascii=False)
    ai_tier = tier_to_ai_tier(user.effective_tier)
    await db.execute(
        """INSERT OR REPLACE INTO meeting_reports
           (id, user_id, meeting_id, report_json, report_html,
            model, ai_tier, input_tokens, output_tokens, cost_usd,
            generation_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user.id,
            meeting_id,
            report_json_str,
            report_html,
            report_model,
            ai_tier,
            response.input_tokens,
            response.output_tokens,
            request_cost,
            elapsed_ms,
            meeting_dt.isoformat(),
        ),
    )
    await db.commit()

    return {
        "report_html": report_html,
        "report_json": report_json,
        "meeting_id": meeting_id,
        "ai_tier": ai_tier,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": request_cost,
        "generation_ms": elapsed_ms,
    }


@router.get("/meetings/{meeting_id}/report")
async def get_cached_report(
    meeting_id: str,
    user: UserRecord = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Retrieve a previously generated report. No LLM call, no charge.

    Returns the cached report if it exists (retained for 30 days).
    SS should use this for recovery after timeouts — the report was
    generated, paid for, and cached even if the HTTP response didn't
    make it back to the client.
    """
    cursor = await db.execute(
        "SELECT * FROM meeting_reports WHERE meeting_id = ? AND user_id = ?",
        (meeting_id, user.id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "report_not_found",
            "message": f"No cached report for meeting {meeting_id}. Generate one with POST first.",
        })

    # ai_tier was added in a later schema rev. Old rows return None;
    # iOS falls back to whatever attribution it had (or skips).
    try:
        cached_ai_tier = row["ai_tier"]
    except (IndexError, KeyError):
        cached_ai_tier = None

    return {
        "report_html": row["report_html"],
        "report_json": json.loads(row["report_json"]),
        "meeting_id": meeting_id,
        "ai_tier": cached_ai_tier,
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "cost_usd": row["cost_usd"],
        "generation_ms": row["generation_ms"],
        "cached": True,
        "generated_at": row["created_at"],
    }


class RenderRequest(BaseModel):
    report_json: dict
    duration_seconds: int


@router.post("/reports/render")
async def render_report(
    body: RenderRequest,
    user: UserRecord = Depends(get_current_user),
):
    """Re-render a report from edited JSON. No LLM call, no allocation charge.

    Used for live preview after the user edits report sections in the review screen.
    """
    now = datetime.now(timezone.utc)
    metadata = {
        "meeting_date": now.strftime("%B %-d, %Y"),
        "meeting_time": now.strftime("%-I:%M %p"),
        "meeting_duration": format_duration(body.duration_seconds),
    }

    report_html = render_report_html(body.report_json, metadata)
    return {"report_html": report_html}
