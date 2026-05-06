# Web search caps — wire contract

GP-side per-tier caps on Anthropic web search. Explicit user opt-in via
`metadata.search_enabled: true` on `/v1/chat`. Server-side gate evaluates
tier + monthly cap pre-LLM, decides whether the search tool is attached
to the upstream Anthropic call, and emits a structured CTA payload iOS
renders inline.

Last updated: 2026-05-05.

## Concepts

- **`metadata.search_enabled`** — boolean flag iOS sends on `/v1/chat`
  when the user has explicitly opted into web search for this query.
  Absent or `false` means no search; gate doesn't fire.
- **Hard cap** — `searches_per_month` on the user's tier. At hard cap,
  the gate strips `search_enabled` before the LLM call (so the adapter
  doesn't attach the tool) and returns a CTA. Query still runs, just
  without search results.
- **Soft cap** — `searches_soft_threshold` (Pro tier today; Plus is
  null). Past the soft threshold, search still runs, but a gentler
  warning CTA surfaces so the user isn't surprised when they hit the
  hard cap later.
- **`allocation_resets_at`** — same per-user 30-day rolling cycle the
  cost-allocation reset uses. For Plus/Pro, anchored to Apple's
  `expiresDate` (so the cycle stays aligned with billing). For Free,
  locally computed as `now + 1 calendar month` from account creation.
- **`search_state`** — additive block on `/v1/chat` responses (both JSON
  and SSE `done` event) carrying counter + CTA metadata when
  `search_enabled=true` was on the request. Ephemeral; iOS does not
  persist it.
- **`feature_state`** — Free-reject envelope (mirrors the existing
  `budget_exhausted` shape). `text` is empty; `cta_only: true` signals
  iOS to dispatch on the flag rather than branching on `text === ""`.

## Tier matrix

| Tier | Hard cap | Soft cap | At hard cap | Past soft cap |
|---|---|---|---|---|
| Free | 0 | — | reject before LLM call (paywall modal CTA) | n/a |
| Plus | 75/mo | none | run query *without* search + alert CTA | n/a |
| Pro | 120/mo | 80 | run query *without* search + alert CTA | run query *with* search + banner CTA |

Caps are admin-tunable from the GP dashboard via
`PUT /admin/tunable/tier-field` with `feature: "search"` and
`field: "searches_per_month"` or `"searches_soft_threshold"`. Changes
land in `tiers.{en,es,ja}.json` in lockstep.

## Server-side guard: provider

Anthropic's `web_search_20250305` is the only provider-side mechanism
GP wires today; OpenAI / Gemini / Generic adapters silently ignore the
flag. The chat-router gate strips `search_enabled` immediately when
`request.provider != "anthropic"` so the counter doesn't increment
against a search that physically can't run. iOS-side enforcement
(disable the search toggle when SS AI not selected) is the primary
layer; this is the server-side backstop.

## Outcomes

### 1. Free user with `search_enabled=true` → reject before LLM

Mirrors the `budget_exhausted` envelope. No LLM call, no cost.

```json
{
  "text": "",
  "model": "claude-haiku-4-5-20251001",
  "provider": "anthropic",
  "ai_tier": "free",
  "cta_only": true,
  "feature_state": {
    "feature": "search",
    "cta": {
      "kind": "search_paywall_required",
      "header_icon": "globe",
      "title": "Web Search",
      "body": "Pull fresh information from the web into your queries...",
      "bullets_label": "TRY IT FOR",
      "bullets": [
        {"icon": "newspaper.fill", "label": "Latest news on a competitor or topic in your meeting"},
        {"icon": "doc.text.magnifyingglass", "label": "Recent docs for a library or framework you're discussing"},
        {"icon": "chart.line.uptrend.xyaxis", "label": "Current pricing, availability, or product changes"}
      ],
      "footer": "Web search is available with a Plus or Pro subscription, when using SS AI as your model.",
      "primary_action": {"label": "Upgrade", "action": "open_paywall"},
      "secondary_action": {"label": "Maybe later", "action": "dismiss"}
    }
  }
}
```

### 2. Plus or Pro past hard cap → query runs without search

Gate strips `search_enabled` before the LLM call so the adapter doesn't
attach the tool. Response includes the LLM output normally plus a
`search_state` sidecar with the hard-cap CTA.

```json
{
  "text": "<assistant response>",
  "model": "claude-haiku-4-5-20251001",
  "provider": "anthropic",
  "ai_tier": "standard",
  "search_state": {
    "used": 75,
    "total": 75,
    "soft_threshold": null,
    "resets_at": "2026-06-15T20:12:33+00:00",
    "was_used": false,
    "cta": {
      "kind": "search_cap_exhausted",
      "title": "Web search limit reached",
      "body": "You've used all 75 searches this month. Upgrade to Pro for higher limits.",
      "primary_action": {"label": "Upgrade", "action": "open_paywall"},
      "secondary_action": {"label": "OK", "action": "dismiss"}
    }
  }
}
```

### 3. Pro past soft cap → query runs WITH search + warning

Search still fires; counter increments; sidecar carries a banner CTA
with no buttons.

```json
{
  "text": "<assistant response>",
  "search_state": {
    "used": 85,
    "total": 120,
    "soft_threshold": 80,
    "resets_at": "2026-06-15T20:12:33+00:00",
    "was_used": true,
    "cta": {
      "kind": "search_soft_cap_warning",
      "title": "Approaching your monthly search limit",
      "body": "You've used 85 of 120 searches this month."
    }
  }
}
```

### 4. Under all caps → counter only

```json
{
  "text": "<assistant response>",
  "search_state": {
    "used": 23,
    "total": 75,
    "soft_threshold": null,
    "resets_at": "2026-06-15T20:12:33+00:00",
    "was_used": true,
    "cta": null
  }
}
```

## SSE streaming parity

Streaming responses (Meeting Chat, freeform Response) carry
`search_state` in the final SSE `done` event with the same payload
shape:

```json
{
  "type": "done",
  "input_tokens": 100,
  "output_tokens": 50,
  "cost": {...},
  "usage": {...},
  "ai_tier": "standard",
  "search_state": {...},
  "allocation_percent": 87.5
}
```

Field is omitted entirely when the request had no `search_enabled`.
Post-stream counter increment + `search_usage` audit row insert mirror
the non-streaming path.

## Three CTA kinds → three iOS layouts

iOS branches on `cta.kind` to pick layout, rendering only the fields
that exist:

| `kind` | Layout | Fields populated |
|---|---|---|
| `search_paywall_required` | full-screen modal (Free) | `header_icon`, `title`, `body`, `bullets_label`, `bullets[]`, `footer`, `primary_action`, `secondary_action` |
| `search_cap_exhausted` | toast/alert with buttons (Plus/Pro hard) | `title`, `body`, `primary_action`, `secondary_action` (optional) |
| `search_soft_cap_warning` | silent banner (Pro soft) | `title`, `body` |

`primary_action.action` values: `open_paywall`, `dismiss`, `none`.
`dismiss` closes the CTA without further action.

## Template variables

Server substitutes `{used}` and `{total}` in CTA `title` and `body`
fields before sending. **`{reset_date}` is intentionally NOT
substituted** — locale-aware date formatting (en-US "Jun 15" vs es
"15 jun" vs ja "6月15日") needs to happen on iOS via `DateFormatter`
+ `Locale.current`. iOS swaps `{reset_date}` client-side at render time
using the raw ISO from `search_state.resets_at`.

## `was_used` signal

Every populated `search_state` carries `was_used: bool`. True when the
upstream Anthropic response reported `web_search_requests > 0`; false
when the gate stripped the flag (hard cap path) or when no searches
were performed (under-cap path). iOS uses this to render a "based on
web search" attribution on the response bubble — branching on
`was_used` rather than inferring from CTA presence avoids false
positives when search runs cleanly under all caps.

## Pre-search counter visibility

`GET /v1/usage/me` returns a `search` block so iOS can render a
"23 of 75 used this month" pill near the search toggle without firing
a search-bearing request first:

```json
{
  ...,
  "search": {
    "used": 23,
    "total": 75,
    "soft_threshold": null,
    "resets_at": "2026-06-15T20:12:33+00:00"
  }
}
```

`total: 0` indicates the tier has no search at all (Free) — iOS uses
this to decide whether to show the search toggle as disabled or to
route the tap into the upgrade flow.

## Audit trail

Every search-bearing response writes a row to the `search_usage`
table:

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT | UUID |
| `user_id` | TEXT | FK → users |
| `request_timestamp` | TEXT | ISO 8601 UTC |
| `meeting_id` | TEXT | nullable |
| `provider` | TEXT | always "anthropic" today |
| `model` | TEXT | model used |
| `searches_count` | INTEGER | how many searches in this turn |
| `search_cost_usd` | REAL | flat $0.01/search Anthropic fee |
| `usage_log_id` | TEXT | FK → usage_log (the response row) |

Visible per-user via `GET /admin/user/{id}/search-usage` (admin-only).
Used for offline reconciliation if the rolling counter ever drifts.

## Failure modes

- **DB increment fails after Anthropic returns search results**:
  fail-open. The user gets the search this turn; counter doesn't
  advance. Logged as a warning. Reconcile offline from `search_usage`
  audit rows if the drift becomes material.
- **`{reset_date}` substitution missing on iOS**: the literal
  `{reset_date}` placeholder will appear in the body. Better than
  shipping a raw ISO timestamp into a localized UI string.
- **Old iOS build sends legacy `body["web_search"]` instead of
  `metadata.search_enabled`**: the legacy field was never honored,
  so the request silently runs without search. No CTA, no counter
  movement.
