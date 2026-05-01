# Budget gate — wire contract

GP-controlled pre-call cost estimate that blocks Free-tier `/v1/chat` and
`/v1/meetings/{id}/report` calls before any LLM tokens are spent. Replaces
the count-based Project Chat quota (still in code as of writing, deprecation
in a follow-up after a one-week soak).

Last updated: 2026-05-01.

## Concepts

- **Credits** — wire-facing unit. **1 cent = 100 credits** (1 USD = 10,000 credits).
  Free's $0.35 monthly cap surfaces as `credits_total: 3500`. Conversion is
  server-canonical; iOS never sees raw dollar amounts. The ratio can shift
  later without an app update.
- **Pre-call cost estimate** — `(input_tokens × input_price) + (max_output_tokens × output_price)`
  for the resolved model. `input_tokens` uses the same `(text.count + 3) / 4`
  heuristic iOS uses for the fuel gauge so the gate and gauge agree.
- **Overage tolerance** — `$0.05` (500 credits). A user can land *within* the
  band on a borderline call; the next call gets blocked.
- **Context cap** — `tier.max_input_tokens` per tier. Free 50K / Plus 150K /
  Pro 180K. Project Chat only — Free Form / Catch Me Up / etc. don't have
  this gate.

## Endpoints

### `POST /v1/chat`

Two new pre-call gates run after feature hooks (so the assembled prompt is
final) and before the stream/non-stream branch.

#### Context-cap gate (Project Chat only)

Fires when `prompt_mode = ProjectChat` AND `(len(system_prompt) + len(user_content)) / 4 > tier.max_input_tokens`.

```http
HTTP/1.1 413 Payload Too Large
Content-Type: application/json

{
  "detail": {
    "code": "context_too_large",
    "message": "Selected context is too large for your tier (78231 tokens, max 50000). Deselect meetings or drop transcript chips.",
    "feature_state": {
      "feature": "project_chat",
      "cta": {
        "kind": "context_too_large",
        "text": "Selected context is 78K tokens, over your 50K-token limit. Deselect meetings or drop transcripts to fit.",
        "action": "trim_context"
      },
      "details": {
        "max_tokens": 50000,
        "actual_tokens": 78231,
        "tokenizer": "chars_div_4"
      }
    }
  }
}
```

iOS already enforces this client-side via the fuel gauge before the user can
hit Send (per the `tiers.{tier}.feature_definitions.project_chat.max_input_tokens`
contract). This 413 path is defense-in-depth for races / hacked clients /
stale tier values. iOS routes `action: "trim_context"` to a deselect-meetings
hint, NOT the paywall.

#### Budget gate (all chat modes)

Fires when `monthly_used + estimated_cost > effective_limit + $0.05`. Returns
a 200 with empty text — same content-type as a normal chat reply, so SS's
content-type-driven parser handles it without a streaming codepath split.

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "text": "",
  "model": "claude-haiku-4-5-20251001",
  "provider": "anthropic",
  "ai_tier": "free",
  "feature_state": {
    "feature": "chat" | "project_chat",
    "credits_remaining": 100,
    "credits_total": 3500,
    "credits_resets_at": "2026-06-01T00:00:00Z",
    "cta": {
      "kind": "budget_exhausted",
      "text": "You've used your free AI for this month. Upgrade to Plus to keep going.",
      "action": "open_paywall"
    }
  }
}
```

iOS contract: render `feature_state.cta.text` as a CTA pill below the most
recent send (no empty assistant bubble). `feature_state` is ephemeral — do
not persist it in chat history.

#### Fail-open behavior

If model pricing isn't loaded (transient outage), `estimate_call_cost_usd`
returns `None` and the gate is skipped. The post-call `usage_tracker.check_quota`
backstop still catches retroactively. We'd rather fail open than blanket-block
on a transient pricing fetch failure.

### `POST /v1/meetings/{meeting_id}/report`

Same pre-call estimate. On overage, returns the **canned/sample report** verbatim
and persists it with `report_status = "placeholder_budget_blocked"` and
`is_editable = false` so iOS can disable the editor and surface a "Hide samples"
filter.

```json
{
  "report_html": "<the canned HTML with localized CTA banner>",
  "report_json": null,
  "report_status": "placeholder_budget_blocked",
  "is_editable": false,
  "meeting_id": "...",
  "ai_tier": null,
  "input_tokens": 0,
  "output_tokens": 0,
  "cost_usd": 0.0,
  "generation_ms": 0,
  "feature_state": {
    "feature": "meeting_report",
    "credits_remaining": 100,
    "credits_total": 3500,
    "credits_resets_at": "2026-06-01T00:00:00Z",
    "cta": {
      "kind": "report_blocked_budget_exhausted",
      "text": "You've used your free AI for this month. Upgrade to Plus to keep going.",
      "action": "open_paywall"
    }
  }
}
```

The HTML body comes from the `canned-report` remote config (English base,
`canned-report.es` / `canned-report.ja` for locale variants). CTA copy
substitutes into the HTML at response time + duplicates in
`feature_state.cta.text` for the SS-rendered pill.

After upgrade, iOS can re-POST the same `/v1/meetings/{id}/report` endpoint —
`INSERT OR REPLACE` semantics replace the canned row with a real generated
report.

### `POST /v1/verify-receipt`

Adds `placeholder_report_count` (always-present integer) to both response
paths so iOS can prompt regen for the most recent placeholder right after
upgrade without scanning the meeting list.

```json
{
  "status": "ok",
  "old_tier": "free",
  "new_tier": "plus",
  "is_trial": false,
  "monthly_limit_usd": -1,
  "allocation_resets_at": "...",
  "placeholder_report_count": 2
}
```

### `GET /v1/usage/me`

Adds a `credits` block alongside the existing `allocation.*` and `hours.*`
fields (left untouched for back-compat). iOS Account screen should bind to
`credits.{used,total,remaining,resets_at}` directly — no client-side
conversion, no drift between display and gate.

```json
{
  "credits": {
    "used": 3400,
    "total": 3500,
    "remaining": 100,
    "resets_at": "2026-06-01T00:00:00Z"
  }
}
```

Plus/Pro: `total = -1`, `remaining = -1` (unlimited badge).

## CTA kind / action table

Locked across all locales. Server keeps `kind` + `action` stable; only
`text` is localized. iOS branches on `kind`; for unknown kinds, falls back
to `action` for routing.

| `kind` | `action` | When |
|---|---|---|
| `quota_exhausted` | `open_paywall` | Legacy — Project Chat count quota. Deprecating with the budget gate. |
| `budget_exhausted` | `open_paywall` | `/v1/chat` blocked, monthly $ exceeded. |
| `report_blocked_budget_exhausted` | `open_paywall` | Meeting report blocked, canned response returned. |
| `context_too_large` | `trim_context` | `/v1/chat` 413, context exceeded `max_input_tokens`. **Routes to trim-scope hint, NOT paywall.** |
| `login_required` | `sign_in` | Existing. |
| `unlimited` | `null` | Informational pill, untappable. |
| `quota_remaining` | `null` | Informational pill, untappable. |

## Server config

| Setting | Source | Default |
|---|---|---|
| `monthly_cost_limit_usd` | `tiers.yml` per tier | Free $0.35, Plus/Pro -1 (unlimited) |
| `max_input_tokens` | `tiers.yml` per tier | Free 50K, Plus 150K, Pro 180K |
| Credit conversion | `app/services/budget_gate.py::CREDITS_PER_DOLLAR` | 10000 |
| Overage tolerance | `app/services/budget_gate.py::OVERAGE_TOLERANCE_USD` | 0.05 |
| Default max output tokens (when request omits) | `app/services/budget_gate.py::DEFAULT_MAX_OUTPUT_TOKENS` | 4096 |
| Canned report HTML + CTA | `config/remote/canned-report.json` (+ `.es`, `.ja`) | n/a |
| Meeting report chrome strings | `config/remote/report-strings.json` (+ `.es`, `.ja`) | n/a |

All of these are admin-editable via the dashboard's Configs tab (no app update
needed for SS). Server reads the cap per-tier; iOS reads `tiers.{tier}.feature_definitions.project_chat.max_input_tokens`
out of `tiers.json` at app start.

## Tokenizer

Both sides use `(text.count + 3) / 4` (Swift integer division, ASCII-style
char/4 approximation). Within ~10% of the real Anthropic BPE tokenizer at
worst, fine for an abuse guard. Documented as `tokenizer: "chars_div_4"` in
the 413 `details` payload.

## Test surfaces

- Unit: `tests/test_budget_gate.py` — credits conversion, char/4 heuristic,
  overage boundaries, fail-open behavior.
- Integration: `tests/integration/test_budget_gate_e2e.py` — chat block,
  413 context cap, canned report, no-cost-burn on block, per-tier
  differential.
- Locale: `tests/test_report_template_localization.py`, `tests/test_canned_report_localization.py`
  — chrome + canned CTA copy across en/es/ja, wire enums (kind, action)
  stable across locales.
