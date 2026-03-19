# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Generic config-driven provider adapter (`api_format: "generic"`) — add new LLM providers via YAML alone, no code changes needed
- Full provider usage metadata capture in `usage` response field (cached tokens, reasoning tokens, finish reason, response ID, etc.)
- `metadata` JSON column in `usage_log` table for complete provider telemetry
- Dot-path field extraction (`response_mappings`) and automatic usage flattening for nested provider responses
- Provider config template in `providers.yml` with inline documentation
- Pricing service: fetches LLM model costs from LiteLLM's JSON (configurable via `CZ_PRICING_SOURCE_URL`)
- Cost breakdown in every `/v1/chat` response: `input_cost`, `output_cost`, `cached_savings`, `total_cost`, `billable_input_tokens`
- Cached token handling: subtracts cached tokens from billable count (OpenAI, Anthropic, Gemini patterns)
- Reasoning token cost support (OpenAI o-series, DeepSeek reasoner)
- Daily cost limit per tier (`daily_cost_limit_usd` in `tiers.yml`)
- `estimated_cost_usd` now populated in usage_log from pricing data
- Health endpoint shows pricing status (loaded, model count, source URL)

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
