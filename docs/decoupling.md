# Decoupling GhostPour from Shoulder Surf

> **Last updated:** April 1, 2026
> **Status:** Phase 1 in progress (test harness), Phase 2 pending

## Why

GhostPour is marketed as a generic LLM gateway but has Shoulder Surf meeting copilot concepts hardcoded into its core: prompt modes, meeting IDs, CQ capture skip lists, transcript endpoints, and StoreKit product IDs. A second customer would inherit SS's domain concepts. We need to decouple while keeping SS working exactly as-is.

## Constraint

**SS iOS app must not break.** Same requests, same responses, same headers. Every refactor step is backwards compatible and verified by the integration test harness.

## Current SS-Specific Coupling

| Area | What's Hardcoded | Generic Alternative |
|------|-----------------|-------------------|
| `ChatRequest` fields | `meeting_id`, `session_duration_sec`, `prompt_mode`, `call_type` | Generic `metadata: dict` |
| CQ capture skip list | `PostMeetingChat`, `ProjectChat`, `AutoSummary`, `PostSessionAnalysis` | Config-driven per feature |
| CQ integration | Recall/capture embedded in chat endpoint | Pluggable feature hook |
| Endpoints | `/capture-transcript`, `/quilt/*`, `/meetings/*` | Separate optional router |
| Tier config | SS StoreKit product IDs | Per-app product ID mapping |
| `/v1/usage/me` | `summary_mode`, `summary_interval_minutes` | Nested `app_config` key |
| Remote configs | Global namespace | Per-app prefix convention |

## Phase 1: Integration Test Harness (safety net)

Built before any refactoring to catch regressions.

### What exists (`tests/integration/`)

**Fixtures** (`tests/conftest.py`):
- Isolated temp SQLite DB per test
- Real app with lifespan, tier config, feature config
- Mocked provider (canned `ChatResponse`), CQ (tracked calls), pricing (known costs)
- Pre-seeded users at free/standard/pro tiers + exhausted/trial variants

**Chat endpoint tests** (`tests/integration/test_chat_e2e.py` — 18 tests):
- Auto model resolution, allocation headers, auth
- Quota exhausted → 429, 80% warning
- Model access blocked → 403
- CQ recall injection, capture fire/skip, teaser gating, disabled
- Usage logging with SS field passthrough

### Still needed
- Auth flow (Apple auth, refresh rotation)
- Subscription lifecycle (verify-receipt, sync, usage/me)
- Remote config versioning
- CQ proxy endpoint coverage

## Phase 2: Decouple (each step independently deployable)

### 2.1 ChatRequest metadata abstraction
Add `metadata: dict` to ChatRequest with a validator that copies top-level SS fields into it. Add `get_meta(key)` helper. All downstream code reads via helper. SS sends same JSON — validator handles the mapping.

### 2.2 Configurable capture skip list
Move hardcoded `_cq_skip_modes` from `chat.py` into `config/features.yml` under `context_quilt.capture_skip_modes`. Read from config at runtime.

### 2.3 Extract CQ into a feature hook
Define `FeatureHook` protocol (`before_llm`, `after_llm`, `response_headers`). Move CQ logic from chat.py into `app/services/features/context_quilt_hook.py`. Chat endpoint calls hooks generically. CQ hook registered at startup if `CZ_CQ_BASE_URL` is set.

### 2.4 Separate CQ router
Move `/quilt/*`, `/meetings/*`, `/capture-transcript` to `app/routers/cq_proxy.py`. Conditionally included in `main.py` only when CQ is configured.

### 2.5 Namespace StoreKit product IDs
Change `storekit_product_id: str` → `app_product_ids: dict[str, str]` in tier config. Keep backwards-compat property.

### 2.6 Document remote config pattern
Document per-app slug prefix convention in `docs/remote-config.md`. No code change.

### 2.7 Extract SS fields from /v1/usage/me
Move `summary_mode`, `summary_interval_minutes`, `max_images_per_request` under `app_config` key. Keep top-level for backwards compat.

## Sequencing

```
Phase 1: Tests first (1.1-1.2 done, 1.3-1.6 pending)
Phase 2:
  2.1, 2.2, 2.4, 2.5, 2.6, 2.7 — independent, any order
  2.3 — after 2.1 and 2.2 (builds on metadata + config skip list)
```

## Verification

After each Phase 2 step:
1. `pytest tests/ -v` — all tests must pass
2. Deploy
3. SS sends a real request — verify identical behavior
