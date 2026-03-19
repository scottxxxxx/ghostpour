# CloudZap

Open-source LLM API gateway with authentication, multi-provider routing, subscription-based access control, and usage tracking.

CloudZap sits between your mobile/web app and LLM providers (OpenAI, Anthropic, Google Gemini, xAI, DeepSeek, Kimi, Qwen). It keeps API keys server-side, authenticates users via Sign in with Apple, enforces tier-based rate limits and token quotas, and logs all usage.

## Features

- **Multi-provider routing** — 7 LLM providers through a single `/v1/chat` endpoint
- **Sign in with Apple** — Verify Apple identity tokens, issue JWT access/refresh tokens
- **Subscription tiers** — Configurable access control (providers, models, token quotas, rate limits)
- **Usage tracking** — Per-request logging with token counts and latency
- **Rate limiting** — Per-user requests-per-minute enforcement
- **Zero provider keys on-device** — All API keys stay server-side

## Quick Start

```bash
# Clone
git clone https://github.com/scottguida/cloudzap.git
cd cloudzap

# Configure
cp .env.example .env
# Edit .env with your JWT secret, Apple bundle ID, and provider API keys

# Run locally
docker compose up --build

# Health check
curl http://localhost:8000/health
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check |
| POST | `/auth/apple` | None | Exchange Apple identity token for JWT |
| POST | `/auth/refresh` | None | Refresh access token |
| POST | `/v1/chat` | Bearer JWT | Proxied LLM request |
| POST | `/webhooks/admin/set-tier` | X-Admin-Key | Manual tier assignment |

### POST /v1/chat

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "system_prompt": "You are a helpful assistant.",
  "user_content": "Summarize this meeting transcript...",
  "images": ["base64..."],
  "max_tokens": 4096
}
```

Response:
```json
{
  "text": "Here are the key points...",
  "input_tokens": 1523,
  "output_tokens": 342,
  "model": "claude-sonnet-4-6",
  "provider": "anthropic"
}
```

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

Define subscription tiers with per-tier limits:

```yaml
tiers:
  free:
    daily_token_limit: 50000
    requests_per_minute: 5
    allowed_providers: ["openai", "anthropic"]
    allowed_models: ["gpt-5-nano", "claude-haiku-4-5-20251001"]
    max_images_per_request: 0

  subscriber:
    daily_token_limit: 500000
    requests_per_minute: 30
    allowed_providers: ["*"]
    allowed_models: ["*"]
    max_images_per_request: 5
```

### Provider Configuration (`config/providers.yml`)

Register LLM providers with their API formats, endpoints, and model catalogs. See the included file for all 7 providers.

## Deployment

CloudZap deploys as a Docker container behind any reverse proxy (Nginx, Traefik, Nginx Proxy Manager, etc.).

### Production Docker Compose

```yaml
services:
  cloudzap:
    image: ghcr.io/scottguida/cloudzap:latest
    container_name: cloudzap
    restart: unless-stopped
    expose:
      - "8000"
    volumes:
      - cloudzap-data:/app/data
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
