# Subscription System: ShoulderSurf + StoreKit + GhostPour

> **Last updated:** March 24, 2026
>
> This document describes how subscriptions are purchased, synced, enforced, and displayed across the full stack. Reference this from both the ShoulderSurf and GhostPour `CLAUDE.md` files.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Tier Definitions](#tier-definitions)
3. [Subscription Lifecycle](#subscription-lifecycle)
4. [Allocation Enforcement](#allocation-enforcement)
5. [Usage Reporting to the iOS App](#usage-reporting-to-the-ios-app)
6. [Tier-Locked Settings](#tier-locked-settings)
7. [On-Device Fallback](#on-device-fallback)
8. [Feature Gating (3-State)](#feature-gating-3-state)
9. [Admin Testing / Simulation](#admin-testing--simulation)
10. [Future: Overage Credit Purchases](#future-overage-credit-purchases)

---

## Architecture Overview

Three systems collaborate to manage subscriptions:

```
+------------------+        +------------------+        +------------------+
|  Apple App Store |        |   ShoulderSurf   |        |    GhostPour     |
|    (StoreKit 2)  |        |    (iOS app)     |        |   (API gateway)  |
+--------+---------+        +--------+---------+        +--------+---------+
         |                           |                           |
         | Product catalog           | POST /auth/apple          | JWT issued
         |<------------------------->|-------------------------->|----------->
         |                           |                           |
         | Purchase / renewal        | POST /v1/verify-receipt   | Tier updated
         |<------------------------->|-------------------------->| allocation reset
         |                           |                           |
         | currentEntitlements       | POST /v1/sync-subscription| Tier reconciled
         |<------------------------->|-------------------------->|
         |                           |                           |
         |                           | POST /v1/chat             | Model routed,
         |                           |<------------------------->| quota enforced,
         |                           |                           | cost deducted
         |                           |                           |
         |                           | GET  /v1/usage/me         | Hours, %, limits
         |                           |<------------------------->| returned
         |                           |                           |
         | Cancellation /            | POST /v1/sync-subscription| Downgrade to free
         | expiry                    |   (product_id: null)      |
         |<------------------------->|-------------------------->|
```

**Ownership boundaries:**

| Concern | Owner |
|---------|-------|
| Payment processing, receipts, trials, renewals | Apple (StoreKit 2) |
| Purchase UI, entitlement detection, fallback UX | ShoulderSurf (iOS) |
| Tier state, allocation tracking, model routing, quota enforcement | GhostPour (server) |

**Key principle:** GhostPour is the source of truth for the user's tier and allocation. The iOS app tells GhostPour what StoreKit says, and GhostPour decides what the user gets.

---

## Tier Definitions

Configured in `config/tiers.yml`. Five purchasable tiers plus admin:

```
+-------+----------+--------+-----------+-------+--------+--------+---------+------------+
| Tier  | Price    | Model  | $/month   | Hours | Images | CQ     | Summary | Sum. Interval |
+-------+----------+--------+-----------+-------+--------+--------+---------+------------+
| free  | $0       | Haiku  | $0.05     |   1   |   1    | off    | delta   | 10 min     |
| std   | $2.99    | Haiku  | $1.25     |  25   |   1    | teaser | delta   | 10 min     |
| pro   | $4.99    | Haiku  | $2.50     |  50   |   2    | on     | delta   | 10 min     |
| ultra | $9.99    | Sonnet | $4.75     |  25   |   3    | on     | choice  | 15 min     |
| umax  | $19.99   | Sonnet | $9.50     |  50   |   5    | on     | choice  | 15 min     |
| admin | internal | Sonnet | unlimited |  inf  |  10    | on     | choice  | 10 min     |
+-------+----------+--------+-----------+-------+--------+--------+---------+------------+
```

**How cost limits map to hours:**
- Haiku tiers: `$0.05/hour` (so $1.25 limit = 25 hours)
- Sonnet tiers: `$0.19/hour` (so $4.75 limit = 25 hours)

**Why summary interval varies by tier:** Auto-summaries are full chat requests that consume allocation. Every summary burns tokens. Sonnet is ~4x more expensive per token than Haiku, so Sonnet tiers default to 15-minute intervals instead of 10 to prevent users from burning through hours unexpectedly. This is a per-tier setting in `tiers.yml` (`summary_interval_minutes`) that the iOS app reads from `/v1/usage/me` and locks when using GhostPour.

**StoreKit product IDs** (mapped in `tiers.yml`):
- `com.weirtech.shouldersurf.sub.standard.monthly`
- `com.weirtech.shouldersurf.sub.pro.monthly`
- `com.weirtech.shouldersurf.sub.ultra.monthly`
- `com.weirtech.shouldersurf.sub.ultramax.monthly`

---

## Subscription Lifecycle

### Full flow diagram

```
   User taps "Subscribe"
            |
            v
   +------------------+
   | StoreKit purchase |
   | product.purchase()|
   +--------+---------+
            |
            | Transaction verified locally
            v
   +---------------------------+
   | iOS: verifyWithCloudZap() |
   | POST /v1/verify-receipt   |
   +--------+------------------+
            |
            | { product_id, transaction_id, offer_type, offer_price }
            v
   +-------------------------------+
   | GhostPour: purchase endpoint  |
   | 1. Map product_id -> tier     |
   | 2. Detect trial (offer=intro, |
   |    price=0)                   |
   | 3. Reset allocation:          |
   |    monthly_used = 0           |
   |    overage_balance = 0        |
   |    limit = tier's limit       |
   | 4. Set resets_at (7d trial,   |
   |    30d paid)                  |
   +--------+----------------------+
            |
            | { status, new_tier, monthly_limit_usd }
            v
   +---------------------------+
   | iOS: fetchUsageConfig()   |
   | GET /v1/usage/me          |
   | Refresh tier constraints  |
   +---------------------------+
```

### On every app launch: sync-subscription

```
   App launches
        |
        v
   StoreKit: Transaction.currentEntitlements
        |
        +-- Has entitlement?
        |       |
        |       v  YES
        |   POST /v1/sync-subscription
        |   { active_product_id: "com.weirtech...", is_trial: false }
        |       |
        |       +-- Tier matches & trial state matches? --> { action: "none" }
        |       +-- Tier or trial state mismatch? --> Update tier + limit
        |           +-- Trial-to-paid? --> Reset allocation to full limit
        |           +-- Other mismatch? --> Update limit only
        |
        +-- No entitlement?
                |
                v
            POST /v1/sync-subscription
            { active_product_id: null }
                |
                +-- Already free? --> { action: "none" }
                +-- Was subscribed? --> Downgrade to free,
                    reset allocation, { action: "downgraded" }
```

### Upgrade behavior

When a user upgrades (e.g., Standard -> Pro):

1. StoreKit processes the purchase (Apple prorates the charge)
2. iOS calls `POST /v1/verify-receipt` with the new product ID
3. GhostPour sets the new tier and **resets allocation to the new tier's full limit**
4. `monthly_used_usd` resets to 0 -- the user gets a fresh allocation
5. No carryover of unused hours from the old tier

**Why no carryover:** Apple discounts upgrades (prorated credit from the old subscription). We don't lose money, but we also don't stack hours. The user simply gets the new tier's full allocation starting now.

### Cancellation / expiry

1. Apple stops renewing the subscription
2. On next app launch, `currentEntitlements` returns empty
3. iOS calls `POST /v1/sync-subscription` with `active_product_id: null`
4. GhostPour downgrades to free tier, resets allocation to $0.05

### Trial flow

```
   User taps "Start Free Trial" (Standard tier)
        |
        v
   StoreKit: 7-day introductory offer, price $0
        |
        v
   iOS: POST /v1/verify-receipt
   { product_id: "...standard...", offer_type: "introductory", offer_price: 0 }
        |
        v
   GhostPour: detect trial
   - tier = "standard"
   - is_trial = true
   - monthly_cost_limit_usd = $0.50 (trial cap, NOT $1.25)
   - monthly_used_usd = 0
   - allocation_resets_at = 7 days from now
   - trial_end = 7 days from now
        |
        |   User uses the app for 7 days.
        |   Allocation is capped at $0.50 (10 hours of Haiku).
        |   This prevents a user from burning through the full
        |   $1.25 monthly allocation in 3 days then cancelling.
        |
        v
   Day 7: StoreKit auto-charges $2.99 (or user cancels)
        |
        +-- User did NOT cancel:
        |       |
        |       v
        |   Next app launch: POST /v1/sync-subscription
        |   { active_product_id: "...standard...", is_trial: false }
        |       |
        |       v
        |   GhostPour detects trial-to-paid conversion:
        |   - is_trial was true, now false
        |   - Reset: monthly_used_usd = 0  (fresh start)
        |   - Upgrade: monthly_cost_limit_usd = $1.25  (full limit)
        |   - New period: allocation_resets_at = 30 days from now
        |   - Clear: trial_start = null, trial_end = null
        |   - Response includes: trial_converted: true
        |
        +-- User cancelled during trial:
                |
                v
            StoreKit lets trial run to day 7, then expires.
            Next app launch: POST /v1/sync-subscription
            { active_product_id: null }
                |
                v
            GhostPour downgrades to free:
            - tier = "free"
            - monthly_cost_limit_usd = $0.05
            - monthly_used_usd = 0
```

**Why the trial cap exists:** Without it, a user could subscribe to a 7-day free trial, burn through all 25 hours of Standard in 3 days, then cancel before Apple charges them. The trial cap ($0.50 = 10 hours) limits our exposure during the unpaid period.

**StoreKit does not push to GhostPour.** Apple doesn't notify our server when a trial converts to paid. We only find out when the iOS app launches and calls `sync-subscription`. The conversion is detected by comparing `user.is_trial` (true in our DB) against `body.is_trial` (false from StoreKit). This means the user's allocation upgrade happens on their next app launch after day 7, not at the exact moment of conversion.

---

## Allocation Enforcement

### Per-request flow in `/v1/chat`

```
   POST /v1/chat (Bearer JWT)
        |
        v
   1. Resolve user from JWT
   2. Look up tier from DB (never from JWT)
        |
        v
   3. Auto model routing
      provider: "auto", model: "auto"
      --> tier.default_model (e.g., "anthropic/claude-haiku-4-5-20251001")
      --> split into provider + model
        |
        v
   4. Check model access
      - tier.allowed_providers contains provider?
      - tier.allowed_models contains model?
      - len(images) <= tier.max_images_per_request?
      --> 403 if any fail
        |
        v
   5. Rate limit check
      - In-memory token bucket per user
      - tier.requests_per_minute
      --> 429 if exceeded
        |
        v
   6. Quota check (usage_tracker.check_quota)
      - Read monthly_used_usd from DB
      - Compare to tier.monthly_cost_limit_usd
      - (Uses trial_cost_limit_usd if in trial)
      --> 429 "allocation_exhausted" if used >= limit
        |
        v
   7. Feature gating (Context Quilt, etc.)
        |
        v
   8. Route to upstream provider (Anthropic, etc.)
        |
        v
   9. Calculate cost (LiteLLM pricing, cached token discount)
        |
        v
  10. Record cost: monthly_used_usd += request_cost
        |
        v
  11. Return response + allocation headers
```

### Response headers on every chat response

```
X-Allocation-Percent: "45.3"      // % of monthly limit used
X-Allocation-Warning: "true"      // present when >= 80%
X-Monthly-Used: "1.1325"          // dollars used this period
X-Monthly-Limit: "2.50"           // dollars allowed this period
```

### 429 allocation_exhausted response

When the user has used all their hours:

```json
{
  "detail": {
    "code": "allocation_exhausted",
    "message": "Monthly allocation exhausted ($2.5000/$2.50). Upgrade your plan for more hours.",
    "details": {
      "monthly_used": 2.5,
      "monthly_limit": 2.5,
      "fallback": "on_device"
    }
  }
}
```

The iOS app detects this and shows the upgrade prompt sheet.

---

## Usage Reporting to the iOS App

### GET /v1/usage/me (authenticated)

Called by the iOS app on launch and after purchases. Returns everything the app needs:

```json
{
  "tier": "pro",
  "tier_display_name": "Pro",
  "allocation": {
    "monthly_limit_usd": 2.50,
    "monthly_used_usd": 0.4567,
    "monthly_remaining_usd": 2.0433,
    "percent_used": 18.3,
    "resets_at": "2026-04-23T15:30:00+00:00"
  },
  "hours": {
    "used": 9.1,
    "limit": 50.0,
    "remaining": 40.9
  },
  "overage": {
    "balance_usd": 0,
    "balance_hours": 0
  },
  "this_month": {
    "requests": 42,
    "input_tokens": 15234,
    "output_tokens": 8901,
    "cached_tokens": 1200,
    "cost_usd": 0.4567
  },
  "summary_mode": "delta",
  "summary_interval_minutes": 10,
  "max_images_per_request": 2,
  "features": { "context_quilt": "enabled" },
  "is_trial": false,
  "trial_end": null
}
```

**Hours conversion:**
- `hours.used = monthly_used_usd / model_cost_per_hour`
- `hours.limit = monthly_limit_usd / model_cost_per_hour`
- Haiku = $0.05/hr, Sonnet = $0.19/hr

### GET /v1/tiers (public, no auth)

Server-driven subscription UI. The iOS app renders tier cards from this data instead of hardcoding descriptions:

```json
{
  "tiers": {
    "standard": {
      "display_name": "Standard",
      "description": "Monthly AI meeting assistance for regular use.",
      "hours_per_month": 25,
      "features": { "context_quilt": "teaser" },
      "feature_bullets": [
        "~25 hours of AI assistance",
        "Claude Haiku - fast and capable",
        "Auto-summaries every 10 min"
      ],
      "storekit_product_id": "com.weirtech.shouldersurf.sub.standard.monthly"
    }
  },
  "feature_definitions": {
    "context_quilt": {
      "display_name": "Context Quilt",
      "teaser_description": "Your AI found connections to your past meetings",
      "upgrade_cta": "Upgrade to Pro to unlock meeting memory"
    }
  }
}
```

---

## Tier-Locked Settings

When the iOS app's provider is set to GhostPour (legacy ID: "cloudzap"), these settings are server-controlled:

```
+-------------------------+------------------+---------------------+
| Setting                 | BYOK (own key)   | GhostPour managed   |
+-------------------------+------------------+---------------------+
| Model selection         | User choice      | Locked: "auto"      |
|                         |                  | (server picks)      |
+-------------------------+------------------+---------------------+
| Auto-summary interval   | User choice      | Locked per tier:    |
|                         | (2-15 min)       | 10 min (Haiku) /    |
|                         |                  | 15 min (Sonnet)     |
+-------------------------+------------------+---------------------+
| Summary mode            | User choice      | Locked per tier:    |
|                         |                  | "delta" or "choice" |
+-------------------------+------------------+---------------------+
| Max images per query    | 5                | Locked per tier:    |
|                         |                  | 1 / 2 / 3 / 5      |
+-------------------------+------------------+---------------------+
| Image resolution        | User picker      | Locked: 1024px      |
+-------------------------+------------------+---------------------+
| Context Quilt           | User choice      | Tier-gated:         |
|                         |                  | enabled/teaser/off  |
+-------------------------+------------------+---------------------+
```

The iOS app reads these from `/v1/usage/me` response fields: `summary_mode`, `summary_interval_minutes`, `max_images_per_request`, `features`.

---

## On-Device Fallback

When allocation is exhausted, the iOS app offers a graceful fallback:

```
   429 allocation_exhausted
        |
        v
   UpgradePromptView shown
        |
        +-- User taps "Upgrade" --> StoreKit purchase flow
        |
        +-- User taps "Continue with on-device AI"
                |
                v
            Switch provider from GhostPour to Apple Intelligence
            - No API key needed
            - Works offline
            - Subsequent queries use on-device model
            - Auto-summaries paused (to avoid repeated 429s)
```

---

## Feature Gating (3-State)

Features like Context Quilt have three states per tier, configured in `tiers.yml`:

```
+-----------+----------------------------------------------+
| State     | Behavior                                     |
+-----------+----------------------------------------------+
| enabled   | Full feature: check + apply + capture        |
| teaser    | Preview: check + show metadata, skip apply   |
|           | Returns X-CQ-Gated: "true" header            |
|           | iOS shows "upgrade to unlock" nudge          |
| disabled  | Feature doesn't run at all                   |
+-----------+----------------------------------------------+
```

**Example: Context Quilt across tiers**

```
   Free user sends a chat query
   --> CQ state: disabled
   --> Nothing happens, no CQ headers

   Standard user sends a chat query
   --> CQ state: teaser
   --> CQ recalls matching entities from past meetings
   --> Headers: X-CQ-Matched: "3", X-CQ-Entities: "Bob,Widget 2.0"
   --> Headers: X-CQ-Gated: "true"
   --> iOS shows: "Your AI found connections to past meetings. Upgrade to Pro to unlock."
   --> Context NOT injected into prompt

   Pro user sends a chat query
   --> CQ state: enabled
   --> CQ recalls matching entities
   --> Context injected into system prompt
   --> Response is enriched with cross-meeting knowledge
   --> CQ captures query+response for future recall
```

**Client opt-out:** `skip_teasers: ["context_quilt"]` in ChatRequest suppresses teaser headers after the user dismisses the nudge.

---

## Admin Testing / Simulation

The admin dashboard can simulate any tier for a user without changing their real subscription:

```
   POST /webhooks/admin/simulate-tier
   { "user_id": "...", "tier": "ultra", "exhausted": true }
        |
        v
   User's effective_tier becomes "ultra"
   All enforcement uses ultra tier constraints
   If exhausted=true, every chat request returns 429
        |
        v
   iOS shows upgrade prompt (thinks allocation is exhausted)
   Test the full upgrade flow end-to-end
        |
        v
   POST /webhooks/admin/simulate-tier
   { "user_id": "...", "tier": null }
   --> Clears simulation, reverts to real tier
```

Simulation is transparent in `/v1/usage/me`:
```json
{
  "simulation": {
    "active": true,
    "simulated_tier": "ultra",
    "real_tier": "free",
    "exhausted": true
  }
}
```

---

## Future: Overage Credit Purchases

The database column `overage_balance_usd` exists but is currently unused. When StoreKit credit pack purchases are added:

**What already exists:**
- `overage_balance_usd` column on users table
- `overage` section in `/v1/usage/me` response (currently hardcoded to 0)
- `UserRecord.overage_balance_usd` field

**What needs to be built:**
1. `POST /v1/add-credits` endpoint -- verify StoreKit receipt, increment `overage_balance_usd`
2. Re-enable overage deduction in `record_cost` -- when monthly allocation is exhausted, deduct from overage before returning 429
3. Re-enable overage check in `check_quota` -- fall through to overage balance before rejecting
4. Un-hardcode the `overage` section in `/v1/usage/me` -- read real values from DB
5. StoreKit consumable product for credit packs in the iOS app
6. UI in UpgradePromptView to show "Buy more hours" option

**Deduction priority when implemented:**
```
monthly_allocation --> overage_balance --> 429 fallback to on-device
```

---

## Key Files

| File | Project | Purpose |
|------|---------|---------|
| `config/tiers.yml` | GhostPour | Tier definitions, limits, features, StoreKit IDs |
| `config/features.yml` | GhostPour | Feature display metadata |
| `app/routers/chat.py` | GhostPour | verify-receipt, sync-subscription, usage/me, chat |
| `app/services/usage_tracker.py` | GhostPour | check_quota, record_cost, model access |
| `app/routers/webhooks.py` | GhostPour | Admin set-tier, simulate-tier |
| `app/models/user.py` | GhostPour | UserRecord with allocation fields |
| `app/database.py` | GhostPour | Users table schema + migrations |
| `SubscriptionManager.swift` | ShoulderSurf | StoreKit purchase, entitlement check, verify-receipt call |
| `CloudZapAuthManager.swift` | ShoulderSurf | JWT auth, fetchUsageConfig(), tier constraint properties |
| `CloudZapProvider.swift` | ShoulderSurf | Chat requests, response header parsing |
| `TierCatalog.swift` | ShoulderSurf | GET /v1/tiers, server-driven subscription UI |
| `UpgradePromptView.swift` | ShoulderSurf | Paywall UI, upgrade CTA, fallback option |
| `SessionManager.swift` | ShoulderSurf | 429 detection, fallback switching, settings locks |
