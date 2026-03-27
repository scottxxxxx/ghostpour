from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import httpx
import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.database import get_db

router = APIRouter()


def _verify_admin(request: Request, x_admin_key: str) -> None:
    settings = request.app.state.settings
    if not settings.admin_key or x_admin_key != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


class SetTierRequest(BaseModel):
    user_id: str
    tier: str


class SimulateTierRequest(BaseModel):
    user_id: str
    tier: str | None = None  # null to clear simulation
    exhausted: bool = True


class AdminCaptureTranscriptRequest(BaseModel):
    user_id: str
    transcript: str
    meeting_id: str | None = None
    project: str | None = None
    project_id: str | None = None


class UpdateFeatureStateRequest(BaseModel):
    tier: str
    feature: str
    state: str  # "enabled", "teaser", "disabled"


@router.post("/admin/set-tier")
async def set_tier(
    body: SetTierRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Set a user's subscription tier with dollar-value carryover on upgrade.

    On upgrade: unused allocation from the old tier is converted to dollar
    value and added to the new tier's allocation. monthly_used_usd resets to 0.

    On downgrade: allocation resets to the new tier's limit. No carryover
    (downgrades take effect at period end in production via StoreKit).
    """
    _verify_admin(request, x_admin_key)

    tier_config = request.app.state.tier_config
    if body.tier not in tier_config.tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {body.tier}. Available: {list(tier_config.tiers.keys())}",
        )

    new_tier = tier_config.tiers[body.tier]

    # Read current user state
    cursor = await db.execute(
        "SELECT tier, monthly_used_usd, monthly_cost_limit_usd, overage_balance_usd FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    old_tier_name = row["tier"]

    # Apply tier change — reset allocation to the new tier's limit, no carryover
    now = datetime.now(timezone.utc)
    new_limit = new_tier.monthly_cost_limit_usd
    resets_at = (now + timedelta(days=30)).isoformat()

    await db.execute(
        """UPDATE users SET
            tier = ?,
            monthly_cost_limit_usd = ?,
            monthly_used_usd = 0,
            overage_balance_usd = 0,
            allocation_resets_at = ?,
            updated_at = ?
           WHERE id = ?""",
        (body.tier, new_limit, resets_at, now.isoformat(), body.user_id),
    )
    await db.commit()

    return {
        "status": "ok",
        "user_id": body.user_id,
        "old_tier": old_tier_name,
        "new_tier": body.tier,
        "monthly_limit_usd": new_limit,
        "allocation_resets_at": resets_at,
    }


@router.post("/admin/simulate-tier")
async def simulate_tier(
    body: SimulateTierRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Toggle tier simulation for testing upgrade flows.

    Sets a temporary tier override on a user without changing their real tier.
    When active, the user sees the simulated tier's constraints, and if
    exhausted=true, all chat requests return 429 allocation_exhausted.

    Send tier=null to clear the simulation and restore the real tier.
    """
    _verify_admin(request, x_admin_key)

    tier_config = request.app.state.tier_config

    # Validate tier if setting simulation
    if body.tier is not None and body.tier not in tier_config.tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {body.tier}. Available: {list(tier_config.tiers.keys())}",
        )

    # Verify user exists
    cursor = await db.execute(
        "SELECT id, tier, simulated_tier FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    real_tier = row["tier"]

    if body.tier is None:
        # Clear simulation
        await db.execute(
            "UPDATE users SET simulated_tier = NULL, simulated_exhausted = 0 WHERE id = ?",
            (body.user_id,),
        )
        await db.commit()
        return {
            "status": "ok",
            "simulation": "cleared",
            "user_id": body.user_id,
            "real_tier": real_tier,
        }

    # Activate simulation
    await db.execute(
        "UPDATE users SET simulated_tier = ?, simulated_exhausted = ? WHERE id = ?",
        (body.tier, 1 if body.exhausted else 0, body.user_id),
    )
    await db.commit()

    return {
        "status": "ok",
        "simulation": "active",
        "user_id": body.user_id,
        "real_tier": real_tier,
        "simulated_tier": body.tier,
        "exhausted": body.exhausted,
    }


@router.post("/admin/update-feature-state")
async def update_feature_state(
    body: UpdateFeatureStateRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Toggle a feature's state for a specific tier. Writes to tiers.yml and reloads."""
    _verify_admin(request, x_admin_key)

    if body.state not in ("enabled", "teaser", "disabled"):
        raise HTTPException(status_code=400, detail=f"Invalid state: {body.state}. Must be enabled, teaser, or disabled")

    tier_config = request.app.state.tier_config
    if body.tier not in tier_config.tiers:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {body.tier}")

    # Load current YAML
    tiers_path = Path(__file__).parent.parent.parent / "config" / "tiers.yml"

    with open(tiers_path) as f:
        raw = yaml.safe_load(f)

    # Update the feature state
    tier_data = raw["tiers"].get(body.tier)
    if not tier_data:
        raise HTTPException(status_code=400, detail=f"Tier {body.tier} not found in tiers.yml")

    if "features" not in tier_data:
        tier_data["features"] = {}

    old_state = tier_data["features"].get(body.feature, "disabled")
    tier_data["features"][body.feature] = body.state

    # Write back
    with open(tiers_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Reload tier config in app state
    from app.models.tier import load_tier_config
    request.app.state.tier_config = load_tier_config(str(tiers_path))

    return {
        "status": "ok",
        "tier": body.tier,
        "feature": body.feature,
        "old_state": old_state,
        "new_state": body.state,
    }


# --- Admin Transcript Capture ---


@router.post("/admin/capture-transcript")
async def admin_capture_transcript(
    body: AdminCaptureTranscriptRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """Send a transcript to Context Quilt on behalf of a user. Admin-only."""
    _verify_admin(request, x_admin_key)

    import asyncio
    from app.services import context_quilt as cq

    # Look up user for display_name and email
    cursor = await db.execute(
        "SELECT id, email, display_name FROM users WHERE id = ?",
        (body.user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    asyncio.create_task(cq.capture(
        user_id=row["id"],
        interaction_type="meeting_transcript",
        content=body.transcript,
        meeting_id=body.meeting_id,
        project=body.project,
        project_id=body.project_id,
        display_name=row["display_name"],
        email=row["email"],
    ))

    return {
        "status": "queued",
        "user_id": body.user_id,
        "project": body.project,
        "transcript_length": len(body.transcript),
    }


# --- Provider Status & Key Management ---

# Providers we can check balance/status for
_PROVIDER_CHECKS = {
    "anthropic": {
        "display_name": "Anthropic",
        "env_key": "anthropic_api_key",
        "check_url": "https://api.anthropic.com/v1/messages",
        "has_balance_api": False,
        "console_url": "https://console.anthropic.com/settings/billing",
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "env_key": "openrouter_api_key",
        "check_url": "https://openrouter.ai/api/v1/auth/key",
        "has_balance_api": True,
        "console_url": "https://openrouter.ai/credits",
    },
    "openai": {
        "display_name": "OpenAI",
        "env_key": "openai_api_key",
        "check_url": None,
        "has_balance_api": False,
        "console_url": "https://platform.openai.com/settings/organization/billing/overview",
    },
}


@router.get("/admin/provider-status")
async def provider_status(
    request: Request,
    x_admin_key: str = Header(...),
):
    """Check API key status and balance for configured providers."""
    _verify_admin(request, x_admin_key)
    settings = request.app.state.settings

    results = {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, info in _PROVIDER_CHECKS.items():
            key = getattr(settings, info["env_key"], "")
            masked = f"...{key[-4:]}" if key and len(key) > 4 else "(not set)"

            entry = {
                "display_name": info["display_name"],
                "key_set": bool(key),
                "key_masked": masked,
                "console_url": info["console_url"],
                "status": "unknown",
            }

            if not key:
                entry["status"] = "no_key"
                results[name] = entry
                continue

            try:
                if name == "openrouter":
                    # OpenRouter has a balance API
                    resp = await client.get(
                        "https://openrouter.ai/api/v1/auth/key",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        entry["status"] = "ok"
                        entry["balance"] = {
                            "label": data.get("label", ""),
                            "usage_usd": data.get("usage", 0),
                            "limit_usd": data.get("limit", None),
                            "remaining_usd": (
                                round(data["limit"] - data["usage"], 4)
                                if data.get("limit") is not None
                                else None
                            ),
                            "is_free_tier": data.get("is_free_tier", False),
                        }
                    else:
                        entry["status"] = "invalid_key"

                elif name == "anthropic":
                    # Anthropic has no balance API — verify key with a minimal call
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": "claude-haiku-4-5-20251001",
                            "max_tokens": 1,
                            "messages": [{"role": "user", "content": "hi"}],
                        },
                    )
                    if resp.status_code == 200:
                        entry["status"] = "ok"
                    elif resp.status_code == 401:
                        entry["status"] = "invalid_key"
                    elif resp.status_code == 429:
                        entry["status"] = "rate_limited"
                    else:
                        entry["status"] = "ok"  # 400 etc still means key works

                else:
                    # Generic: just mark as configured
                    entry["status"] = "configured"

            except httpx.TimeoutException:
                entry["status"] = "timeout"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)

            results[name] = entry

    return {"providers": results}


class UpdateKeyRequest(BaseModel):
    provider: str   # e.g., "anthropic", "openrouter"
    api_key: str    # new key value


@router.post("/admin/update-key")
async def update_key(
    body: UpdateKeyRequest,
    request: Request,
    x_admin_key: str = Header(...),
):
    """Update a provider API key — takes effect immediately and persists to .env file."""
    _verify_admin(request, x_admin_key)
    settings = request.app.state.settings

    if body.provider not in _PROVIDER_CHECKS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {body.provider}. Available: {list(_PROVIDER_CHECKS.keys())}",
        )

    env_key = _PROVIDER_CHECKS[body.provider]["env_key"]

    if not hasattr(settings, env_key):
        raise HTTPException(status_code=400, detail=f"No setting for {env_key}")

    # Update in-memory (pydantic-settings model is frozen, so use object.__setattr__)
    object.__setattr__(settings, env_key, body.api_key)

    # Persist to .env file so it survives container restarts
    env_var_name = f"CZ_{env_key.upper()}"
    persisted = _persist_env_var(env_var_name, body.api_key)

    masked = f"...{body.api_key[-4:]}" if len(body.api_key) > 4 else "***"
    return {
        "status": "ok",
        "provider": body.provider,
        "key_masked": masked,
        "persisted": persisted,
    }


def _persist_env_var(name: str, value: str) -> bool:
    """Update or add an env var in the .env file. Returns True if successful."""
    import os

    # Try common .env file locations
    env_paths = [".env.prod", ".env", "/app/.env.prod", "/app/.env"]
    env_path = None
    for p in env_paths:
        if os.path.exists(p):
            env_path = p
            break

    if not env_path:
        return False

    try:
        # Read existing file
        with open(env_path) as f:
            lines = f.readlines()

        # Replace existing key or append
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f"{name}="):
                new_lines.append(f"{name}={value}\n")
                found = True
            else:
                new_lines.append(line)

        if not found:
            new_lines.append(f"{name}={value}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)

        return True
    except Exception:
        return False


@router.get("/admin/dashboard")
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
):
    """Admin dashboard: users, usage, costs, latency. Protected by admin key."""
    _verify_admin(request, x_admin_key)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Users ---
    cursor = await db.execute("SELECT COUNT(*) FROM users")
    total_users = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
    active_users = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT tier, COUNT(*) FROM users WHERE is_active = 1 GROUP BY tier"
    )
    tier_breakdown = {row[0]: row[1] for row in await cursor.fetchall()}

    # --- Usage (last N days) ---
    since = f"{days}d"
    cursor = await db.execute(
        """SELECT
            COUNT(*) as total_requests,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
            SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END) as rate_limited,
            COALESCE(SUM(input_tokens), 0) as total_input_tokens,
            COALESCE(SUM(output_tokens), 0) as total_output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as total_cost_usd,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms,
            MAX(response_time_ms) as max_latency_ms,
            MIN(response_time_ms) as min_latency_ms
           FROM usage_log
           WHERE request_timestamp >= date('now', ?)""",
        (f"-{days} days",),
    )
    row = await cursor.fetchone()
    usage_summary = {
        "period_days": days,
        "total_requests": row[0],
        "successful": row[1],
        "errors": row[2],
        "rate_limited": row[3],
        "total_input_tokens": row[4],
        "total_output_tokens": row[5],
        "total_tokens": row[4] + row[5],
        "total_cost_usd": round(row[6], 4),
        "avg_latency_ms": int(row[7]) if row[7] else 0,
        "max_latency_ms": row[8],
        "min_latency_ms": row[9],
    }

    # --- Usage by provider ---
    cursor = await db.execute(
        """SELECT provider, model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'
           GROUP BY provider, model
           ORDER BY requests DESC""",
        (f"-{days} days",),
    )
    by_model = [
        {
            "provider": r[0],
            "model": r[1],
            "requests": r[2],
            "input_tokens": r[3],
            "output_tokens": r[4],
            "cost_usd": round(r[5], 4),
            "avg_latency_ms": int(r[6]) if r[6] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # --- Usage by user (top 10) ---
    cursor = await db.execute(
        """SELECT u.id, u.email, u.tier,
            COUNT(*) as requests,
            COALESCE(SUM(l.input_tokens), 0) + COALESCE(SUM(l.output_tokens), 0) as total_tokens,
            COALESCE(SUM(l.estimated_cost_usd), 0) as cost_usd,
            MAX(l.request_timestamp) as last_request
           FROM usage_log l
           JOIN users u ON l.user_id = u.id
           WHERE l.request_timestamp >= date('now', ?) AND l.status = 'success'
           GROUP BY u.id
           ORDER BY total_tokens DESC
           LIMIT 10""",
        (f"-{days} days",),
    )
    top_users = [
        {
            "user_id": r[0],
            "email": r[1],
            "tier": r[2],
            "requests": r[3],
            "total_tokens": r[4],
            "cost_usd": round(r[5], 4),
            "last_request": r[6],
        }
        for r in await cursor.fetchall()
    ]

    # --- Today's usage ---
    cursor = await db.execute(
        """SELECT
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost_usd
           FROM usage_log
           WHERE request_timestamp >= ? AND status = 'success'""",
        (today,),
    )
    today_row = await cursor.fetchone()
    today_usage = {
        "requests": today_row[0],
        "tokens": today_row[1],
        "cost_usd": round(today_row[2], 4),
    }

    # --- Latency percentiles (last N days) ---
    cursor = await db.execute(
        """SELECT response_time_ms FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'
           ORDER BY response_time_ms""",
        (f"-{days} days",),
    )
    latencies = [r[0] for r in await cursor.fetchall() if r[0] is not None]
    percentiles = {}
    if latencies:
        for p in [50, 75, 90, 95, 99]:
            idx = int(len(latencies) * p / 100)
            percentiles[f"p{p}"] = latencies[min(idx, len(latencies) - 1)]

    # Allocation alerts: users above 80%
    cursor = await db.execute(
        """SELECT u.id, u.email, u.tier, u.monthly_used_usd, u.monthly_cost_limit_usd
           FROM users u
           WHERE u.is_active = 1
             AND u.monthly_cost_limit_usd > 0
             AND u.monthly_used_usd >= u.monthly_cost_limit_usd * 0.8
           ORDER BY (u.monthly_used_usd / u.monthly_cost_limit_usd) DESC"""
    )
    allocation_alerts = [
        {
            "user_id": r["id"],
            "email": r["email"],
            "tier": r["tier"],
            "monthly_used_usd": round(float(r["monthly_used_usd"] or 0), 4),
            "monthly_limit_usd": round(float(r["monthly_cost_limit_usd"] or 0), 4),
            "percent_used": round(float(r["monthly_used_usd"] or 0) / float(r["monthly_cost_limit_usd"]) * 100, 1) if r["monthly_cost_limit_usd"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Trial stats
    cursor = await db.execute(
        "SELECT COUNT(*) FROM users WHERE is_trial = 1"
    )
    active_trials = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM users WHERE is_trial = 0 AND tier != 'free' AND tier != 'admin'"
    )
    converted = (await cursor.fetchone())[0]

    cursor = await db.execute(
        """SELECT id, email, tier, trial_end FROM users
           WHERE is_trial = 1
           ORDER BY trial_end ASC"""
    )
    trial_users = [
        {"user_id": r["id"], "email": r["email"], "tier": r["tier"], "trial_end": r["trial_end"]}
        for r in await cursor.fetchall()
    ]

    # Cached token savings
    cursor = await db.execute(
        """SELECT
            COALESCE(SUM(cached_tokens), 0) as total_cached,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output
           FROM usage_log
           WHERE request_timestamp >= date('now', ?) AND status = 'success'""",
        (f"-{days} days",),
    )
    cache_row = await cursor.fetchone()
    total_cached = cache_row["total_cached"]
    # Estimate savings: cached tokens would have been billed as input tokens
    # Use approximate Haiku input rate ($0.80/1M) as baseline
    estimated_savings = total_cached * 0.80 / 1_000_000

    # Daily usage trend
    cursor = await db.execute(
        """SELECT
            date(request_timestamp) as day,
            COUNT(*) as requests,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
           FROM usage_log
           WHERE request_timestamp >= date('now', ?)
           GROUP BY date(request_timestamp)
           ORDER BY day""",
        (f"-{days} days",),
    )
    daily_usage = [
        {"day": r["day"], "requests": r["requests"], "cost": round(r["cost"], 4), "errors": r["errors"]}
        for r in await cursor.fetchall()
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users": {
            "total": total_users,
            "active": active_users,
            "by_tier": tier_breakdown,
        },
        "today": today_usage,
        "usage": usage_summary,
        "by_model": by_model,
        "top_users": top_users,
        "latency_percentiles": percentiles,
        "allocation_alerts": allocation_alerts,
        "trials": {
            "active_trials": active_trials,
            "converted_subscribers": converted,
            "trial_users": trial_users,
        },
        "cache_savings": {
            "cached_tokens": total_cached,
            "estimated_savings_usd": round(estimated_savings, 4),
        },
        "daily_usage": daily_usage,
    }


@router.get("/admin/errors")
async def error_log(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Recent failed requests for debugging."""
    _verify_admin(request, x_admin_key)

    cursor = await db.execute(
        """SELECT l.id, l.user_id, u.email, l.provider, l.model,
            l.status, l.error_message, l.response_time_ms,
            l.request_timestamp, l.call_type, l.prompt_mode
           FROM usage_log l
           LEFT JOIN users u ON l.user_id = u.id
           WHERE l.status != 'success'
             AND l.request_timestamp >= date('now', ?)
           ORDER BY l.request_timestamp DESC
           LIMIT ?""",
        (f"-{days} days", limit),
    )
    errors = [
        {
            "id": r["id"],
            "user_email": r["email"],
            "provider": r["provider"],
            "model": r["model"],
            "status": r["status"],
            "error_message": r["error_message"],
            "response_time_ms": r["response_time_ms"],
            "timestamp": r["request_timestamp"],
            "call_type": r["call_type"],
            "prompt_mode": r["prompt_mode"],
        }
        for r in await cursor.fetchall()
    ]

    # Error summary by type
    cursor = await db.execute(
        """SELECT status, COUNT(*) as count
           FROM usage_log
           WHERE status != 'success'
             AND request_timestamp >= date('now', ?)
           GROUP BY status
           ORDER BY count DESC""",
        (f"-{days} days",),
    )
    by_status = {r["status"]: r["count"] for r in await cursor.fetchall()}

    # Error summary by provider
    cursor = await db.execute(
        """SELECT provider, COUNT(*) as count
           FROM usage_log
           WHERE status != 'success'
             AND request_timestamp >= date('now', ?)
           GROUP BY provider
           ORDER BY count DESC""",
        (f"-{days} days",),
    )
    by_provider = {r["provider"]: r["count"] for r in await cursor.fetchall()}

    return {
        "errors": errors,
        "total": len(errors),
        "by_status": by_status,
        "by_provider": by_provider,
    }


@router.get("/admin/tiers")
async def get_tiers(
    request: Request,
    x_admin_key: str = Header(...),
):
    """View all tier configurations with their model/provider access rules."""
    _verify_admin(request, x_admin_key)
    tier_config = request.app.state.tier_config

    tiers = {}
    for name, tier in tier_config.tiers.items():
        tiers[name] = {
            "display_name": tier.display_name,
            "default_model": tier.default_model,
            "monthly_cost_limit_usd": tier.monthly_cost_limit_usd,
            "requests_per_minute": tier.requests_per_minute,
            "summary_mode": tier.summary_mode,
            "summary_interval_minutes": tier.summary_interval_minutes,
            "allowed_providers": tier.allowed_providers,
            "allowed_models": tier.allowed_models,
            "max_images_per_request": tier.max_images_per_request,
            "storekit_product_id": tier.storekit_product_id,
            "features": tier.features,
        }

    return {"tiers": tiers}


@router.get("/admin/user/{user_id}")
async def user_detail(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(default=30, ge=1, le=90),
):
    """Detailed user view with budget, usage breakdown by call type, and query history."""
    _verify_admin(request, x_admin_key)
    tier_config = request.app.state.tier_config

    # User info
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user_tier = row["tier"]
    tier = tier_config.tiers.get(user_tier)

    # Monthly budget
    cursor = await db.execute(
        """SELECT
            COALESCE(SUM(COALESCE(input_tokens, 0)), 0) as input_tokens,
            COALESCE(SUM(COALESCE(output_tokens, 0)), 0) as output_tokens,
            COALESCE(SUM(COALESCE(cached_tokens, 0)), 0) as cached_tokens,
            COALESCE(SUM(COALESCE(estimated_cost_usd, 0)), 0) as total_cost,
            COUNT(*) as total_requests
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', 'start of month')
             AND status = 'success'""",
        (user_id,),
    )
    month_row = await cursor.fetchone()

    monthly_limit = tier.daily_cost_limit_usd * 30 if tier and tier.daily_cost_limit_usd != -1 else -1
    monthly_used = month_row["total_cost"]

    # Usage by call type
    cursor = await db.execute(
        """SELECT
            call_type,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(cached_tokens), 0) as cached_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms,
            COALESCE(SUM(image_count), 0) as total_images
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY call_type
           ORDER BY requests DESC""",
        (user_id, f"-{days} days"),
    )
    by_call_type = [
        {
            "call_type": r["call_type"] or "unknown",
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cached_tokens": r["cached_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
            "total_images": r["total_images"],
        }
        for r in await cursor.fetchall()
    ]

    # Usage by prompt mode
    cursor = await db.execute(
        """SELECT
            prompt_mode,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as total_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY prompt_mode
           ORDER BY requests DESC""",
        (user_id, f"-{days} days"),
    )
    by_prompt_mode = [
        {
            "prompt_mode": r["prompt_mode"] or "unknown",
            "requests": r["requests"],
            "total_tokens": r["total_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Usage by model
    cursor = await db.execute(
        """SELECT
            provider, model,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost,
            ROUND(AVG(response_time_ms), 0) as avg_latency_ms
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY provider, model
           ORDER BY requests DESC""",
        (user_id, f"-{days} days"),
    )
    by_model = [
        {
            "provider": r["provider"],
            "model": r["model"],
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost": round(r["cost"], 4),
            "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] else 0,
        }
        for r in await cursor.fetchall()
    ]

    # Daily usage trend (last N days)
    cursor = await db.execute(
        """SELECT
            date(request_timestamp) as day,
            COUNT(*) as requests,
            COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) as tokens,
            COALESCE(SUM(estimated_cost_usd), 0) as cost
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= date('now', ?)
             AND status = 'success'
           GROUP BY date(request_timestamp)
           ORDER BY day""",
        (user_id, f"-{days} days"),
    )
    daily_trend = [
        {"day": r["day"], "requests": r["requests"], "tokens": r["tokens"], "cost": round(r["cost"], 4)}
        for r in await cursor.fetchall()
    ]

    return {
        "user": {
            "id": row["id"],
            "email": row["email"],
            "tier": user_tier,
            "created_at": row["created_at"],
            "is_active": bool(row["is_active"]),
        },
        "budget": {
            "tier": user_tier,
            "monthly_limit_usd": round(monthly_limit, 2) if monthly_limit != -1 else -1,
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_remaining_usd": round(monthly_limit - monthly_used, 4) if monthly_limit != -1 else -1,
            "percent_used": round(monthly_used / monthly_limit * 100, 1) if monthly_limit > 0 else 0,
            "this_month": {
                "requests": month_row["total_requests"],
                "input_tokens": month_row["input_tokens"],
                "output_tokens": month_row["output_tokens"],
                "cached_tokens": month_row["cached_tokens"],
            },
        },
        "by_call_type": by_call_type,
        "by_prompt_mode": by_prompt_mode,
        "by_model": by_model,
        "daily_trend": daily_trend,
    }


@router.get("/admin/user/{user_id}/queries")
async def user_queries(
    user_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List individual queries for a user with raw request/response JSON."""
    _verify_admin(request, x_admin_key)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Get total count for pagination
    count_row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM usage_log WHERE user_id = ? AND request_timestamp >= ?",
        (user_id, cutoff),
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    cursor = await db.execute(
        """SELECT id, provider, model, input_tokens, output_tokens, cached_tokens,
                  estimated_cost_usd, response_time_ms, status, error_message,
                  call_type, prompt_mode, image_count, request_timestamp, metadata
           FROM usage_log
           WHERE user_id = ? AND request_timestamp >= ?
           ORDER BY request_timestamp DESC
           LIMIT ? OFFSET ?""",
        (user_id, cutoff, limit, offset),
    )
    rows = await cursor.fetchall()

    queries = []
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        queries.append({
            "id": row["id"],
            "provider": row["provider"],
            "model": row["model"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cached_tokens": row["cached_tokens"],
            "cost": row["estimated_cost_usd"],
            "latency_ms": row["response_time_ms"],
            "status": row["status"],
            "error": row["error_message"],
            "call_type": row["call_type"],
            "prompt_mode": row["prompt_mode"],
            "image_count": row["image_count"],
            "timestamp": row["request_timestamp"],
            "raw_request": meta.get("raw_request"),
            "raw_response": meta.get("raw_response"),
        })

    return {"queries": queries, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/users")
async def list_users(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    x_admin_key: str = Header(...),
):
    """List all users with their usage stats."""
    _verify_admin(request, x_admin_key)

    tier_config = request.app.state.tier_config

    cursor = await db.execute(
        """SELECT u.id, u.apple_sub, u.email, u.tier, u.created_at, u.is_active,
            u.simulated_tier, u.simulated_exhausted,
            u.monthly_used_usd, u.monthly_cost_limit_usd, u.allocation_resets_at,
            u.is_trial, u.trial_end,
            (SELECT COUNT(*) FROM usage_log l WHERE l.user_id = u.id AND l.status = 'success') as total_requests,
            (SELECT COALESCE(SUM(COALESCE(l2.input_tokens,0)), 0)
             FROM usage_log l2 WHERE l2.user_id = u.id AND l2.status = 'success') as total_input_tokens,
            (SELECT COALESCE(SUM(COALESCE(l2.output_tokens,0)), 0)
             FROM usage_log l2 WHERE l2.user_id = u.id AND l2.status = 'success') as total_output_tokens,
            (SELECT COALESCE(SUM(l3.estimated_cost_usd), 0)
             FROM usage_log l3 WHERE l3.user_id = u.id AND l3.status = 'success') as total_cost_usd,
            (SELECT MAX(l4.request_timestamp) FROM usage_log l4 WHERE l4.user_id = u.id) as last_request
           FROM users u
           ORDER BY u.created_at DESC"""
    )
    users = []
    for r in await cursor.fetchall():
        monthly_used = float(r["monthly_used_usd"] or 0)
        monthly_limit = float(r["monthly_cost_limit_usd"] or 0)
        tier_name = r["tier"]
        tier_def = tier_config.tiers.get(tier_name)

        # Convert cost to hours for display
        model_cost_per_hour = 0.19 if tier_def and "sonnet" in (tier_def.default_model or "") else 0.05
        hours_used = monthly_used / model_cost_per_hour if model_cost_per_hour > 0 else 0
        hours_limit = monthly_limit / model_cost_per_hour if monthly_limit > 0 else -1
        percent_used = round(monthly_used / monthly_limit * 100, 1) if monthly_limit > 0 else 0

        users.append({
            "id": r["id"],
            "apple_sub": r["apple_sub"][:8] + "..." if r["apple_sub"] else None,
            "email": r["email"],
            "tier": tier_name,
            "tier_display_name": tier_def.display_name if tier_def else tier_name,
            "created_at": r["created_at"],
            "is_active": bool(r["is_active"]),
            "simulated_tier": r["simulated_tier"],
            "simulated_exhausted": bool(r["simulated_exhausted"]),
            "is_trial": bool(r["is_trial"]),
            "trial_end": r["trial_end"],
            # Current month allocation
            "monthly_used_usd": round(monthly_used, 4),
            "monthly_limit_usd": round(monthly_limit, 4),
            "percent_used": percent_used,
            "hours_used": round(hours_used, 1),
            "hours_limit": round(hours_limit, 1) if hours_limit != -1 else -1,
            "allocation_resets_at": r["allocation_resets_at"],
            # Lifetime totals
            "total_requests": r["total_requests"],
            "total_input_tokens": r["total_input_tokens"],
            "total_output_tokens": r["total_output_tokens"],
            "total_tokens": r["total_input_tokens"] + r["total_output_tokens"],
            "total_cost_usd": round(r["total_cost_usd"], 4) if r["total_cost_usd"] else 0,
            "last_request": r["last_request"],
        })

    return {"users": users, "count": len(users)}
