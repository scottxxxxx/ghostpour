"""Meeting report generation endpoint.

POST /v1/meetings/{meeting_id}/report generates a structured HTML meeting
report from data already stored in GP (transcript, queries, summaries,
analysis) — all indexed by meeting_id.
"""

import json
import logging
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatRequest
from app.models.user import UserRecord
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
    tier_config = request.app.state.tier_config

    tier = tier_config.tiers.get(user.effective_tier)
    if not tier:
        raise HTTPException(status_code=500, detail="Unknown tier")

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

    # 3. Call LLM (always Sonnet for report quality, charged to user's allocation)
    report_model = "claude-sonnet-4-6"
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

    # 5. Parse the LLM response as JSON
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

    # 6. Render HTML
    now = datetime.now(timezone.utc)
    metadata = {
        "meeting_date": now.strftime("%B %-d, %Y"),
        "meeting_time": now.strftime("%-I:%M %p"),
        "meeting_duration": format_duration(body.duration_seconds),
    }

    report_html = render_report_html(report_json, metadata)

    return {
        "report_html": report_html,
        "report_json": report_json,
        "meeting_id": meeting_id,
        "model": report_model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": request_cost,
        "generation_ms": elapsed_ms,
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
