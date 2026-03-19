# CLAUDE.md — CloudZap

> **Last updated:** March 19, 2026

## Project Overview

**CloudZap** is an open-source LLM API gateway built with FastAPI. It sits between client apps and LLM providers, handling auth, routing, rate limiting, and usage tracking. The first customer is Shoulder Surf (iOS meeting copilot).

**Live deployment:** `https://cz.shouldersurf.com`

## Tech Stack

- **FastAPI** (Python 3.12) — async web framework
- **SQLite** via aiosqlite — persistence (single writer, no ORM)
- **PyJWT** — HS256 JWT access/refresh tokens
- **httpx** — async HTTP client for provider calls
- **Docker** — deployment on GCP VM behind Nginx Proxy Manager

## Project Structure

```
app/
├── main.py              # FastAPI app factory, lifespan, middleware
├── config.py            # pydantic-settings with CZ_ env prefix
├── database.py          # aiosqlite init + schema (3 tables)
├── dependencies.py      # get_current_user (JWT verification)
├── models/              # Pydantic request/response models
├── routers/             # auth, chat, health, webhooks
├── services/
│   ├── apple_auth.py    # Apple JWKS token verification
│   ├── jwt_service.py   # JWT create/verify
│   ├── provider_router.py  # Dispatches to correct adapter
│   ├── providers/       # OpenAI-compat, Anthropic, Gemini adapters
│   ├── rate_limiter.py  # In-memory token bucket
│   └── usage_tracker.py # SQLite usage logging + quota check
└── middleware/           # Request logging
config/
├── tiers.yml            # Subscription tier definitions
└── providers.yml        # Provider registry (URLs, auth, models)
```

## Build & Run

```bash
# Local development
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your values
uvicorn app.main:app --reload

# Docker
docker compose up --build

# Tests
pytest tests/ -v
```

## Key Architecture Decisions

- **3 built-in adapters + 1 generic**: OpenAICompatAdapter (OpenAI/xAI/DeepSeek/Kimi/Qwen), AnthropicAdapter, GeminiAdapter, plus GenericAdapter for adding providers via YAML alone.
- **Pricing from LiteLLM**: Fetches model costs on startup from LiteLLM's JSON (configurable URL via `CZ_PRICING_SOURCE_URL`). Computes billable tokens (subtracts cached), cost breakdown per request, daily cost limit enforcement.
- **Full usage passthrough**: All provider metadata captured in flexible `usage` dict — cached tokens, reasoning tokens, finish reason, etc. No hardcoded fields.
- **SQLite + single uvicorn worker**: SQLite doesn't handle concurrent writes well. Single worker is sufficient for MVP load. Migration path: swap to asyncpg + Postgres, increase workers.
- **YAML config, not database config**: Tier definitions and provider catalogs change infrequently and should be version-controlled.
- **In-memory rate limiter**: Single worker means in-memory state is consistent. Resets on restart (acceptable — window is 60s).
- **HS256 JWT**: Symmetric signing is simpler for a single-service architecture. RS256 only matters when multiple services verify tokens.

## Environment Variables

All prefixed with `CZ_`. Secrets (API keys, JWT secret, admin key) are ONLY in env vars, never in code or config files. See `.env.example` for the full list.

## Deployment

- **GCP VM**: `35.239.227.192` (weirtech-shared-infra, e2-medium)
- **Container**: `cloudzap` on `proxy-tier` Docker network
- **Routing**: Nginx Proxy Manager routes `cz.shouldersurf.com` → `cloudzap:8000`
- **CI/CD**: Push to `main` → GitHub Actions builds image → pushes to GHCR → SSH deploys
- **Data**: SQLite DB persisted in `cloudzap-data` Docker volume at `/app/data/`
- **Server config**: `/opt/cloudzap/.env.prod` + `/opt/cloudzap/docker-compose.prod.yml`

## Database

3 tables, raw SQL (no ORM):
- **users**: `id`, `apple_sub`, `email`, `tier`, timestamps
- **refresh_tokens**: `id`, `user_id`, `token_hash`, `expires_at`, `revoked`
- **usage_log**: `id`, `user_id`, `provider`, `model`, token counts, `estimated_cost_usd`, latency, status, `metadata` (JSON blob with full usage + cost)

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Health check |
| POST | `/auth/apple` | None | Apple Sign In → JWT |
| POST | `/auth/refresh` | None | Refresh token rotation |
| POST | `/v1/chat` | Bearer JWT | Proxied LLM request |
| POST | `/webhooks/admin/set-tier` | X-Admin-Key | Manual tier control |
| GET | `/docs` | None | Swagger UI |

## Testing

```bash
pytest tests/ -v
```

41 tests covering: JWT creation/verification, tier enforcement (provider/model/image gating), provider request building, base64 redaction, rate limiting, generic adapter (dot-path extraction, usage flattening, URL templates), pricing (cost calculation, cached tokens, reasoning tokens, Anthropic/OpenAI/Gemini patterns).

## Related Projects

- **Shoulder Surf** (`/Users/scottguida/ShoulderSurf/`) — iOS meeting copilot, first CloudZap customer
- **GCP Proxy** (`/Users/scottguida/GCP Proxy for My sites/`) — Nginx Proxy Manager infrastructure docs
