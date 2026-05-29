# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **`reasoningLevels` + `promptReserveTokens` on each model in `llm-providers.json` (PR A1 — Option A consolidation, step 1)** — SS audit response confirmed Option A: collapse `model-capabilities.json`'s iOS-facing fields into `llm-providers.json` so the per-model contract has one canonical home. This PR ships the additions; SS's PR A2 will swap iOS reads (three call-sites — `LLMService.swift:744`, `ProjectChatSection.swift:1751`, `ModelCapabilities.swift:198`) over to `LLMModelConfig`; a follow-up PR A3 will remove `model-capabilities.json` as an iOS-facing config and move server-side routing intelligence (`contextSlots`, `contextQuilt`, `splitModelSummary`, `estimatedAvailableTokens`) into a non-published `config/internal/model-routing.json`. Three changes in this PR: (a) **`reasoningLevels: array<string> \| null`** per model — provider-native vocabularies, moved over verbatim from `model-capabilities.json` (e.g. Opus 4.7 = `["default","low","medium","high","xhigh","max"]`, Kimi K2.5 = `["default","disabled","enabled"]`, Haiku 4.5 = `null` because manual budget_tokens has no string picker). (b) **`promptReserveTokens: int \| null`** per model — all `null` for now (every model falls back to the file-level default — matches today's `defaultPromptReserveTokens(for: ...)` behavior on iOS). (c) **`defaultPromptReserveTokens: 8000`** at the top level — file-level fallback iOS reads when a model's per-model `promptReserveTokens` is null; key name confirmed with SS before either side coded. Also reconciled six `supportsReasoning` mismatches between `llm-providers.json` and `model-capabilities.json` so PR A2 stays a pure relocation (no behavior drift): `anthropic:claude-haiku-4-5-20251001`, `openrouter:anthropic/claude-haiku-4.5`, `kimi:kimi-k2-thinking`, `qwen:qwen-plus`, `qwen:qwen-flash`, `qwen:qwen3-max` all moved `true → false` to match the iOS-canonical-today source (`model-capabilities.json`). Today iOS gates the picker on the AND of `supportsReasoning && !reasoningLevels.isEmpty` so the picker correctly hides on these models; once A2 ships and reads only from `LLMModelConfig`, the AND still resolves correctly. Bumps `llm-providers.json` v10 → v11 across en/es/tr locales. Schema test `tests/test_llm_providers_per_model_fields.py` extended with 4 new tests (23 assertions total): required-fields presence for the new 2; `reasoningLevels` type + nullability invariants (null or non-empty array of non-empty strings); `promptReserveTokens` positive-int-or-null; `defaultPromptReserveTokens` positive-int top-level presence; `reasoningLevels`/`supportsReasoning` consistency invariants (non-empty levels imply supportsReasoning=true; supportsReasoning=false implies levels=null). Wire-shape spec updated at `docs/wire-contracts/llm-providers-fields.md`; SS handoff at `docs/handoffs/ss-config-cleanup-a1.md`; original proposal that led here at `docs/handoffs/ss-config-canonical-homes.md`.
- **Per-model capability fields in `llm-providers.json` (PR #183 — "PR B")** — 7 new per-model fields landed on every model in `llm-providers.json` (and the `.es` / `tr-` locale variants): `maxOutputTokens`, `temperatureDefault`, `maxImagesPerRequest`, `streamingSupported`, `toolUseSupported`, `cacheControlSupported`, `serverManaged`. Driven by an SS audit which found iOS was guessing or hardcoding per-model facts that should live in config. Per boss decision these live in `llm-providers.json` (next to existing per-model identity/capability fields), not split into `model-capabilities.json`. Key per-model choices: `temperatureDefault: null` on Anthropic Opus 4.7 / Sonnet 4.6 (the adaptive-thinking path 400s when temperature is sent — iOS must omit the field on the wire); `temperatureDefault: 0.3` on gpt-5.x / Gemini 3.x / Grok / DeepSeek / Qwen for the meeting-assistant workload (OpenAI's 1.0 default is too creative for factual summarization); `temperatureDefault: 0.6` on Kimi K2.5 (instant-mode default) and `1.0` on K2.6 / K2-Thinking (Moonshot's thinking-mode default); `serverManaged: true` only on `cloudzap.auto` (signals iOS to hit `/v1/chat` rather than BYOK-route); `cacheControlSupported: true` on Anthropic native + `anthropic/*` OR routes + `cloudzap.auto`. Bumps `llm-providers.json` v9 → v10. Forward-compatible — old iOS that doesn't know the new fields ignores them. New schema test `tests/test_llm_providers_per_model_fields.py` (17 assertions) enforces the invariants. Wire-shape spec at `docs/wire-contracts/llm-providers-fields.md`; SS handoff at `docs/handoffs/ss-per-model-fields.md`. PR A (cleanup of dead fields in `model-capabilities.json` + redundant per-model `supportsReasoning`) is the next follow-up.
- **Per-user web search caps (cost-reduction slice 2 — PRs #149, #150, #151, #152)** — explicit user-opt-in web search on Anthropic with per-tier monthly caps to bound cost exposure. Free tier hard-rejects (paywall CTA, no LLM call). Plus 75/mo hard cap. Pro 120/mo hard cap with 80 soft cap. Caps reset on the per-user `allocation_resets_at` cycle (anniversary-aligned with Apple billing for paid tiers). Database migration v18: `users.searches_used` counter + new `search_usage` audit table. AnthropicAdapter attaches `web_search_20250305` tool when `metadata.search_enabled=true`; chat-router gate evaluates pre-LLM and emits CTAs. Counter increment + audit row on response. Admin tile in dashboard for editing caps; per-user search-usage view at `GET /admin/user/{id}/search-usage`. Live counter and caps surfaced on `GET /v1/usage/me` so iOS can render an "X of Y used" pill before firing a search-bearing request. (#149)
  - **Enriched Free upsell CTA (#150)** to match the SS mockup: `header_icon`, `bullets[]` with SF Symbol icons, `footer`, `primary_action`/`secondary_action`. Plus/Pro hard-cap and Pro soft-cap CTAs normalized to the same shape. Three CTA `kind`s drive three iOS layouts: `search_paywall_required` (modal), `search_cap_exhausted` (toast/alert), `search_soft_cap_warning` (silent banner).
  - **SS-feedback follow-ups (#151)** — gate no-ops `search_enabled` for non-Anthropic providers (no other adapter wires the tool); `search_state.was_used: bool` on every populated sidecar so iOS branches on whether search actually ran rather than inferring from CTA presence; stop substituting `{reset_date}` server-side (iOS formats with `DateFormatter`/`Locale.current` using raw ISO from `search_state.resets_at`); `cta_only: true` on Free reject responses so iOS dispatches on the flag instead of branching on `text === ""`.
  - **Streaming SSE parity (#152)** — `search_state` now lands in the SSE `done` event with the same payload shape as the JSON response. Post-stream counter increment + audit row insert wired (mirrors non-streaming, fail-open on DB errors). Streaming Meeting Chat queries no longer silently drop search-cap CTAs.
  - Wire shape for SS in `docs/wire-contracts/search-caps.md`.
  - **`web_search` tier entitlement publication (#155, #156)** — adds `features.web_search` to `/v1/usage/me` (Free `disabled`, Plus/Pro/Admin `enabled`) so field-deployed iOS builds gating on `features["web_search"] == "enabled"` work without an app update. Unblocked the prod issue where Pro users on in-meeting freeform were routed to the "subscription required" sheet. Wire-contract doc clarifies `search.total > 0` remains canonical; `features.web_search` is fallback symmetry — both kept in sync server-side.
  - **Web search activity tile in admin user detail (#163)** — frontend wiring of the existing `/admin/user/{id}/search-usage` endpoint. Four summary cards (used/cap with color coding, period reset, total searches + Anthropic flat-fee cost over the dashboard window) plus an audit table flagging rows that hit the `max_uses=5` Anthropic ceiling. Surfaces the wire counter alongside per-user cost tables so ops can spot-check that the counter matches what we paid.
  - **Counter advance in response sidecar + non-Anthropic CTA + drift log (#164)** — three coupled fixes caught by SS smoke testing:
    - Bug fix: `search_state.used` was the pre-LLM snapshot — iOS pill froze after search-bearing responses. Now bumps `used += searches_performed` after DB increment in both streaming and non-streaming paths.
    - Non-Anthropic strip now surfaces `search_state` with `cta.kind: "search_unavailable_for_provider"` (SS-preferred approach (b): CTA dispatch over a free-form `reason` code). `cta_unavailable` template added to Plus + Pro across en/es/ja.
    - Defensive `logger.warning` when a paid-tier search-bearing request lands on a non-Anthropic provider — drift detector for future model-routing config changes. Today unreachable.
  - **Streaming web_search counter (#165) — silent-undercount fix** — PR #149's counter was wired into `AnthropicAdapter.parse_response` (non-streaming) but **not** into `send_request_stream`. Since `/v1/chat` always streams, every Anthropic `web_search` since #149 shipped was silently uncounted (no audit row, no per-user increment, no cost accrual on our side) while Anthropic kept billing $0.01/search. Pro users effectively bypassed the 120/mo cap on live, meeting, and project chat. Fix mirrors the parse_response counter inside the SSE loop, tracking `content_block_start` events whose `content_block.type == "server_tool_use"` and `name == "web_search"`. Two new unit tests pin multi-invocation counting and absence when the model declined to call the tool.
- **`tiers.json` v17 (en/es/ja)** — per-tier `feature_definitions.search` block with `searches_per_month`, `searches_soft_threshold`, `cta_hard_cap`, `cta_soft_cap`. Top-level `feature_definitions.search` metadata for the SS tier-card listing. All three locales in lockstep.
- **Model-routing granularity — per-surface dials (PRs #160, #161, #162)** — three-PR build-out of the model-routing config from a single shared `query` / `follow_up` row into nine per-surface call_types.
  - **(#160)** Search-tool nudge in the system prompt when `search_enabled` survives the gate (Haiku 4.5 was anchoring to in-context meeting summaries and declining `web_search`) + Pro `query` bumped from Haiku to Sonnet 4.6 (Sonnet uses tools more aggressively). Affected all interactive Pro paths — addressed surgically in #161.
  - **(#161)** Dedicated `project_chat` call_type entry. When `prompt_mode == "ProjectChat"`, resolver prefers the `project_chat` row over the shared `query` row. Server-only change; defaults preserved Sonnet for Pro.
  - **(#162)** Split single shared `follow_up` row into three per-surface follow-up dials plus a dedicated `meeting_chat` row mirroring `project_chat`. End state: 9 call_types — `summary` / `analysis` / `report` (background); `query` / `query_follow_up` (Copilot/freeform); `meeting_chat` / `meeting_chat_follow_up`; `project_chat` / `project_chat_follow_up`. Two-stage resolver: prompt_mode-aware surface preference catches legacy iOS clients sending `call_type=query` inside ProjectChat / PostMeetingChat; direct call_type lookup handles surfaces without a prompt_mode. Defaults: Haiku free/plus, Sonnet pro on first sends; Haiku across all tiers on follow-ups (cheap refinements rarely benefit from Sonnet on top of a Sonnet first answer). Dashboard auto-renders all rows (data-driven over `Object.entries(call_types)`). Wire-contract spec at `docs/wire-contracts/model-routing-call-types.md`.
- **CQ recall block as its own cache breakpoint (PR #158)** — Anthropic adapter slices `system_prompt` into `[prefix, recall, suffix]` with `cache_control: ephemeral` on prefix + recall, isolating the CQ recall block as its own breakpoint so the base prefix keeps cross-turn caching even when recall content differs across turns. Hook stashes the sanitized recall on `metadata.cq_recall_block`; non-Anthropic adapters ignore the metadata. Fallback to legacy single-block layout when recall is missing/empty/not located. Anticipates [CQ PR #89](https://github.com/scottxxxxx/contextquilt/pull/89): byte-stable recall within a 5-min window for the same input + 30s render cache on the rendered RecallResponse. Adds 1 breakpoint (prefix) on top of the existing 1 (recall) for a total of 2 against Anthropic's 4-breakpoint budget.
- **CQ `(you)`-suffix sanitizer kill-switch (PR #159)** — `CZ_CQ_DISABLE_YOU_SUFFIX_SANITIZER` flag (default `false`) gates the render-time `_sanitize_you_suffix` regex in the CQ feature hook. CQ confirmed PR #43 (extraction voice rules) and PR #93 (self-typed-patch voice + owner stripping) tightened upstream extraction so new patches use second-person "You" natively. Canary plan: flip on, spot-check 1 week, then delete the function. Default behavior unchanged.

### Changed
- **Fix action-item over-attribution in the report prompt** — the single rule "Attribute action items to specific people by name when the transcript supports it" (`app/services/meeting_report.py`) was producing two failure modes: (1) soft suggestions ("a great step would be us taking a look", "we should consider") got promoted to firm action items, and (2) owners were assigned by name-proximity rather than by who actually committed — e.g. a meeting where someone noted "Scott drafted the success criteria" produced a false "Scott: review the success criteria" action even though Scott never spoke to it and another person proposed the review. Replaced with two principles: an action requires a concrete commitment or assignment that someone took on (not a hypothetical), and the owner is whoever committed to doing it (not whoever is named nearby or authored a related document); unowned proposals become open questions. Validated with before/after report generation against two real meetings: in the first, the false "Scott" action correctly moved to an open question; in the second, Scott's genuine verbal commitment ("Yeah, I can talk to them") was correctly retained while borderline non-commitments dropped. No real actions were suppressed in either — dropped items were either genuinely ownerless or got consolidated. Single-line prompt change, easy to revert. No wire shape change, no migration, no SS coordination.
- **Tighten `suggested_tags` rule in the report prompt** — `app/services/meeting_report.py` line 71 previously instructed the model to return 1-4 tags from the taxonomy, which the model treated as "produce 3-4 every time" because the upper bound anchored its default. Result: tags became monotonous (every meeting got "Action Items", "Decisions", "Follow-ups") and stopped carrying any distinguishing signal. New rule: at most 2 tags, applied only when they capture what makes THIS meeting distinct from a typical meeting, with most meetings producing 1 tag or 0. Prompt now explicitly says "do not apply a tag just because it could plausibly fit" and requires a reason explaining what makes the tag distinguishing. Single-line prompt change, easy to revert by restoring the previous wording. No wire shape change, no migration, no SS coordination required. Worth eyeballing for a few meetings post-deploy to confirm tags actually got more discriminating instead of meetings losing tags they should have had.

### Fixed
- **`subscription_tier` forwarding on `/admin/capture-transcript` (PR #157)** — the admin path was the last write call site missing `subscription_tier` forwarding to CQ; PR #112 covered `/v1/capture-transcript` and the chat `after_llm` hook but the admin endpoint was skipped. SELECT now pulls `tier` + `simulated_tier` alongside `display_name`/`email`, computes `effective_tier` with the same `simulated_tier or tier` precedence, and forwards it through `cq.capture()`. Closes the inference gap CQ flagged in their 30-day `extraction_metrics` reconciliation — admin-injected captures were landing without a tier tag, biasing the cost-by-tier dashboard.
- **Request-log streaming mislabel (PR #166)** — `StreamingBypassMiddleware` decided "(streaming)" from `"stream":true` in the request body before the handler ran. Handlers that override streaming and return JSON (Project Chat being the canonical case) were mislabeled in both the summary log line and the dashboard live-log, with body shown as `"(streaming)"` instead of the actual JSON response. Now decided from the response `content-type` (`text/event-stream`) inside the `http.response.start` callback; the two log-path branches collapsed into one. 5 new ASGI-level tests cover JSON-response-to-stream-request not mislabeled (the bug), real SSE labeled correctly with body suppressed, log-line suffix, skip-paths bypass, and `x-request-id` injection.
- **`allocation_resets_at` reset bugs (PR #148)** — two adjacent issues that would have compounded into the search-cap reset cycle:
  1. **30-day drift** — every `now + timedelta(days=30)` site accumulated ~5 days/year of error vs Apple's calendar-month billing. Replaced with `relativedelta(months=1)` for the local fallback. Apple-aware paths (the webhook handler) now anchor `allocation_resets_at` to `expiresDate` from the signed transaction — Apple's value bakes in calendar/end-of-month edge cases (Jan 31 → Feb 28 → Mar 31, leap years) and stays in sync with what Apple actually charges.
  2. **DID_RENEW same-tier no-op** — the Apple webhook historically early-returned on a renewal where the user's tier didn't change (i.e., normal monthly Plus → Plus renewal), leaving `monthly_used_usd` accumulating across renewals indefinitely and `allocation_resets_at` frozen on the original subscription date. A subscriber on a stable tier would hit their cost cap permanently after one billing cycle. Replaced the early-return with `_renew_same_tier` which zeros `monthly_used_usd` + `searches_used` and advances `allocation_resets_at` to Apple's `expiresDate`.
  3. **Lazy-reset safety net** — `usage_tracker.check_quota` now calls `lazy_reset_if_due` on every read. Atomic WHERE-guarded UPDATE that catches missed/delayed Apple webhooks AND Free users (no Apple webhook path). Multi-month inactivity gaps preserve the original day-of-month anchor by always computing from the original stale date rather than chaining single-month deltas (which loses the anchor after the first end-of-month snap-back).
  - New `app/services/allocation_reset.py` centralizes `compute_next_reset` / `roll_forward_past` / `lazy_reset_if_due`. 20 new tests covering Apple `expiresDate` override, calendar edge cases, multi-month gaps with anchor preservation, lazy-reset due/not-due/race-loss/null states, and the same-tier-renewal fix end-to-end.
  - `python-dateutil` promoted to an explicit dependency.

### Removed
- **Project Chat count quota — Slice 5 deprecation (PR #167)** — the Free-tier count quota for Project Chat (PRs #80, #93) was superseded by the budget gate (PRs #109–#121). Soak window 2026-05-02 → 2026-05-10 let older iOS builds migrate to `feature_state.credits_*` + `budget_exhausted`. This PR removes the machinery: `app/services/project_chat_quota.py` (whole module); `users.project_chat_used_this_period` and `users.project_chat_period` columns (migration v19: `ALTER TABLE … DROP COLUMN`; older SQLite silently skips); `send_to_gp_with_cta` verdict + `quota_remaining` / `quota_exhausted` / `unlimited` CTA kinds from `project_chat_policy.py`; `free_quota_per_month` + quota CTA strings from `features.yml` and `tiers.{,es,ja}.json`; `feature_state` emission on the unblocked `/v1/chat` Project Chat path (the blocked path's `budget_exhausted` envelope is the contract now); Project Chat's two `zero_quota_on_tier_change` calls in `/v1/verify-receipt` (memory-capture's still fires — different feature). `current_period_utc` + `next_period_resets_at` extracted to a new `app/services/period.py` so `memory_capture_quota.py` doesn't import from a deleted module. `resolve_project_chat_verdict` reduced to routing only: `(is_logged_in, tier, gp_chat_flag, selected_model)` → `send_to_gp` / `send_to_user_model` / `login_required`. `docs/wire-contracts/project-chat.md` rewritten as a routing-only contract. Net -910 lines across 19 files; iOS builds older than the budget-gate migration see no `quota_*` signal in `feature_state` — server still enforces spend via the budget gate so behavior degrades to "silent until `budget_exhausted`."

### Reverted
- **`__CQ_BREAK__` prompt-cache marker (PR #147 reverts PR #146)** — the marker mechanism added in PR #146 was structurally unreachable end-to-end on the iOS architecture. SS deliberately puts meeting context into `user_content`, not `system_prompt`; the post-marker template slots (`context_quilt`/`summary`/`project`) resolve to empty in `SessionManager.swift`, leaving the marker block as the literal last 16 characters of the system prompt with nothing after it. The cache savings we measured during smoke-test came from `cache_control: ephemeral` on the single system block, which was already in the code pre-PR #146; the marker contributed nothing. SS unwound `LLMService.stripGPSentinelIfNeeded` in their own commit. `protected-prompts` bumped to v7 with the marker removed; SS retained the slot-mirroring fix and pull-to-refresh `ProtectedPrompts.reload()` they shipped during this debugging pass — both real bugs found in transit.

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
    - **JSON-pointer sub-key surgery (#168)** — endpoint now accepts RFC 6901 pointers (entries starting with `/`) alongside legacy top-level keys. Deep bundle adds like `/limits/project_chat/defaultPromptReserveTokens` can land without clobbering sibling dashboard edits. Dashboard modal walks bundle leaves and renders one row per pointer; arrays are opaque leaves. The PR #121 scenario that motivated the original endpoint is now actually deployable through the modal — no SSH or container restart needed.
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
