# CLAUDE.md — GhostPour

> **Last updated:** March 26, 2026
> **Formerly:** CloudZap. Env vars still use `CZ_` prefix and some identifiers retain "cloudzap" for backwards compatibility with deployed clients.

## Project Overview

**GhostPour** is an open-source LLM API gateway built with FastAPI. It sits between client apps and LLM providers, handling auth, routing, rate limiting, usage tracking, and subscription-based access control. The first customer is Shoulder Surf (iOS meeting copilot).

**Live deployment:** `https://cz.shouldersurf.com`
**Admin dashboard:** `https://cz.shouldersurf.com/admin`
**GitHub:** `https://github.com/scottxxxxx/cloudzap`
**Subscription spec:** `/Users/scottguida/ShoulderSurf/Subscription_Tiers.md` — full tier details, pricing, allocation, carryover, StoreKit config
**Planning docs:** `shouldersurf-proxy-claude-code-plan.docx`, `Server side proxy-claude-code-plan.docx` (in repo root, gitignored)

### Deep-dive docs

| Doc | Covers |
|-----|--------|
| `docs/subscription-system.md` | Full-stack subscription lifecycle: StoreKit + GhostPour + allocation enforcement |
| `docs/feature-gating.md` | 3-state feature gating, Context Quilt integration, adding new features |
| `docs/remote-config.md` | iOS remote config system (`GET /v1/config/{name}`) |
| `docs/deployment.md` | GCP VM, Docker, CI/CD, admin dashboard |

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
├── main.py              # FastAPI app factory, lifespan (loads FeatureConfig), middleware
├── config.py            # pydantic-settings with CZ_ env prefix, feature_config_path
├── database.py          # aiosqlite init + schema + migrations
├── dependencies.py      # get_current_user (JWT verification)
├── models/
│   ├── chat.py          # ChatRequest / ChatResponse Pydantic models
│   ├── feature.py       # FeatureState enum, FeatureDefinition, FeatureConfig, load_feature_config()
│   ├── tier.py          # TierDefinition with feature_state(), is_feature_enabled(), is_feature_teaser()
│   └── user.py          # UserRecord model
├── routers/
│   ├── auth.py          # POST /auth/apple, POST /auth/refresh
│   ├── chat.py          # /v1/chat, /v1/usage/me, /v1/tiers, /v1/verify-receipt, /v1/sync-subscription, /v1/capture-transcript
│   ├── config.py        # GET /v1/config/{name} (remote config for iOS app)
│   ├── health.py        # GET /health, GET /admin, GET /v1/model-pricing
│   └── webhooks.py      # Admin endpoints (dashboard, users, tiers, errors, simulate-tier, feature-state, capture-transcript, provider-status, update-key)
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
├── tiers.yml            # Subscription tier definitions (features dict, feature_bullets, descriptions)
├── features.yml         # Feature definitions with display metadata
├── providers.yml        # Provider registry (URLs, auth, models)
└── remote/              # iOS remote config JSON files (see docs/remote-config.md)
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

See **`/Users/scottguida/ShoulderSurf/Subscription_Tiers.md`** for the full tier specification, and **`docs/subscription-system.md`** for the full-stack implementation guide.

5 tiers + admin, configured in `config/tiers.yml`. Model assignment is server-controlled — client sends `model: "auto"`, gateway substitutes the tier's `default_model`.

Summary: free ($0.05), standard ($2.99), pro ($4.99), ultra ($9.99), ultra_max ($19.99). Haiku for free/standard/pro, Sonnet for ultra/ultra_max. 2x markup on avg user cost. Monthly cost-based allocation with overage credits at same rate. Dollar-value carryover on upgrade.

### Allocation tracking

- `monthly_cost_limit_usd` per tier (derived from hours x model cost)
- `monthly_used_usd` tracked per user, resets on subscription renewal date
- `overage_balance_usd` per user (purchased credit packs)
- Usage priority: monthly allocation → overage balance → on-device fallback
- `X-Allocation-Percent` and `X-Allocation-Warning` headers on every chat response
- `GET /v1/usage/me` returns `user_id`, allocation, hours, overage, usage stats, and per-tier feature states

### JWT design rule

**Never encode tier in JWT.** Always read tier from the database on every request. This ensures tier changes (upgrades, downgrades, admin overrides) take effect immediately without waiting for token expiry.

## Auto Model Routing

When the iOS app sends `provider: "auto", model: "auto"`, the chat endpoint:
1. Looks up the user's tier from the database
2. Reads `default_model` from the tier config (e.g., `"anthropic/claude-sonnet-4-6"`)
3. Splits into provider + model
4. Routes to the correct upstream provider

This means subscribers never choose a model — GhostPour picks the best one for their tier.

## iOS Settings Locks (for GhostPour users)

When the iOS app's provider is set to GhostPour (legacy ID: "cloudzap"), these settings are locked:

| Setting | BYOK (own key) | GhostPour managed |
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
- **YAML config, not database config**: Tier definitions, provider catalogs, and feature definitions are version-controlled. Feature states are per-tier in `tiers.yml`; feature metadata lives in `features.yml`.
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
- `CZ_CQ_BASE_URL` — Context Quilt endpoint (e.g., `https://cq.shouldersurf.com`)
- `feature_config_path` — path to features.yml (default: `config/features.yml`, set in `app/config.py`)

## Database

3 tables, raw SQL (no ORM), with versioned migrations in `app/database.py`:

**users**: `id`, `apple_sub`, `email`, `display_name`, `tier`, `monthly_cost_limit_usd`, `monthly_used_usd`, `overage_balance_usd`, `allocation_resets_at`, `simulated_tier`, `simulated_exhausted`, `is_trial`, `trial_start`, `trial_end`, `is_active`, `metadata`, timestamps

**refresh_tokens**: `id`, `user_id`, `token_hash`, `expires_at`, `revoked`, `created_at`

**usage_log**: `id`, `user_id`, `provider`, `model`, `input_tokens`, `output_tokens`, `cached_tokens`, `estimated_cost_usd`, `response_time_ms`, `status`, `error_message`, `call_type`, `prompt_mode`, `image_count`, `session_duration_sec`, `request_timestamp`, `metadata` (JSON)

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Health check + pricing status |
| GET | `/admin` | None (key in UI) | Admin dashboard web UI |
| POST | `/auth/apple` | None | Apple Sign In → JWT |
| POST | `/auth/refresh` | None | Refresh token rotation |
| POST | `/v1/chat` | Bearer JWT | Proxied LLM request (auto model routing, feature gating) |
| POST | `/v1/capture-transcript` | Bearer JWT | End-of-meeting transcript capture for Context Quilt |
| GET | `/v1/quilt/{user_id}` | Bearer JWT | Proxy: fetch user's quilt patches from Context Quilt |
| PATCH | `/v1/quilt/{user_id}/patches/{patch_id}` | Bearer JWT | Proxy: update a quilt patch |
| DELETE | `/v1/quilt/{user_id}/patches/{patch_id}` | Bearer JWT | Proxy: delete a quilt patch |
| POST | `/v1/verify-receipt` | Bearer JWT | StoreKit receipt verification |
| POST | `/v1/sync-subscription` | Bearer JWT | Subscription state sync from iOS |
| GET | `/v1/usage/me` | Bearer JWT | User's allocation, overage, usage stats, features |
| GET | `/v1/tiers` | None | Public tier catalog (server-driven subscription UI) |
| GET | `/v1/config/{name}` | None | Remote config for iOS app (see `docs/remote-config.md`) |
| GET | `/v1/model-pricing` | None | Cached LiteLLM pricing JSON |
| GET | `/webhooks/admin/dashboard` | X-Admin-Key | Usage stats, latency, top users |
| GET | `/webhooks/admin/users` | X-Admin-Key | User list with lifetime stats |
| GET | `/webhooks/admin/user/{user_id}` | X-Admin-Key | Single user detail |
| GET | `/webhooks/admin/tiers` | X-Admin-Key | Tier config viewer |
| GET | `/webhooks/admin/errors` | X-Admin-Key | Recent error log |
| POST | `/webhooks/admin/set-tier` | X-Admin-Key | Manual tier assignment |
| POST | `/webhooks/admin/simulate-tier` | X-Admin-Key | Tier simulation for testing |
| POST | `/webhooks/admin/update-feature-state` | X-Admin-Key | Override feature state for a tier |
| POST | `/webhooks/admin/capture-transcript` | X-Admin-Key | Send transcript to CQ on behalf of a user |
| GET | `/webhooks/admin/provider-status` | X-Admin-Key | Provider health check |
| POST | `/webhooks/admin/update-key` | X-Admin-Key | Update provider API key |
| GET | `/docs` | None | Swagger UI |

**Planned endpoints:**
- `POST /v1/add-credits` — Add overage credits after StoreKit purchase verification

## Reserved Route Namespaces

- `/auth/*` — Sign in with Apple, JWT (shared with future Context Quilt)
- `/v1/*` — Chat, pricing, usage, Context Quilt proxy
- `/webhooks/*` — Admin, Apple webhooks
- `/memory/*` — Reserved for future Context Quilt

## Testing

```bash
pytest tests/ -v
```

41 tests covering: JWT, tier enforcement, provider routing, base64 redaction, rate limiting, generic adapter, pricing/cost calculation.

## Related Projects

- **Shoulder Surf** (`/Users/scottguida/ShoulderSurf/`) — iOS meeting copilot, first GhostPour customer
- **Context Quilt** (`/Users/scottguida/contextquilt/`) — persistent AI memory layer, live at `cq.shouldersurf.com`
- **Project Bifrost** (`/Users/scottguida/bifrost/`) — Nginx Proxy Manager on shared GCP VM
