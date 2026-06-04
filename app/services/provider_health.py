"""Periodic provider key health daemon.

Pings each managed provider on a fixed cadence so we get an alert the
moment a key revokes or a budget runs out, rather than finding out
hours later when iOS starts seeing failed chats.

Probe strategy per provider:
- **Anthropic** — POST /v1/messages/count_tokens with a 1-character
  message. Validates the API key without billing tokens. Detects
  revoked keys (401) and account suspension (403). Does NOT validate
  remaining quota because count_tokens isn't billable; a separate
  reactive alert fires on 402/quota when a real chat call returns one.
- **OpenRouter** — GET /v1/auth/key. Returns usage + limit. Detects
  revoked keys (401), and proactively fires `provider_budget_exhausted`
  when `remaining_usd` drops below `openrouter_low_balance_threshold_usd`.
- **OpenAI** — only probed when a key is configured. Tiny
  /v1/chat/completions call (`max_completion_tokens=1`). Costs
  fractions of a cent per probe. Detects revoked keys (401) and
  budget exhaustion (402, 429 with insufficient_quota).

Failure handling:
- 401/403 → fire `provider_auth_failed` incident (one per provider).
- 402, or `code:insufficient_quota`, or low OpenRouter balance →
  `provider_budget_exhausted` incident.
- 429 (transient rate limit) → log warning, no alert.
- 5xx, timeouts, network errors → log warning, no alert. Provider
  outages are not our incidents to escalate.
- Any other 4xx → log warning + record on the per-provider status,
  but no alert (operator investigates via status endpoint).

The alerting infrastructure already dedupes by `(category, subject)`
within a 30 minute window, so a sustained failure fires one email
per 30 min, not one every 15 min.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import httpx

from app.config import Settings

logger = logging.getLogger("ghostpour.provider_health")

# Probe timeouts. Keep short — a 15 minute cadence means a stuck probe
# doesn't matter much, but we'd rather move on than hang the daemon.
_PROBE_TIMEOUT_SECS = 10.0


@dataclass
class ProbeResult:
    """One probe attempt's outcome. Cached in module state for the
    status endpoint and used by the daemon to decide whether to alert."""
    provider: str
    checked_at: datetime
    healthy: bool
    status_code: int | None
    detail: str
    # Provider-specific extras (e.g. OpenRouter remaining_usd) so the
    # dashboard can render the same numbers operators see today.
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checked_at"] = self.checked_at.isoformat().replace("+00:00", "Z")
        return d


# Module-level last-check cache keyed by provider name. Only the daemon
# writes; the status endpoint reads. No lock needed (GIL atomic dict ops).
_last_check: dict[str, ProbeResult] = {}


def get_last_check(provider: str | None = None) -> ProbeResult | dict[str, ProbeResult] | None:
    if provider is None:
        return dict(_last_check)
    return _last_check.get(provider)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Per-provider probes ---------------------------------------------------


async def check_anthropic(api_key: str, client: httpx.AsyncClient) -> ProbeResult:
    """POST /v1/messages/count_tokens. Free, validates auth."""
    if not api_key:
        return ProbeResult(
            provider="anthropic", checked_at=_now(),
            healthy=False, status_code=None,
            detail="no API key configured",
        )
    try:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages/count_tokens",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "messages": [{"role": "user", "content": "x"}],
            },
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        return ProbeResult(
            provider="anthropic", checked_at=_now(),
            healthy=False, status_code=None,
            detail=f"network/timeout: {e}",
        )
    return _classify("anthropic", resp)


async def check_openrouter(
    api_key: str,
    low_balance_threshold_usd: float,
    client: httpx.AsyncClient,
) -> ProbeResult:
    """GET /v1/auth/key. Returns usage + limit; we trip the budget
    alert proactively when remaining drops below the threshold."""
    if not api_key:
        return ProbeResult(
            provider="openrouter", checked_at=_now(),
            healthy=False, status_code=None,
            detail="no API key configured",
        )
    try:
        resp = await client.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        return ProbeResult(
            provider="openrouter", checked_at=_now(),
            healthy=False, status_code=None,
            detail=f"network/timeout: {e}",
        )

    if resp.status_code != 200:
        return _classify("openrouter", resp)

    try:
        data = resp.json().get("data", {})
        usage = float(data.get("usage", 0.0))
        limit = data.get("limit")  # null = unlimited
        remaining = (float(limit) - usage) if limit is not None else None
    except (ValueError, KeyError, TypeError) as e:
        return ProbeResult(
            provider="openrouter", checked_at=_now(),
            healthy=False, status_code=200,
            detail=f"unparseable response: {e}",
        )

    extras = {"usage_usd": usage, "limit_usd": limit, "remaining_usd": remaining}

    if remaining is not None and remaining < low_balance_threshold_usd:
        return ProbeResult(
            provider="openrouter", checked_at=_now(),
            healthy=False, status_code=200,
            detail=(
                f"remaining=${remaining:.2f} below threshold "
                f"${low_balance_threshold_usd:.2f} (limit ${limit}, used ${usage:.2f})"
            ),
            extras=extras,
        )

    return ProbeResult(
        provider="openrouter", checked_at=_now(),
        healthy=True, status_code=200,
        detail=(
            f"remaining=${remaining:.2f}" if remaining is not None
            else f"unlimited (used ${usage:.2f})"
        ),
        extras=extras,
    )


async def check_openai(api_key: str, client: httpx.AsyncClient) -> ProbeResult:
    """Tiny /v1/chat/completions call. Only runs when a key is configured."""
    if not api_key:
        return ProbeResult(
            provider="openai", checked_at=_now(),
            healthy=True, status_code=None,
            detail="no API key configured; skipping probe",
        )
    try:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "x"}],
                "max_completion_tokens": 1,
            },
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        return ProbeResult(
            provider="openai", checked_at=_now(),
            healthy=False, status_code=None,
            detail=f"network/timeout: {e}",
        )
    return _classify("openai", resp)


def _classify(provider: str, resp: httpx.Response) -> ProbeResult:
    """Map an HTTP response into a ProbeResult. Shared between Anthropic
    and OpenAI; OpenRouter has its own balance-aware path above."""
    code = resp.status_code
    if 200 <= code < 300:
        return ProbeResult(
            provider=provider, checked_at=_now(),
            healthy=True, status_code=code, detail="ok",
        )

    body = ""
    try:
        body = (resp.text or "")[:500]
    except Exception:
        body = "(no body)"

    return ProbeResult(
        provider=provider, checked_at=_now(),
        healthy=False, status_code=code,
        detail=f"HTTP {code}: {body}",
    )


# --- Tick + alerting -------------------------------------------------------


async def _alert(
    db: aiosqlite.Connection,
    settings: Settings,
    *,
    category: str,
    subject: str,
    details: dict[str, Any],
) -> None:
    """Wrap report_incident so a missing alerting config never kills
    the daemon loop. Fire-and-forget."""
    try:
        from app.services.alerting import report_incident
        await report_incident(
            db,
            category=category,
            subject=subject,
            details=details,
            from_addr=settings.alert_email_from,
        )
    except Exception as e:
        logger.warning("provider_health: alert dispatch failed: %s", e)


def _alert_decision(result: ProbeResult) -> tuple[str, str] | None:
    """Decide whether a ProbeResult should fire an incident, and which
    category. Returns (category, subject) or None.

    The subject is stable per failure mode so the alerting infrastructure
    can dedupe repeats within its 30 minute suppression window."""
    if result.healthy:
        return None

    code = result.status_code

    if code in (401, 403):
        return ("provider_auth_failed", f"{result.provider}_auth_{code}")

    if code == 402:
        return ("provider_budget_exhausted", f"{result.provider}_budget_402")

    # OpenRouter low-balance path: status 200 but unhealthy because
    # remaining dropped below threshold.
    if result.provider == "openrouter" and code == 200:
        return ("provider_budget_exhausted", "openrouter_low_balance")

    # Transient: 429, 5xx, network/timeout. Log but no alert.
    if code is None or code == 429 or (code >= 500):
        return None

    # Other 4xx — log but don't alert. Operator can pull from the status
    # endpoint if something is genuinely wrong.
    return None


async def tick(
    db: aiosqlite.Connection,
    settings: Settings,
) -> dict[str, ProbeResult]:
    """One round of probes across all providers. Updates module state
    and fires alerts for any newly unhealthy provider."""
    global _last_check
    results: dict[str, ProbeResult] = {}

    async with httpx.AsyncClient() as client:
        tasks = {
            "anthropic": check_anthropic(settings.anthropic_api_key, client),
            "openrouter": check_openrouter(
                settings.openrouter_api_key,
                settings.openrouter_low_balance_threshold_usd,
                client,
            ),
            "openai": check_openai(settings.openai_api_key, client),
        }
        outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for (name, task), outcome in zip(tasks.items(), outcomes):
            if isinstance(outcome, BaseException):
                outcome = ProbeResult(
                    provider=name, checked_at=_now(),
                    healthy=False, status_code=None,
                    detail=f"probe raised: {outcome}",
                )
            results[name] = outcome
            _last_check[name] = outcome

    for result in results.values():
        decision = _alert_decision(result)
        if decision is None:
            continue
        category, subject = decision
        await _alert(
            db, settings,
            category=category, subject=subject,
            details={
                "provider": result.provider,
                "status_code": result.status_code,
                "detail": result.detail,
                "extras": result.extras,
                "checked_at": result.checked_at.isoformat(),
            },
        )

    return results


async def run_daemon(app) -> None:
    """Lifespan-spawned loop. First tick after a brief delay so startup
    logs aren't tangled with the probe output, then every
    `provider_health_check_interval_seconds`. Fail-soft: an exception in
    any tick must not kill the loop."""
    await asyncio.sleep(10.0)
    while True:
        try:
            settings = app.state.settings
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
            async with aiosqlite.connect(db_path) as db:
                results = await tick(db, settings)
            unhealthy = [name for name, r in results.items() if not r.healthy]
            if unhealthy:
                logger.warning(
                    "provider_health tick unhealthy=%s details=%s",
                    unhealthy,
                    {n: results[n].detail for n in unhealthy},
                )
            else:
                logger.info("provider_health tick all healthy: %s", list(results.keys()))
        except Exception as e:
            logger.warning("provider_health tick failed: %s", e)

        try:
            await asyncio.sleep(app.state.settings.provider_health_check_interval_seconds)
        except asyncio.CancelledError:
            return
