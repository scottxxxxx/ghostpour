# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-03-19

### Added
- **Admin dashboard** (`/admin`) — dark-themed web UI with Overview, Models, Users, Tiers, and Latency tabs
- **Auto model routing** — client sends `model: "auto"`, gateway resolves to tier's `default_model`
- **5 subscription tiers** — free, standard, pro, ultra, ultra_max (+ admin), each with default model, cost limits, and rate limits
- **Generic provider adapter** (`api_format: "generic"`) — add new LLM providers via YAML config alone, no code changes
- **Pricing service** — fetches LLM model costs from LiteLLM JSON on startup, refreshes daily, configurable source URL
- **Cost breakdown** in every `/v1/chat` response: `input_cost`, `output_cost`, `cached_savings`, `total_cost`, `billable_input_tokens`
- **Cached token handling** — subtracts cached tokens from billable count (OpenAI, Anthropic, Gemini patterns)
- **Reasoning token cost** support (OpenAI o-series, DeepSeek reasoner)
- **`/v1/model-pricing`** endpoint — serves cached pricing data as iOS app fallback
- **Full usage metadata** capture in `usage` response field (cached tokens, reasoning tokens, finish reason, etc.)
- **`metadata` JSON column** in `usage_log` table for complete provider telemetry
- **Dot-path field extraction** (`response_mappings`) for generic adapter response parsing
- **Provider config template** in `providers.yml` with inline documentation
- **Admin API endpoints**: `GET /webhooks/admin/dashboard`, `GET /webhooks/admin/users`, `GET /webhooks/admin/tiers`
- **Tier simulate** — admin can switch their account to any tier to test access rules
- **Latency percentiles** (p50/p75/p90/p95/p99) in dashboard
- **Database migrations** — `ALTER TABLE` support for schema changes on existing deployments
- **Open-source community docs** — CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CHANGELOG.md, issue/PR templates
- **CI test workflow** — runs pytest on PRs and pushes to main
- **README badges** — tests, deploy, license, Python version

### Changed
- LICENSE copyright holder updated to WEIRTECH
- `/v1/pricing` renamed to `/v1/model-pricing` for clarity
- Health endpoint now includes pricing status
- Docker compose files: removed deprecated `version` field, fixed GHCR image name casing

## [0.1.0] - 2026-03-18

### Added
- Initial release
- Multi-provider LLM routing (OpenAI, Anthropic, Google Gemini, xAI, DeepSeek, Kimi, Qwen)
- Sign in with Apple authentication with JWT access/refresh tokens
- Subscription tier system with YAML configuration
- Per-user rate limiting (requests per minute)
- Daily token quota enforcement
- Usage logging to SQLite
- Manual admin tier management endpoint
- Health check endpoint
- Docker deployment with GitHub Actions CI/CD
- Raw request/response JSON passthrough (base64 redacted)
- Interactive API docs at `/docs`
