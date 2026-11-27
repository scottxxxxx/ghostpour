"""POST /v1/help/ask: an answer to a question typed into Help, written from
the Help articles the app sends with it (Shoulder Surf, 2026-11-26).

Wire shape (docs/wire-contracts/help-ask.md is the contract):

  POST /v1/help/ask                 no auth; X-App-ID: shouldersurf
  {
    "question": str,                 1 to 500 characters
    "articles": [                    1 to 3, in the client's rank order
      {"id": slug, "title": str (max 200), "body": str (max 8000)}
    ],
    "app_locale": "pt-BR",
    "device_id": UUID,
    "app_version": str|null, "app_build": str|null,
    "support_email": str             optional
    "ui_terms": {"Settings": "Ajustes"}  optional, the app's own names
  }
  -> 200 {"answer": str, "covered": bool, "used_article_ids": [ids],
          "source": "articles", "output_language": null}
  -> 400 invalid_request      a shape failure (never FastAPI's 422 list)
  -> 413 body_too_large       over 32 KB
  -> 429 rate_limited         details.retry_after, in seconds
  -> 503 help_ask_unavailable switched off, the day's spend cap is reached,
                              or the model call failed
  Every error is {"detail": {"code", "message", "details"}}.

It has to work for anyone who can open Help, signed out included, so there
is no auth dependency here and the Authorization header is never read: an
expired token cannot fail the call, and a valid one cannot attach the call
to an account. Nothing is written to usage_log and no allowance moves. The
feature pays for itself out of one daily budget (app/services/help_ask.py).

Order of the checks, cheapest first and the model last:
size, shape, the on/off dial, the three rate limits, the spend cap (which
is also the reservation), then the one model call.

Neither the question nor the answer is logged by anything in this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.database import get_db
from app.routers.telemetry import _SLUG_PATTERN, _UUID_RE, _client_ip, _ip_hash
from app.services import help_ask

logger = logging.getLogger("ghostpour.help_ask")

router = APIRouter()

MAX_BODY_BYTES = 32 * 1024
_HOUR, _DAY = 3600.0, 86400.0

_UNKNOWN_KEYS_SEEN: set[tuple] = set()


class HelpArticle(BaseModel):
    """One Help article the client ranked for this question. Help text is
    public; the id is the same slug the Help telemetry uses."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., pattern=_SLUG_PATTERN)
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=8000)


class HelpAskBody(BaseModel):
    # Unknown keys are accepted and reported once, never stored (the ping's
    # allow-then-report rule): an old server must not fail a newer client.
    model_config = ConfigDict(extra="allow")

    question: str = Field(..., min_length=1, max_length=500)
    articles: list[HelpArticle] = Field(..., min_length=1, max_length=3)
    app_locale: str = Field(..., min_length=1, max_length=35)
    device_id: str = Field(..., min_length=1, max_length=64)
    # Optional: the client sends null when the bundle has no value. A
    # missing version must not cost the person their answer.
    app_version: str | None = Field(None, min_length=1, max_length=32)
    app_build: str | None = Field(None, min_length=1, max_length=16)
    # Any string is accepted. One that is not an address is simply not
    # used (help_ask.valid_support_email).
    support_email: str | None = None
    # Any JSON value is accepted here. One that is not a usable glossary is
    # left out with a log line (help_ask.valid_ui_terms), never a 400.
    ui_terms: object | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v

    @field_validator("device_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        if not _UUID_RE.match(v):
            raise ValueError("device_id must be a UUID")
        return v

    @model_validator(mode="after")
    def _distinct_ids(self):
        ids = [a.id for a in self.articles]
        if len(set(ids)) != len(ids):
            raise ValueError("article ids must be distinct")
        return self


def _error(status: int, code: str, message: str, details: dict | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail={
        "code": code, "message": message, "details": details or {}})


def _unavailable(reason: str) -> HTTPException:
    return _error(503, "help_ask_unavailable",
                  "Help answers are not available right now.", {"reason": reason})


def _invalid(exc: ValidationError) -> HTTPException:
    """The 400 for a shape failure. It names the field and the rule and
    never echoes what was sent: the value may be the question itself."""
    first = exc.errors(include_url=False, include_input=False, include_context=False)[0]
    loc = ".".join(str(p) for p in first.get("loc") or ()) or "body"
    return _error(400, "invalid_request", f"Invalid request: {loc}",
                  {"field": loc, "reason": str(first.get("msg") or first.get("type") or "invalid")})


async def _read_capped_body(request: Request) -> bytes:
    """The raw body, refused past the cap before anything is parsed. The
    declared length is checked first because it is free; the bytes that
    actually arrive are the real bound."""
    def _too_large(check: str, size: int) -> HTTPException:
        return _error(413, "body_too_large",
                      f"Request body is over {MAX_BODY_BYTES} bytes",
                      {"check": check, "limit_bytes": MAX_BODY_BYTES, "bytes": size})

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large("content_length", int(declared))
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise _too_large("streamed_bytes", len(buf))
    return bytes(buf)


def _check_rate_limits(limiter, cfg: help_ask.HelpAskConfig, device_id: str,
                       ip_hash: str) -> None:
    """Per device 10 an hour and 30 a day, per IP 30 an hour (the served
    values). All three are looked at before a slot is taken in any, so a
    refused request is not counted anywhere. In memory, like every other
    limiter here: a restart forgets the windows, and the spend cap, which
    lives in the database, is what holds across one."""
    limits = [(f"helpask:dev:h:{device_id.lower()}", cfg.per_device_per_hour, _HOUR, "device_hour"),
              (f"helpask:dev:d:{device_id.lower()}", cfg.per_device_per_day, _DAY, "device_day")]
    if ip_hash:
        limits.append((f"helpask:ip:h:{ip_hash}", cfg.per_ip_per_hour, _HOUR, "ip_hour"))
    refused = [(retry, name) for key, limit, window, name in limits
               for ok, retry in [limiter.peek(key, limit, window)] if not ok]
    if refused:
        # The longest wait is the honest one: retrying sooner hits the
        # other limit.
        retry_after, name = max(refused)
        raise _error(429, "rate_limited",
                     f"Too many Help questions; retry in {retry_after}s",
                     {"retry_after": retry_after, "limit": name})
    for key, limit, window, _ in limits:
        limiter.check(key, limit, window)


@router.post("/help/ask")
async def help_ask_route(request: Request,
                         db: aiosqlite.Connection = Depends(get_db)) -> dict:
    raw = await _read_capped_body(request)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _error(400, "invalid_request", "Body is not valid JSON",
                     {"field": "body", "reason": "not valid JSON"})
    if not isinstance(parsed, dict):
        raise _error(400, "invalid_request", "Body must be a JSON object",
                     {"field": "body", "reason": "not an object"})
    try:
        body = HelpAskBody.model_validate(parsed)
    except ValidationError as exc:
        raise _invalid(exc)

    app_state = request.app.state
    app_id = getattr(request.state, "app_id", "unknown")
    if body.model_extra:
        key = (app_id, tuple(sorted(body.model_extra)))
        if key not in _UNKNOWN_KEYS_SEEN:
            _UNKNOWN_KEYS_SEEN.add(key)
            logger.warning("help_ask_unknown_fields app=%s keys=%s (accepted, NOT used)",
                           key[0], ",".join(key[1]))

    remote_configs = getattr(app_state, "remote_configs", None)
    cfg = help_ask.load_config(remote_configs)
    if not cfg.usable:
        raise _unavailable("disabled")

    _check_rate_limits(app_state.rate_limiter, cfg, body.device_id,
                       _ip_hash(_client_ip(request)))

    pricing = getattr(app_state, "pricing", None)
    prices = help_ask.token_prices(pricing, remote_configs, cfg.provider, cfg.model)
    if prices is None:
        # No price, no cap. Refuse instead of spending blind.
        logger.error("help_ask_no_price provider=%s model=%s: no price known, feature refused",
                     cfg.provider, cfg.model)
        raise _unavailable("no_price")

    articles = [a.model_dump() for a in body.articles]
    support_email = help_ask.valid_support_email(body.support_email)
    model_request = help_ask.build_request(
        cfg, question=body.question, articles=articles,
        app_locale=body.app_locale, support_email=support_email,
        ui_terms=help_ask.valid_ui_terms(body.ui_terms))

    now = datetime.now(timezone.utc)
    row_id = str(uuid.uuid4())
    reserved = await help_ask.reserve(
        db, row_id=row_id, received_at=now.isoformat(), day=now.date().isoformat(),
        app_id=app_id, device_id=body.device_id.lower(), app_locale=body.app_locale,
        app_version=body.app_version, app_build=body.app_build,
        question=help_ask.stored_question(body.question),
        offered_ids=[a["id"] for a in articles], model=cfg.model,
        reserved_usd=help_ask.worst_case_cost(model_request, prices),
        cap_usd=cfg.daily_spend_cap_usd)
    if not reserved:
        logger.warning("help_ask_refused reason=daily_cap cap_usd=%s", cfg.daily_spend_cap_usd)
        raise _unavailable("daily_cap")

    from app.services.anthropic_or_fallback import route_with_fallback
    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            route_with_fallback(app_state.provider_router, model_request, db),
            timeout=help_ask.MODEL_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        # A provider that ANSWERED with an error did not bill. A timeout or
        # a dropped connection may have, and nothing says how much, so the
        # reservation stands for those.
        answered_with_error = isinstance(exc, HTTPException)
        await help_ask.settle(
            db, row_id, outcome="model_error", covered=None, used_ids=None,
            input_tokens=None, output_tokens=None,
            cost_usd=0.0 if answered_with_error else None, latency_ms=elapsed)
        logger.warning("help_ask_model_failed type=%s ms=%d", type(exc).__name__, elapsed)
        raise _unavailable("model_error")
    elapsed = int((time.monotonic() - start) * 1000)

    result = help_ask.finalize(help_ask.parse_reply(response), articles=articles,
                               app_locale=body.app_locale, support_email=support_email)
    cost = help_ask.actual_cost(pricing, prices, model_request, response)
    await help_ask.settle(
        db, row_id, outcome=result["outcome"], covered=result["covered"],
        used_ids=result["used_article_ids"], input_tokens=response.input_tokens,
        output_tokens=response.output_tokens, cost_usd=cost, latency_ms=elapsed)
    # `stop` says why the model stopped. max_tokens next to outcome=fallback
    # means the reply was cut off at the cap, not that the model had no answer.
    logger.info("help_ask outcome=%s stop=%s in=%s out=%s cost_usd=%s ms=%d", result["outcome"],
                (response.usage or {}).get("finish_reason"),
                response.input_tokens, response.output_tokens, cost, elapsed)
    if not result["serve"]:
        # Paid for and recorded above, but there is no answer to show.
        raise _unavailable("model_error")
    return {
        "answer": result["answer"],
        "covered": result["covered"],
        "used_article_ids": result["used_article_ids"],
        "source": "articles",
        # The language is decided by the question the person typed, not by
        # a locale GP directed, so there is nothing to echo (the rule
        # queries got in #1240).
        "output_language": None,
    }
