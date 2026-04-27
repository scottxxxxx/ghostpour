# GhostPour

> *Formerly CloudZap. The pour you never see.*

[![Tests](https://github.com/scottxxxxx/ghostpour/actions/workflows/test.yml/badge.svg)](https://github.com/scottxxxxx/ghostpour/actions/workflows/test.yml)
[![Deploy](https://github.com/scottxxxxx/ghostpour/actions/workflows/deploy.yml/badge.svg)](https://github.com/scottxxxxx/ghostpour/actions/workflows/deploy.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

Open-source LLM API gateway with authentication, multi-provider routing, subscription-based access control, and usage tracking.

GhostPour sits between your mobile/web app and LLM providers (OpenAI, Anthropic, Google Gemini, xAI, DeepSeek, Kimi, Qwen). It keeps API keys server-side, authenticates users via Sign in with Apple, enforces tier-based rate limits and token quotas, and logs all usage.

> **Note:** Environment variables still use the `CZ_` prefix and some internal identifiers retain the "cloudzap" name for backwards compatibility with deployed clients.

## Features

- **Multi-provider routing** — 8 LLM providers (OpenAI, Anthropic, Google Gemini, xAI, DeepSeek, Kimi, Qwen, OpenRouter) through a single `/v1/chat` endpoint with auto model selection
- **Sign in with Apple** — Verify Apple identity tokens, issue JWT access/refresh tokens
- **Subscription tiers** — 3 tiers (free / plus / pro) with monthly cost cap, per-tier default model, summary mode, and image limits
- **Real-time subscription state** — Apple Server Notifications V2 webhook + StoreKit 2 receipt verification
- **SSE streaming** for `/v1/chat` with 90s wall-clock cap
- **Context Quilt integration** — pluggable feature hook for context recall + memory capture, with per-tier feature gating
- **Meeting reports** — server-generated HTML reports with cached retrieval, tier-driven model selection
- **Multi-app support** — `X-App-ID` header routes per-app configs for multiple iOS bundles (Shoulder Surf, Tech Rehearsal, Interview Buddy)
- **Per-app per-call-type model routing** with admin-dashboard editor
- **Server-side prompt assembly** — clients send `call_type`, server assembles `system_prompt` from registered config
- **Remote configs** with locale negotiation, version-aware updates, persistent volume seed
- **Admin dashboard** — overview, users, tiers, model routing, configs, live log, query log, providers, latency
- **Usage tracking** — per-request logging with token counts, cost breakdown, cached-token handling
- **Rate limiting** — per-user requests-per-minute enforcement
- **Zero provider keys on-device** — all API keys stay server-side

## Quick Start

```bash
# Clone
git clone https://github.com/scottxxxxx/ghostpour.git
cd ghostpour

# Configure
cp .env.example .env
# Edit .env with your JWT secret, Apple bundle ID, and provider API keys

# Run locally
docker compose up --build

# Health check
curl http://localhost:8000/health
```

## API Endpoints

### Auth, chat, usage

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check (version, pricing status, uptime) |
| POST | `/auth/apple` | None | Exchange Apple identity token for JWT |
| POST | `/auth/refresh` | None | Refresh access token |
| POST | `/v1/chat` | Bearer JWT | Proxied LLM request (supports `stream: true` for SSE) |
| POST | `/v1/verify-receipt` | Bearer JWT | StoreKit 2 receipt verification with idempotent allocation |
| POST | `/v1/sync-subscription` | Bearer JWT | Client-driven subscription state refresh |
| POST | `/v1/apple-notifications` | Apple JWS | Apple Server Notifications V2 webhook |
| GET | `/v1/usage/me` | Bearer JWT | Allocation, hours, usage stats, user_id |
| GET | `/v1/tiers` | None | Public tier catalog (display strings, feature_items, status_items, costs) |
| GET | `/v1/model-pricing` | None | Cached LLM model pricing (iOS fallback) |
| GET | `/v1/config/{slug}` | None | Locale-aware remote config (idle-tips, llm-providers, model-capabilities, protected-prompts, feature-highlights, etc.) |

### Context Quilt proxy (when `CZ_CQ_BASE_URL` is configured)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/capture-transcript` | Bearer JWT | End-of-session full-transcript capture (forwards to CQ + stores locally for report) |
| GET | `/v1/quilt/{user_id}` | Bearer JWT | Fetch user's quilt patches |
| GET | `/v1/quilt/{user_id}/graph` | Bearer JWT | Quilt graph visualization (svg/png/html) with `Cache-Control` + ETag |
| POST | `/v1/quilt/{user_id}/patches` | Bearer JWT | Create a patch manually |
| PATCH | `/v1/quilt/{user_id}/patches/{patch_id}` | Bearer JWT | Update patch (text, category, owner, project) |
| DELETE | `/v1/quilt/{user_id}/patches/{patch_id}` | Bearer JWT | Delete a patch |
| POST | `/v1/quilt/{user_id}/connections` | Bearer JWT | Create a patch-to-patch connection |
| DELETE | `/v1/quilt/{user_id}/connections` | Bearer JWT | Delete a connection |
| POST | `/v1/quilt/{user_id}/prewarm` | Bearer JWT | Pre-warm CQ Redis cache for this user |
| POST | `/v1/quilt/{user_id}/rename-speaker` | Bearer JWT | Rename a speaker label globally |
| POST | `/v1/quilt/{user_id}/reassign-speaker` | Bearer JWT | Reassign per-meeting speaker labels to self or another person |
| POST | `/v1/origins/{user_id}/{origin_type}/{origin_id}/assign-project` | Bearer JWT | Move an origin's patches to a different project |

### Meeting reports

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/meetings/{meeting_id}/report` | Bearer JWT | Generate a meeting report (LLM call, tier-driven model) |
| GET | `/v1/meetings/{meeting_id}/report` | Bearer JWT | Fetch cached meeting report (free, 30-day retention) |
| POST | `/v1/reports/render` | Bearer JWT | Re-render report HTML from existing JSON (template-only, free) |

### Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/webhooks/admin/dashboard` | X-Admin-Key | Admin dashboard (overview, users, tiers, model routing, configs, live log, query log, providers) |
| GET | `/webhooks/admin/live-log` | X-Admin-Key | Recent request log buffer (last 1000 entries) |
| GET | `/webhooks/admin/live-log/{request_id}` | X-Admin-Key | Single request detail by `X-Request-ID` |
| POST | `/webhooks/admin/set-tier` | X-Admin-Key | Manual tier assignment |

### POST /v1/chat

```json
{
  "provider": "auto",
  "model": "auto",
  "system_prompt": "You are a helpful assistant.",
  "user_content": "Summarize this meeting transcript...",
  "images": ["base64..."],
  "max_tokens": 4096,
  "stream": false,
  "context_quilt": true,
  "metadata": {
    "call_type": "query",
    "prompt_mode": "Ask",
    "meeting_id": "uuid",
    "project_id": "uuid",
    "project": "ABM",
    "locale": "en",
    "user_identified": true,
    "user_label": "Scott",
    "identification_source": "voice_id"
  }
}
```

Notes:
- `provider: "auto"` and `model: "auto"` resolve via per-app per-call-type routing config, falling back to the user's tier default.
- `stream: true` returns Server-Sent Events; the response is capped at 90s wall-clock and emits a `stream_timeout` event on overrun.
- `context_quilt: true` enables CQ recall + capture for that request (only if the user's tier has `context_quilt: enabled`).
- The `metadata` block accepts arbitrary keys; known consumers: `call_type`, `prompt_mode`, `image_count`, `session_duration_sec`, `meeting_id`, `project`, `project_id`, `locale`, `owner_speaker_label`, plus the identification fields above.

Response:
```json
{
  "text": "Here are the key points...",
  "input_tokens": 1523,
  "output_tokens": 342,
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "cost": {
    "input_cost": 0.0046,
    "output_cost": 0.0051,
    "cached_savings": 0.0,
    "total_cost": 0.0097
  }
}
```

Response headers include `X-Request-ID`, allocation headers (`X-Monthly-Used`, `X-Monthly-Limit`, `X-Allocation-Percent`), and CQ headers when applicable (`X-CQ-Matched`, `X-CQ-Entities`, `X-CQ-Patch-IDs`, `X-CQ-Gated`).

## Configuration

### Environment Variables (`.env`)

All prefixed with `CZ_`:

| Variable | Required | Description |
|----------|----------|-------------|
| `CZ_JWT_SECRET` | Yes | JWT signing secret (min 32 chars) |
| `CZ_APPLE_BUNDLE_ID` | Yes | Your iOS app's bundle ID |
| `CZ_OPENAI_API_KEY` | No | OpenAI API key |
| `CZ_ANTHROPIC_API_KEY` | No | Anthropic API key |
| `CZ_GOOGLE_API_KEY` | No | Google Gemini API key |
| `CZ_XAI_API_KEY` | No | xAI (Grok) API key |
| `CZ_DEEPSEEK_API_KEY` | No | DeepSeek API key |
| `CZ_KIMI_API_KEY` | No | Kimi (Moonshot) API key |
| `CZ_QWEN_API_KEY` | No | Qwen (Alibaba) API key |
| `CZ_ADMIN_KEY` | No | Admin key for tier management |

### Tier Configuration (`config/tiers.yml`)

Define subscription tiers with monthly cost cap, per-tier rate limits, default model, image limits, and per-feature state (`enabled` / `teaser` / `disabled`):

```yaml
tiers:
  free:
    display_name: "Free"
    monthly_cost_limit_usd: 0.35
    hours_per_month: 5
    requests_per_minute: 5
    default_model: "anthropic/claude-haiku-4-5-20251001"
    summary_mode: "off"
    max_images_per_request: 1
    features:
      context_quilt: "disabled"

  plus:
    display_name: "Plus"
    monthly_cost_limit_usd: 2.40
    requests_per_minute: 20
    default_model: "anthropic/claude-haiku-4-5-20251001"
    summary_mode: "interval"
    summary_interval_minutes: 5
    max_images_per_request: 3
    storekit_product_id: "com.example.app.plus.monthly"
    features:
      context_quilt: "teaser"

  pro:
    display_name: "Pro"
    monthly_cost_limit_usd: 5.10
    requests_per_minute: 60
    default_model: "anthropic/claude-sonnet-4-6"
    summary_mode: "interval"
    summary_interval_minutes: 2
    max_images_per_request: 5
    storekit_product_id: "com.example.app.pro.monthly"
    features:
      context_quilt: "enabled"
```

### Provider Configuration (`config/providers.yml`)

Register LLM providers with their API formats, endpoints, and model catalogs. See the included file for all 7 providers.

## Deployment

GhostPour deploys as a Docker container behind any reverse proxy (Nginx, Traefik, Nginx Proxy Manager, etc.).

### Production Docker Compose

```yaml
services:
  ghostpour:
    image: ghcr.io/scottxxxxx/ghostpour:latest
    container_name: ghostpour
    restart: unless-stopped
    expose:
      - "8000"
    volumes:
      - ghostpour-data:/app/data
    env_file:
      - .env.prod
    networks:
      - proxy-tier
```

### GitHub Actions CI/CD

The included workflow (`.github/workflows/deploy.yml`) builds and pushes to GHCR on every push to `main`, then deploys via SSH.

Required GitHub Secrets: `GCP_HOST`, `GCP_USERNAME`, `GCP_SSH_KEY`, `GCP_SSH_PASSPHRASE`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run locally (without Docker)
CZ_JWT_SECRET=dev-secret CZ_APPLE_BUNDLE_ID=com.example.app uvicorn app.main:app --reload
```

## Tech Stack

- **FastAPI** — async Python web framework
- **SQLite** (via aiosqlite) — lightweight persistence
- **PyJWT** — JWT token management
- **httpx** — async HTTP client for provider calls
- **Docker** — containerized deployment

## License

MIT
