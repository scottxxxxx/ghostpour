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


# Server-controlled per-tier report model. Clients do not pick.
# Pro gets Advanced AI (Sonnet) per the tier promise; everyone else
# gets Standard AI (Haiku) which matches the "Standard AI" feature
# bullet for Plus and the breakeven margin math in tiers.yml.
_REPORT_MODEL_BY_TIER = {
    "free": "claude-haiku-4-5-20251001",
    "plus": "claude-haiku-4-5-20251001",
    "pro": "claude-sonnet-4-6",
    "admin": "claude-sonnet-4-6",
}


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

    # 2. Build LLM prompt — localize narrative content from Accept-Language.
    # Wire enums (stoplight color, emoji_label, severity, etc.) stay English
    # so iOS keying continues to work; only display text gets translated.
    from app.routers.config import _parse_accept_language
    locale = _parse_accept_language(request.headers.get("Accept-Language"))
    system_prompt, user_message = build_report_prompt(
        meeting_data,
        attendees=body.attendees,
        tag_taxonomy=body.tag_taxonomy,
        locale=locale,
    )

    # 3. Select model — server-controlled per tier. Clients do not pick.
    report_model = _REPORT_MODEL_BY_TIER.get(
        user.effective_tier, "claude-haiku-4-5-20251001"
    )
    report_provider = "anthropic"

    # 3.5. Pre-call budget gate. If running this report would push the
    # user past their effective_limit + overage tolerance, return the
    # canned/sample report verbatim and persist it with
    # report_status='placeholder_budget_blocked'. No LLM call.
    effective_limit = tier.monthly_cost_limit_usd
    if user.is_trial and tier.trial_cost_limit_usd is not None:
        effective_limit = tier.trial_cost_limit_usd

    if effective_limit != -1 and pricing.is_loaded:
        from app.services.budget_gate import (
            dollars_to_credits,
            estimate_call_cost_usd,
            estimate_input_tokens,
            would_exceed_budget,
        )
        prompt_tokens = estimate_input_tokens(system_prompt + user_message)
        estimated_cost = estimate_call_cost_usd(
            pricing,
            provider=report_provider,
            model=report_model,
            input_tokens=prompt_tokens,
            max_output_tokens=4096,
        )
        cursor = await db.execute(
            "SELECT monthly_used_usd FROM users WHERE id = ?",
            (user.id,),
        )
        row = await cursor.fetchone()
        monthly_used = float(row["monthly_used_usd"] or 0) if row else 0.0
        if estimated_cost is not None and would_exceed_budget(
            monthly_used_usd=monthly_used,
            estimated_cost_usd=estimated_cost,
            effective_limit_usd=effective_limit,
        ):
            return await _build_canned_report_response(
                request, db, user, meeting_id, body,
                effective_limit_usd=effective_limit,
                monthly_used_usd=monthly_used,
            )

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
    # Real (LLM-generated) report — report_status NULL signals "not a placeholder",
    # is_editable=1 lets iOS open the editor. Canned/budget-blocked reports take
    # a different persistence path (see budget-gate handler) and set both flags.
    await db.execute(
        """INSERT OR REPLACE INTO meeting_reports
           (id, user_id, meeting_id, report_json, report_html,
            model, ai_tier, input_tokens, output_tokens, cost_usd,
            generation_ms, created_at, report_status, is_editable)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            None,  # report_status: real reports have no status marker
            1,     # is_editable: real reports are editable
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
        "report_status": None,
        "is_editable": True,
    }


async def _build_canned_report_response(
    request,
    db,
    user,
    meeting_id,
    body,
    *,
    effective_limit_usd: float,
    monthly_used_usd: float,
):
    """Return + persist the canned/sample meeting report when a Free user
    is over budget. Pulls the HTML template + CTA strings from the
    canned-report remote config, substitutes meeting metadata + CTA copy,
    and stamps the row with report_status='placeholder_budget_blocked'
    so iOS can disable the editor and surface the 'Hide samples' filter.
    """
    from app.services.budget_gate import dollars_to_credits

    configs = request.app.state.remote_configs
    canned = configs.get("canned-report", {})
    template = canned.get("report_html_template", "")
    cta = canned.get("cta", {})

    # Substitute meeting metadata + CTA copy. Server-rendered: iOS just
    # displays whatever HTML we send.
    meeting_dt = datetime.now(timezone.utc)
    if body.meeting_start_iso:
        try:
            meeting_dt = datetime.fromisoformat(body.meeting_start_iso)
        except (ValueError, TypeError):
            pass

    rendered_html = template
    for key, value in [
        ("{{cta_eyebrow}}", cta.get("eyebrow", "")),
        ("{{cta_headline}}", cta.get("headline", "")),
        ("{{cta_body}}", cta.get("body", "")),
        ("{{cta_button_text}}", cta.get("button_text", "Upgrade")),
    ]:
        rendered_html = rendered_html.replace(key, value)

    # Persist with placeholder flags so subsequent GETs return the same
    # canned content + iOS can route around it correctly.
    await db.execute(
        """INSERT OR REPLACE INTO meeting_reports
           (id, user_id, meeting_id, report_json, report_html,
            model, ai_tier, input_tokens, output_tokens, cost_usd,
            generation_ms, created_at, report_status, is_editable)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user.id,
            meeting_id,
            json.dumps({"placeholder": True}),
            rendered_html,
            None, None, 0, 0, 0.0, 0,
            meeting_dt.isoformat(),
            "placeholder_budget_blocked",
            0,  # is_editable=false
        ),
    )
    await db.commit()

    credits_total = dollars_to_credits(effective_limit_usd)
    credits_used = dollars_to_credits(monthly_used_usd)
    credits_remaining = max(0, credits_total - credits_used)

    return {
        "report_html": rendered_html,
        "report_json": None,
        "meeting_id": meeting_id,
        "ai_tier": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "generation_ms": 0,
        "report_status": "placeholder_budget_blocked",
        "is_editable": False,
        "feature_state": {
            "feature": "meeting_report",
            "credits_remaining": credits_remaining,
            "credits_total": credits_total,
            "credits_resets_at": user.allocation_resets_at,
            "cta": {
                "kind": "report_blocked_budget_exhausted",
                "text": cta.get("pill_text", "You've used your free AI for this month. Upgrade to Plus to keep going."),
                "action": cta.get("action", "open_paywall"),
            },
        },
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
    # Same pattern for report_status / is_editable (added v14).
    def _safe(col, default=None):
        try:
            return row[col]
        except (IndexError, KeyError):
            return default

    cached_ai_tier = _safe("ai_tier")
    cached_status = _safe("report_status")
    cached_editable_int = _safe("is_editable")
    # Legacy rows (NULL is_editable) are treated as editable=true; only
    # explicitly-stamped 0 disables the editor.
    cached_editable = True if cached_editable_int is None else bool(cached_editable_int)

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
        "report_status": cached_status,
        "is_editable": cached_editable,
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
