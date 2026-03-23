# CLAUDE.md — CloudZap

> **Last updated:** March 22, 2026

## Project Overview

**CloudZap** is an open-source LLM API gateway built with FastAPI. It sits between client apps and LLM providers, handling auth, routing, rate limiting, usage tracking, and subscription-based access control. The first customer is Shoulder Surf (iOS meeting copilot).

**Live deployment:** `https://cz.shouldersurf.com`
**Admin dashboard:** `https://cz.shouldersurf.com/admin`
**GitHub:** `https://github.com/scottxxxxx/cloudzap`
**Subscription spec:** `/Users/scottguida/ShoulderSurf/Subscription_Tiers.md` — full tier details, pricing, allocation, carryover, StoreKit config
**Planning docs:** `shouldersurf-proxy-claude-code-plan.docx`, `Server side proxy-claude-code-plan.docx` (in repo root, gitignored)

## Tech Stack

- **FastAPI** (Python 3.12) — async web framework
- **SQLite** via aiosqlite — persistence (single writer, no ORM)
- **PyJWT** — HS256 JWT access/refresh tokens
- **httpx** — async HTTP client for provider calls
- **Docker** — deployment on GCP VM behind Nginx Proxy Manager
- **LiteLLM pricing JSON** — model cost data fetched on startup, refreshed daily

## Project Structure

```
app/
├── main.py              # FastAPI app factory, lifespan, middleware
├── config.py            # pydantic-settings with CZ_ env prefix
├── database.py          # aiosqlite init + schema + migrations
├── dependencies.py      # get_current_user (JWT verification)
├── models/              # Pydantic request/response models
├── routers/
│   ├── auth.py          # POST /auth/apple, POST /auth/refresh
│   ├── chat.py          # POST /v1/chat (with auto model routing)
│   ├── health.py        # GET /health, GET /admin, GET /v1/model-pricing
│   └── webhooks.py      # Admin endpoints (dashboard, users, tiers, set-tier)
├── services/
│   ├── apple_auth.py    # Apple JWKS token verification
│   ├── jwt_service.py   # JWT create/verify
│   ├── pricing.py       # LiteLLM pricing fetch, cost calculation, cached token handling
│   ├── provider_router.py  # Dispatches to correct adapter
│   ├── providers/       # OpenAI-compat, Anthropic, Gemini, Generic adapters
│   ├── rate_limiter.py  # In-memory token bucket
│   ├── usage_tracker.py # SQLite usage logging + quota check
│   └── context_quilt.py # CQ recall + capture integration
├── middleware/           # Request logging
└── static/admin.html    # Web-based admin dashboard
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

## Subscription Tiers

See **`/Users/scottguida/ShoulderSurf/Subscription_Tiers.md`** for the full tier specification.

5 tiers + admin, configured in `config/tiers.yml`. Model assignment is server-controlled — client sends `model: "auto"`, gateway substitutes the tier's `default_model`.

Summary: free ($0.05), standard ($2.99), pro ($4.99), ultra ($9.99), ultra_max ($19.99). Haiku for free/standard/pro, Sonnet for ultra/ultra_max. 2x markup on avg user cost. Monthly cost-based allocation with overage credits at same rate. Dollar-value carryover on upgrade.

### Allocation tracking (TODO — next implementation phase)

- `monthly_cost_limit_usd` per tier (derived from hours × model cost)
- `monthly_used_usd` tracked per user, resets on subscription renewal date
- `overage_balance_usd` per user (purchased credit packs)
- Usage priority: monthly allocation → overage balance → on-device fallback
- `X-Allocation-Percent` and `X-Allocation-Warning` headers on every chat response
- `GET /v1/usage/me` endpoint for authenticated users to check their allocation

### JWT design rule

**Never encode tier in JWT.** Always read tier from the database on every request. This ensures tier changes (upgrades, downgrades, admin overrides) take effect immediately without waiting for token expiry.

## Auto Model Routing

When the iOS app sends `provider: "auto", model: "auto"`, the chat endpoint:
1. Looks up the user's tier from the database
2. Reads `default_model` from the tier config (e.g., `"anthropic/claude-sonnet-4-6"`)
3. Splits into provider + model
4. Routes to the correct upstream provider

This means subscribers never choose a model — CloudZap picks the best one for their tier.

## iOS Settings Locks (for CloudZap users)

When the iOS app's provider is set to CloudZap, these settings are locked:

| Setting | BYOK (own key) | CloudZap managed |
|---------|---------------|-----------------|
| Auto-summary interval | User choice (2-15 min) | Locked: 10 min (Haiku tiers) / 15 min (Sonnet tiers) |
| Summary mode | User choice | Locked: Delta (Free/Standard/Pro), User choice (Ultra/Ultra Max) |
| Model selection | User choice | Locked: Auto |
| Max images per query | 5 | 3 (Haiku tiers) / 5 (Sonnet tiers) |

## Key Architecture Decisions

- **3 built-in adapters + 1 generic**: OpenAICompatAdapter (OpenAI/xAI/DeepSeek/Kimi/Qwen), AnthropicAdapter, GeminiAdapter, plus GenericAdapter for adding providers via YAML alone.
- **Pricing from LiteLLM**: Fetches model costs on startup (configurable URL via `CZ_PRICING_SOURCE_URL`). Computes billable tokens (subtracts cached), cost breakdown per request.
- **Full usage passthrough**: All provider metadata captured in flexible `usage` dict — cached tokens, reasoning tokens, finish reason, etc. No hardcoded fields.
- **SQLite + single uvicorn worker**: SQLite doesn't handle concurrent writes well. Single worker sufficient for MVP. Migration path: asyncpg + Postgres.
- **YAML config, not database config**: Tier definitions and provider catalogs are version-controlled.
- **In-memory rate limiter**: Single worker means in-memory state is consistent. Resets on restart.
- **Content never stored**: Prompts and responses are never persisted on the server — only token counts, costs, and metadata.
- **Anthropic-only at launch**: Subscription users get Anthropic models only. BYOK users retain full multi-provider access in the iOS app.

## Environment Variables

All prefixed with `CZ_`. Secrets are ONLY in env vars, never in code or config files. See `.env.example` for the full list.

Key variables:
- `CZ_JWT_SECRET` — JWT signing secret
- `CZ_APPLE_BUNDLE_ID` — iOS app bundle ID (`com.shouldersurf.ShoulderSurf`)
- `CZ_ANTHROPIC_API_KEY` — Anthropic API key (only provider configured currently)
- `CZ_ADMIN_KEY` — Admin dashboard/API key
- `CZ_PRICING_SOURCE_URL` — LiteLLM pricing JSON URL (default: GitHub raw)
- `CZ_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` — JWT lifetime (currently 1440 = 24h)

## Deployment

- **GCP VM**: `35.239.227.192` (weirtech-shared-infra, e2-medium, ~$25/mo)
- **Container**: `cloudzap` on `proxy-tier` Docker network
- **Routing**: Nginx Proxy Manager routes `cz.shouldersurf.com` → `cloudzap:8000`
- **CI/CD**: Push to `main` → GitHub Actions builds image → pushes to GHCR → SSH deploys
- **Data**: SQLite DB persisted in `cloudzap-data` Docker volume at `/app/data/`
- **Server config**: `/opt/cloudzap/.env.prod` + `/opt/cloudzap/docker-compose.prod.yml`
- **Manual deploy**: SSH in, `docker login ghcr.io`, `docker compose pull && up -d --force-recreate`

## Database

3 tables, raw SQL (no ORM), with migration support for schema changes:
- **users**: `id`, `apple_sub`, `email`, `tier`, timestamps
- **refresh_tokens**: `id`, `user_id`, `token_hash`, `expires_at`, `revoked`
- **usage_log**: `id`, `user_id`, `provider`, `model`, token counts, `estimated_cost_usd`, latency, status, `metadata` (JSON)

**Planned additions** (next implementation phase):
- `monthly_used_usd`, `overage_balance_usd`, `allocation_resets_at` on users table

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Health check + pricing status |
| GET | `/admin` | None (key in UI) | Admin dashboard web UI |
| POST | `/auth/apple` | None | Apple Sign In → JWT |
| POST | `/auth/refresh` | None | Refresh token rotation |
| POST | `/v1/chat` | Bearer JWT | Proxied LLM request (auto model routing) |
| GET | `/v1/model-pricing` | None | Cached LiteLLM pricing JSON |
| GET | `/webhooks/admin/dashboard` | X-Admin-Key | Usage stats, latency, top users |
| GET | `/webhooks/admin/users` | X-Admin-Key | User list with lifetime stats |
| GET | `/webhooks/admin/tiers` | X-Admin-Key | Tier config viewer |
| POST | `/webhooks/admin/set-tier` | X-Admin-Key | Manual tier assignment |
| GET | `/docs` | None | Swagger UI |

**Planned endpoints:**
- `GET /v1/usage/me` — Authenticated user's allocation, overage, usage stats
- `POST /v1/add-credits` — Add overage credits after StoreKit purchase verification

## Reserved Route Namespaces

- `/auth/*` — Sign in with Apple, JWT (shared with future Context Quilt)
- `/v1/*` — Chat, pricing, usage
- `/webhooks/*` — Admin, Apple webhooks
- `/memory/*` — Reserved for future Context Quilt
- `/quilt/*` — Reserved for future Context Quilt

## Testing

```bash
pytest tests/ -v
```

41 tests covering: JWT, tier enforcement, provider routing, base64 redaction, rate limiting, generic adapter, pricing/cost calculation.

## Admin Dashboard

Web UI at `/admin` with tabs:
- **Overview**: Today's stats, period summary, user counts by tier
- **Models**: Usage by provider/model (requests, tokens, cost, latency)
- **Users**: All users with tier badges, lifetime stats
- **Tiers**: Tier config cards with simulate button (switch your account to test any tier)
- **Latency**: Response time percentiles (p50/p75/p90/p95/p99)

Admin key: stored in `CZ_ADMIN_KEY` env var, persisted in browser localStorage.

## Context Quilt Integration

CloudZap integrates with Context Quilt when `context_quilt: true` is in the ChatRequest.

**Recall (pre-route, synchronous):**
- Calls `POST {CQ_BASE_URL}/v1/recall` with the user's query text
- 200ms timeout — skips gracefully on timeout or error
- Injects returned context into `system_prompt` (replaces `{{context_quilt}}` placeholder, or prepends)

**Capture (post-response, async):**
- Fires background `POST {CQ_BASE_URL}/v1/memory` with query, LLM response, and metadata
- Never blocks the response to the user
- Includes `meeting_id`, `project`, `call_type`, `prompt_mode` in metadata

**Response headers (for ShoulderSurf UI indicator):**
- `X-CQ-Matched`: number of entities matched (e.g., "3")
- `X-CQ-Entities`: comma-separated entity names (e.g., "Bob Martinez,Widget 2.0")

**ChatRequest fields:**
- `context_quilt: bool` — enable CQ for this request (default: false)
- `meeting_id: str | None` — meeting UUID for CQ queue grouping
- `project: str | None` — project name for CQ metadata

**Config:**
- `CZ_CQ_BASE_URL` — CQ endpoint (e.g., `https://cq.shouldersurf.com`)
- `CZ_CQ_APP_ID` — app identifier for CQ auth (default: `cloudzap`)
- `CZ_CQ_RECALL_TIMEOUT_MS` — max wait for recall (default: 200)

## Related Projects

- **Shoulder Surf** (`/Users/scottguida/ShoulderSurf/`) — iOS meeting copilot, first CloudZap customer
- **Context Quilt** (`/Users/scottguida/contextquilt/`) — persistent AI memory layer, live at `cq.shouldersurf.com`
- **Project Bifrost** (`/Users/scottguida/bifrost/`) — Nginx Proxy Manager on shared GCP VM
