# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Email Management feature (PRs #124, #126, #129)** — full inbound/outbound pipeline via Resend.
  - **Webhook ingestion** at `POST /webhooks/resend` with Svix signature verification, idempotent dedupe via `svix-id`, persistence to a new `email_events` audit log. Hard bounces and spam complaints land in `email_suppression` (PK = lowercased recipient — never sent to again). Soft bounces are logged only (provider retries). (#124)
  - **Read-only Email tab in the admin dashboard** — events log with type/recipient filters, suppression list, hard-bounce + complaint counters, recent activity timestamps. (#126)
  - **Outbound + iOS toggle + unsubscribe** — `app/services/email_send.py` wraps Resend with a mandatory pre-send suppression check. `users.marketing_opt_in` (default 0, GDPR explicit-opt-in) with `marketing_opt_in_updated_at` + `marketing_opt_in_source` audit fields. New iOS-facing endpoints: `PUT /v1/preferences/marketing-opt-in`, `GET /v1/preferences/me`, and `marketing_opt_in.{enabled, updated_at, source}` on `/v1/usage/me` (the SS startup query). Public `GET /unsubscribe?token=...` link with HMAC token (no expiry, domain-separated). `email.complained` webhook flips both suppression AND marketing_opt_in. (#129)
- **`client-config` remote config + locale-aware Project Chat char cap (PRs #127, #128)** — new runtime-tunables file at `GET /v1/config/client-config` with `Accept-Language` fallback. First tunable: `limits.project_chat.max_input_chars` per tier per locale. EN/ES defaults match legacy `max_input_tokens × 4`; JA halved (Haiku 200K context fits ~2 chars/token CJK content). Server enforcement cuts over to char-based via `app/services/client_config.py`; legacy `tiers.{slug}.feature_definitions.project_chat.max_input_tokens` stays on `/v1/tiers` for back-compat. New `PUT /admin/tunable/project-chat-cap` dual-writes both fields with per-locale support; dashboard Tiers-tab editor grew a locale dropdown.
- **Secret Manager infrastructure (PRs #123, #130, #132, #135, #138)** — env-first → GCP Secret Manager fallback for app secrets, eliminating plaintext-on-disk as the only auth surface.
  - `app.secrets.get_secret(name, env_var=...)` helper. (#123)
  - Pass `cloud-platform` scope explicitly so the SM SDK doesn't 403 with `requires_scopes=True` against GCE metadata creds. (#130)
  - 5-minute TTL cache (tunable via `CZ_SECRET_CACHE_TTL_SECONDS`) so rotations propagate without container restart. (#132)
  - `_ensure_secrets_in_env()` at startup auto-fills 11 known secrets from SM when env is empty — `.env.prod` can be slimmed per-secret. (#135)
  - Structured `secret_filled_from_sm` log for migration verification. (#138)
- **`/v1/tiers` exposes `feature_definitions` per tier + top-level `version`** — fixes a missed wiring from PR #120. iOS reads `tiers[slug].feature_definitions.project_chat.max_input_tokens`; the field was on disk but never copied into the response. 4 endpoint tests pin the wire shape. (#125)
- **Footgun audit + remediation sweep (PRs #131–#142, plus ghostpour-ops ops work)** — 15 known footguns inventoried 2026-05-03 and fixed in one day:
  - **C1** Force-sync from bundle (#131) — `POST /admin/config/{slug}/sync-from-bundle` + dashboard modal preview. Closes the silent gap where bundle JSON updates merged via PR never reached prod (root cause of PR #121 and the tiers v15 confusion).
  - **C2** `get_secret` TTL cache (#132).
  - **C3** `email_events` 90-day retention prune at `init_db` time (#133), mirroring `meeting_reports`'s 30-day prune.
  - **C4** `/v1/health` alias (#134) — silences NPM bifrost 404 polls.
  - **C5** `.env.prod` → SM fallback (#135).
  - **M1** Dashboard auto-reauth on any admin-key 403 (#136) — wraps `window.fetch` so a rotated `CZ_ADMIN_KEY` bumps the user to the auth screen instead of leaving silent failures.
  - **M2** Webhook auth visibility (#137) — structured `webhook_auth_failure` logs, startup warning if the webhook secret is unreachable, `webhook.{signing_secret_configured, last_event_received_at}` on `/admin/email/stats`.
  - **M3** `secret_filled_from_sm` log (#138).
  - **M5** Locale-drift indicator on Configs tab (#139) — orange ⚠ chip when a locale variant's version doesn't match base.
  - **H1** Litestream switches to VM metadata-service identity (#142) — drops the `gp-backup-sa.json` key file mount; bucket IAM granted to the VM Compute SA.
  - **H2** `/unsubscribe` per-IP rate limit, 30/min/IP via the existing `RateLimiter` (#140). Belt-and-suspenders — HMAC tokens are unforgeable.
  - **H5** GitHub Actions bumped to Node-24 versions (#141) — pre-empts June 2026 forced cutover.
  - **M4** (operational, ghostpour-ops) — daily 04:00 UTC `mariadb-dump` of the bifrost NPM DB to GCS, 30-day retention via lifecycle policy. SPOF resolved (proxy routing + Let's Encrypt state).
  - **H3** (operational) — `ResendKey.txt` orphan deleted.
- **CQ tier signals** — `subscription_tier` field added to `/v1/memory` and `/v1/recall` metadata on every CQ call (resolved from `user.effective_tier`); new `POST /v1/users/{id}/tier-change` notification fired by GP on real subscription state transitions (event types: `upgrade`, `downgrade`, `trial_start`, `trial_to_paid`, `cancellation`, `expire`, `refund`). Lets CQ slice extraction metrics by tier and drive its own retention/soft-delete policy without GP encoding it. See `docs/wire-contracts/cq-tier-signals.md`.
- **Unified budget gate (PR #117)** — `usage_tracker.check_quota` no longer raises 429 / `allocation_exhausted`. The budget gate is the sole authority for over-cap responses, emitting one wire shape (200 + `feature_state.cta { kind: "budget_exhausted" }`) across both "already past cap" and "this call would push past cap." Simulated-exhausted admin testing toggle keeps the 429 path.
- **No exemption for `summary` / `analysis` call_types (PR #119)** — every LLM call gates past cap, including AutoSummary / DeltaSummary / SummaryConsolidation / PostSessionAnalysis. iOS owns the primary "don't allow meeting start when over cap" UX (reads `credits_remaining` from `/v1/usage/me`); GP is defense-in-depth. Earlier exemption from PR #117 reverted after product call.
- **`max_input_tokens` dashboard tunable + JSON-as-source-of-truth (PR #120)** — Project Chat context cap is now editable per-tier from the admin dashboard's Tiers tab. `tiers.json` is the canonical store; `tiers.yml` is a fallback default. New endpoint `PUT /webhooks/admin/tunable/tier-field` writes to all locale variants in lockstep. `GET /webhooks/admin/config/{slug}` and `/webhooks/admin/tiers` re-read from disk on every call so external file edits propagate to the dashboard immediately.
- **`promptReserveTokens` scaffolding (PR #121)** — top-level `defaultPromptReserveTokens: 8000` in `model-capabilities.json` (+ locale variants). Per-model override via `models.{slug}.promptReserveTokens`. iOS uses this for the Project Chat fuel-gauge denominator: `model.contextWindow - reserveTokens` for external models, `tier.max_input_tokens` for SS AI.
- **`share` icon support in tier feature_items (PR #118)** — restored on Plus + Pro "Shareable meeting reports" rows across en/es/ja after a prior version-bump silently overwrote it. iOS's `featureItemSymbol(for:)` already understands `share`.
- **No-overwrite contract on `seed_remote_configs` (PR #118)** — bundled (repo) configs only seed fresh containers; persistent (dashboard) edits are sacred regardless of version. Repo-side version bumps will no longer silently wipe live admin work. Pinned by 4 unit tests in `tests/test_remote_config_seed.py`.
- **Budget gate (Slice 1–4)** — pre-call cost estimate blocks Free-tier `/v1/chat` and `/v1/meetings/{id}/report` before any LLM tokens are spent. Replaces the count-based Project Chat quota (deprecation in follow-up). Char/4 input-token heuristic matches iOS fuel gauge; $0.05 overage tolerance. See `docs/wire-contracts/budget-gate.md`.
- **Credits abstraction** — wire-facing `credits_{used,total,remaining,resets_at}` fields on `/v1/usage/me` and `/v1/chat` budget-block responses. 1¢ = 100 credits; server-canonical conversion so the ratio can shift later without an iOS update. Free $0.35 budget surfaces as 3,500 credits.
- **Per-tier `max_input_tokens` context cap** — Free 50K / Plus 150K / Pro 180K, exposed at `tiers.{tier}.feature_definitions.project_chat.max_input_tokens` in `tiers.json` (and locale variants). 413 + `context_too_large` CTA + `details {max_tokens, actual_tokens, tokenizer}` server-side as defense-in-depth for the iOS-side fuel-gauge block.
- **Canned/sample meeting report** — when a Free user is over budget, returns a placeholder report (no LLM call) persisted with `report_status="placeholder_budget_blocked"` and `is_editable=false`. Editable from `config/remote/canned-report.json`; localized via `.es` / `.ja` variants.
- **`placeholder_report_count`** on `/v1/verify-receipt` response — lets iOS prompt regen for the most recent canned report after upgrade without scanning the meeting list.
- **Meeting-report localization** — `Accept-Language` directive on the LLM system prompt for narrative content; `report-strings.{locale}` remote configs for template chrome (section headers, table labels). En/es/ja shipped; "+ Lang" from the dashboard for additional locales.
- **CTA wire contract** — stable `kind` + `action` fields on `feature_state.cta`. `kind` ∈ {`budget_exhausted`, `report_blocked_budget_exhausted`, `context_too_large`, `login_required`, `unlimited`, `quota_remaining`, `quota_exhausted`}; `action` ∈ {`open_paywall`, `sign_in`, `trim_context`, null}. Localized `text`, stable enums.
- **3-tier restructure** — free, plus ($6.99), pro ($14.99); legacy 5-tier product IDs removed (no subscribers to grandfather)
- **SSE streaming** for `/v1/chat` (`stream: true` in request body) with raw-ASGI passthrough middleware for chunk-level delivery
- **90-second wall-clock cap** on streaming `/v1/chat`; emits `stream_timeout` event on overrun
- **Meeting reports** — `POST /v1/meetings/{meeting_id}/report` (LLM-generated), `GET /v1/meetings/{meeting_id}/report` (cached), `POST /v1/reports/render` (template-only re-render). Tier-driven model selection (free=Haiku, paid=Sonnet) with optional `quality=fast|best` override
- **Server-controlled report model** — clients send `ai_tier` instead of model name; server resolves
- **4-level stoplight** (red/orange/yellow/green) and sentiment emoji + suggested tags in report JSON
- **Apple Server Notifications V2 webhook** (`POST /v1/apple-notifications`) with JWS verification for real-time subscription state
- **`POST /v1/sync-subscription`** for client-driven cancellation handling
- **Trial support** — explicit `is_trial` flag on `/v1/verify-receipt`; idempotent (no allocation reset on app launch)
- **CQ proxy endpoints** — `POST /v1/quilt/{user_id}/patches` (manual create), `POST/DELETE /v1/quilt/{user_id}/connections`, `POST /v1/quilt/{user_id}/prewarm`, `GET /v1/quilt/{user_id}/graph` (svg/png/html, with `Cache-Control` + ETag), `POST /v1/quilt/{user_id}/rename-speaker`, `POST /v1/quilt/{user_id}/reassign-speaker` (per-meeting `from_labels: [{label, meeting_id}]`)
- **Origin reassignment** — `POST /v1/origins/{user_id}/{origin_type}/{origin_id}/assign-project` to move a meeting's patches to a different project
- **Speaker-identification metadata** — `user_identified`, `user_label`, `identification_source` forwarded to CQ on both `/v1/chat` and `/v1/capture-transcript` (top-level or `metadata: {...}`)
- **Multi-app support** — `X-App-ID` header routes per-app configs (Shoulder Surf, Tech Rehearsal, Interview Buddy)
- **Per-app per-call-type model routing** with admin-dashboard editor
- **Server-side prompt assembly** — clients send `call_type`, server assembles `system_prompt` from registered config (Path B)
- **Quick-prompt context enforcement** — `requiresContext` flag in protected-prompts config; client gates protected prompts when context is empty
- **Remote config persistence** — `GET /v1/config/{slug}` with locale negotiation, version-aware updates, persistent volume seed; admin-dashboard editor with auto-version bump
- **Spanish + Japanese localization** — tiers, feature-highlights, idle-tips, llm-providers, model-capabilities, protected-prompts
- **Structured tier display** — `feature_items` (icon hints: checkmark, brain, chat, etc.) and `status_items` (settings status section) on `/v1/tiers`
- **`feature-highlights` remote config** for pre-sign-in marketing bullets
- **OpenRouter provider** with verified model IDs and pricing
- **`X-Request-ID`** header on every response (12-char hex); request log buffer expanded to 1000 entries
- **`X-CQ-Patch-IDs`** response header on `/v1/chat` (top 20 patch UUIDs from CQ recall)
- **`X-CQ-Matched`, `X-CQ-Entities`, `X-CQ-Gated`** response headers from CQ feature hook
- **Admin dashboard tabs** — Live Log (with path filter), Query Log, Model Routing, Providers (API key + balance management), live-log-by-request-id detail view
- **Anthropic prompt caching** (`cache_control` on system prompt) and persistent HTTP connections to providers
- **Project Chat free-tier teaser** — canned upsell response, no LLM call
- **Locale-aware CQ recall** — client `locale` field forwarded to CQ metadata
- **Communication style injection** — CQ-returned style appended to system prompt for `ProjectChat` and `PostMeetingChat` modes
- **`logger.info` extras rendered in stdout** — `cq_recall_ok matched=N patch_count=N` etc., previously dropped
- **`cost_per_hour_usd` and `monthly_cost_limit_usd`** exposed on `/v1/tiers`
- **`hours_per_month`** on free tier (~5 hours, cost-enforced)

### Changed
- **Tiers**: 5 → 3 (free, plus, pro). Plus/Pro are unlimited AI (no per-month credit cap)
- **`(you)` suffix sanitization** on all chat request content (system prompt + user content), not just CQ context — prevents LLM echo of identity markers
- **CQ proxy**: 401 from upstream is mapped to 502 `upstream_auth_error` (don't trigger client refresh loop on server-to-server auth failure)
- **Origin scoping**: CQ v1 alignment — `origin_id` + `origin_type` replace `meeting_id` (which is still accepted as a deprecated alias)
- **Report template** — server-owned HTML at `app/static/report_template.html`; design updates without App Store review
- **`/v1/usage/me`** `hours_limit` derived from tier config `hours_per_month`, not cost-divided

### Fixed
- **SSE streaming** — `BaseHTTPMiddleware` was materializing entire response body before sending; replaced with pure ASGI middleware that bypasses body capture for streams
- **`verify-receipt`** — was resetting `monthly_used_usd` on every app launch; now idempotent
- **Capture-transcript** — was silently dropping speaker-identification metadata fields (Pydantic strict model + missing forward); both fixed
- **`(you)` suffix** leaking from CQ patches into LLM output
- **CQ proxy** passing through CQ's 401 as GP's own auth rejection (caused client refresh loops)
- **Admin dashboard** showing wrong current tier in simulate dialog
- **`/v1/usage/me`** showing 7h instead of 5h on free tier (cost-derived vs config-derived)
- **Report 500** from undefined `now` variable in cache save path
- **Research notes** in reports rendering raw context instead of LLM responses

### Removed
- **Overage / carryover system** — replaced by simple per-tier monthly cost cap
- **Legacy 5-tier product IDs** (no subscribers to grandfather)
- **Client-facing model names** in API responses and report template (server picks model)

## [0.4.0] - 2026-03-26

### Added
- **GhostPour** rename (formerly CloudZap); env vars retain `CZ_` prefix for backwards compat
- **Context Quilt integration** — recall (inject context into system prompt) + capture (async query/response forwarding); pluggable via `FeatureHook` protocol with per-tier `enabled`/`teaser`/`disabled` states
- **CQ proxy endpoints** — `GET /v1/quilt/{user_id}` (fetch patches), `PATCH /v1/quilt/{user_id}/patches/{patch_id}` (update text/category/owner), `DELETE /v1/quilt/{user_id}/patches/{patch_id}`
- **`POST /v1/capture-transcript`** — end-of-session full-transcript capture; stores locally for report generation and forwards to CQ for knowledge extraction
- **`POST /v1/sync-subscription`** — client-driven subscription state refresh (cancellations, renewals)
- **Trial support** — `is_trial` column on users; cancellation downgrades to free with exhausted allocation
- **StoreKit product IDs** in tiers config; `product-ids.yml` mounted from GitHub secret on deploy
- **Display name** on users (forwarded to CQ for owner attribution)
- **Remote config endpoints** — `GET /v1/config/{slug}` for iOS-side config (idle tips, LLM providers, model capabilities, protected prompts)
- **Admin Providers tab** — API key status, balances, key management with disk persistence
- **Admin tier simulation** — `simulated_tier` column lets admins test tier-gated behavior end-to-end
- **`projectid`** forwarded to CQ for patch grouping; rename cascade support
- **JWT bearer auth** to CQ (replacing legacy `X-App-ID`)

### Changed
- Renamed `cloudzap` → `ghostpour` across infra, image names, repo, container names

### Fixed
- Quilt proxy routes had double `/v1` prefix
- Capture-transcript route had double `/v1` prefix

## [0.3.0] - 2026-03-21

### Added
- **5 subscription tiers** — free, standard ($2.99), pro ($4.99), ultra ($9.99), ultra_max ($19.99) with per-tier default models, summary modes, image limits, and StoreKit product IDs
- **Monthly allocation tracking** — `monthly_used_usd`, `overage_balance_usd`, `allocation_resets_at` on users table
- **Dollar-value carryover** on tier upgrade (Option D) — unused allocation converts to overage balance
- **`POST /v1/verify-receipt`** — StoreKit 2 receipt verification, maps product ID to tier with carryover
- **`GET /v1/usage/me`** — authenticated user's allocation, hours, overage, and usage stats
- **Allocation headers** — `X-Allocation-Percent`, `X-Allocation-Warning`, `X-Monthly-Used`, `X-Monthly-Limit`, `X-Overage-Balance` on every chat response
- **Per-user detail view** in admin dashboard — monthly budget, usage by query type/prompt mode/model
- **Query analytics** — `call_type`, `prompt_mode`, `image_count`, `session_duration_sec`, `cached_tokens` tracked per request (no content stored)
- **Tiers tab** in admin dashboard with simulate button for testing any tier
- **Image limits per tier** — Free: 0, Standard: 1, Pro: 2, Ultra: 3, Ultra Max: 5

### Changed
- **JWT no longer contains tier** — always read from database for immediate upgrade/downgrade effect
- Tier config expanded with `monthly_cost_limit_usd`, `summary_mode`, `summary_interval_minutes`, `storekit_product_id`
- Daily cost/token limits replaced by monthly allocation system

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
