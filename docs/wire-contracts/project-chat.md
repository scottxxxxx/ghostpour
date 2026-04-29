# Project Chat — wire contract

GP-controlled policy for routing Project Chat sends. Replaces the PR #80
canned-upsell intercept with a richer per-request verdict.

Last updated: 2026-04-29.

## Concepts

- **`gp_chat_flag`** — server-controlled policy mode that decides routing.
  Values: `"all"` | `"ssai"` | `"ssai_free_only"` | `"logged_in"` | `"plus"`. Stored
  in the `feature_definitions.project_chat` block of the localized tiers config.
  See "Mode semantics" below.
- **`free_quota_per_month`** — integer cap on Free-tier CTA-wrapped sends
  per calendar month (UTC). Valid: `0` | `1..10` | `-1` (unlimited).
  Decrements only when GP processes a `send_to_gp_with_cta` outcome.
- **`selected_model`** — what model the user has picked client-side.
  Reported as a binary signal: `"ssai"` (user's picker is set to SS AI)
  or `"external"` (anything else: BYOK, Apple FM, etc.). The server
  doesn't need the specific external model.
- **Verdict** — what the iOS client should do for a single Project Chat
  send. One of: `send_to_gp`, `send_to_gp_with_cta`, `send_to_user_model`,
  `login_required`.
- **`feature_state`** — additive block on `/v1/chat` responses (when
  `prompt_mode=ProjectChat`) carrying CTA + quota metadata. Ephemeral —
  iOS does not persist it in chat history.

## State matrix

| Logged in | Subscription | Has quota | GP Chat Flag | Selected Model | Outcome |
|---|---|---|---|---|---|
| Yes | Free | Yes | `all` | SS AI | Send to GP |
| Yes | Free | No | `all` | SS AI | Send to GP |
| Yes | Plus | – | `all` | SS AI | Send to GP |
| Yes | Pro | – | `all` | SS AI | Send to GP |
| Yes | Free | Yes | `all` | Other | Send to user model |
| Yes | Free | No | `all` | Other | Send to user model |
| Yes | Plus | – | `all` | Other | Send to user model |
| Yes | Pro | – | `all` | Other | Send to user model |
| No | – | – | `all` | Other | Send to user model |
| Yes | Free | Yes | `ssai` | SS AI | Send to GP |
| Yes | Free | No | `ssai` | SS AI | Send to GP |
| Yes | Plus | – | `ssai` | SS AI | Send to GP |
| Yes | Pro | – | `ssai` | SS AI | Send to GP |
| Yes | Free | Yes | `ssai` | Other | Send to GP |
| Yes | Free | No | `ssai` | Other | **Send to GP with CTA** |
| Yes | Plus | – | `ssai` | Other | Send to GP |
| Yes | Pro | – | `ssai` | Other | Send to GP |
| No | – | – | `ssai` | Other | Login CTA |
| Yes | Free | Yes | `ssai_free_only` | SS AI | Send to GP |
| Yes | Free | No | `ssai_free_only` | SS AI | Send to GP |
| Yes | Plus | – | `ssai_free_only` | SS AI | Send to GP |
| Yes | Pro | – | `ssai_free_only` | SS AI | Send to GP |
| Yes | Free | Yes | `ssai_free_only` | Other | Send to GP |
| Yes | Free | No | `ssai_free_only` | Other | **Send to GP with CTA** |
| Yes | Plus | – | `ssai_free_only` | Other | Send to user model |
| Yes | Pro | – | `ssai_free_only` | Other | Send to user model |
| No | – | – | `ssai_free_only` | Other | Login CTA |
| Yes | Free | Yes | `logged_in` | SS AI | Send to GP |
| Yes | Free | No | `logged_in` | SS AI | Send to GP |
| Yes | Plus | – | `logged_in` | SS AI | Send to GP |
| Yes | Pro | – | `logged_in` | SS AI | Send to GP |
| Yes | Free | Yes | `logged_in` | Other | Send to user model |
| Yes | Free | No | `logged_in` | Other | Send to user model |
| Yes | Plus | – | `logged_in` | Other | Send to user model |
| Yes | Pro | – | `logged_in` | Other | Send to user model |
| No | – | – | `logged_in` | Other | Login CTA |
| Yes | Free | Yes | `plus` | SS AI | **Send to GP with CTA** |
| Yes | Free | No | `plus` | SS AI | **Send to GP with CTA** |
| Yes | Plus | – | `plus` | SS AI | Send to GP |
| Yes | Pro | – | `plus` | SS AI | Send to GP |
| Yes | Free | Yes | `plus` | Other | **Send to GP with CTA** |
| Yes | Free | No | `plus` | Other | **Send to GP with CTA** |
| Yes | Plus | – | `plus` | Other | Send to user model |
| Yes | Pro | – | `plus` | Other | Send to user model |
| No | – | – | `plus` | Other | Login CTA |

The `Has quota` column flips the CTA *kind*, not the verdict — when both
"Yes" and "No" rows show "Send to GP with CTA," the verdict is identical
but the rendered CTA copy differs.

## Mode semantics

| Mode | Login required | Free + SS AI | Free + external | Paid + SS AI | Paid + external | When to use |
|---|---|---|---|---|---|---|
| `all` | No | → GP | → user model | → GP | → user model | Open beta / no gates |
| `ssai` | Yes | → GP | → GP, CTA on no-quota | → GP | → GP (override) | Force everything through GP |
| `ssai_free_only` | Yes | → GP | → GP, CTA on no-quota | → GP | → user model (BYOK respected) | **Day-1 default**: metered conversion gate for Free, BYOK respect for paid |
| `logged_in` | Yes | → GP | → user model | → GP | → user model | Pure routing-by-choice with auth gate |
| `plus` | Yes | → GP, always CTA on Free | → GP, always CTA on Free | → GP | → user model | Strongest paid-feature framing |

Choose between `ssai` and `ssai_free_only` based on whether you want to
respect BYOK on paid tiers. They're identical for Free users — the
metered-CTA semantics on Free + external + no-quota are the same. The
only difference is whether Plus/Pro + external is overridden (`ssai`) or
respected (`ssai_free_only`).

## Endpoints

### `POST /v1/features/project-chat/check`

Read-only preflight. Call before each Project Chat send.

**Request:**
```http
POST /v1/features/project-chat/check
Authorization: Bearer <jwt>          # optional; omit if not signed in
Accept-Language: en | es | ja        # for localized cta.text
Content-Type: application/json

{ "selected_model": "ssai" | "external" }
```

**Response (200):**
```json
{
  "verdict": "send_to_gp" | "send_to_gp_with_cta" | "send_to_user_model" | "login_required",
  "policy_mode": "plus",
  "quota_remaining": 2,
  "quota_total": 3,
  "quota_resets_at": "2026-05-01T00:00:00Z",
  "cta": {
    "kind": "quota_remaining" | "quota_exhausted" | "unlimited" | "login_required",
    "text": "You have 2 of 3 free Project Chats remaining this month. Upgrade to Plus for unlimited."
  }
}
```

- `quota_*` and `cta` are present **only when applicable**. Plus/Pro user
  with `send_to_gp` verdict: only `verdict` + `policy_mode` returned.
- `cta.text` is pre-rendered server-side. Template variables (`{remaining}`,
  `{total}`) are filled in. Localized via `Accept-Language`.
- Calling this endpoint **does not** decrement quota.

### `POST /v1/chat` with `prompt_mode=ProjectChat`

Same shape as before, with two additions:

#### New error responses

| Status | Body | When |
|---|---|---|
| 401 | `{"detail": {"code": "login_required"}}` | Verdict resolved to `login_required`. |
| 422 | `{"detail": {"code": "use_user_model"}}` | Verdict resolved to `send_to_user_model`. SS should redirect to its own model. |

These fire when iOS skips the preflight or sends despite a verdict that
shouldn't have routed to GP. Defense in depth.

#### Additive `feature_state` block on success

```json
{
  "text": "Based on your meetings, the next steps are…",
  "input_tokens": 1234,
  "output_tokens": 567,
  "model": "anthropic/claude-haiku-4-5",
  "ai_tier": "standard",
  "provider": "anthropic",
  "usage": { ... },
  "cost": { ... },

  "feature_state": {
    "feature": "project_chat",
    "policy_mode": "plus",
    "quota_remaining": 1,
    "quota_total": 3,
    "quota_resets_at": "2026-05-01T00:00:00Z",
    "cta": {
      "kind": "quota_remaining",
      "text": "You have 1 of 3 free Project Chats remaining this month. Upgrade to Plus for unlimited."
    }
  }
}
```

**Rules:**
- `text` is pure AI response. Render in chat bubble. Persist in chat history.
- `feature_state` is **ephemeral**. Do not persist. Render `cta` UI at
  receive time only — pill, banner, modal, your choice.
- `feature_state.cta` is absent when no CTA applies (paid tier, etc.).
- `quota_*` fields are absent when not applicable (paid tier; unlimited quota).
- `feature_state` is always present for `prompt_mode=ProjectChat` responses,
  but may contain only `feature` + `policy_mode`.

#### Streaming

Project Chat is **forced non-streaming** server-side regardless of
`stream: true` in the request, so `feature_state` lands cleanly in a
single JSON body. If iOS sets stream=true for ProjectChat, the response
arrives as a normal JSON body, not SSE.

## SS integration logic

```
1. iOS user taps Send on Project Chat.
2. iOS calls POST /v1/features/project-chat/check with selected_model.
3. Branch on verdict:
     send_to_gp           → POST /v1/chat (prompt_mode=ProjectChat). Render text.
                            feature_state may be present but cta absent.
     send_to_gp_with_cta  → POST /v1/chat (prompt_mode=ProjectChat). Render text.
                            Render feature_state.cta UI separately (pill/banner).
     send_to_user_model   → Route the query to SS's active model picker. Skip GP.
     login_required       → Render feature_state.cta.text as inline login CTA.
                            Don't send.
4. Don't persist feature_state in chat history.
```

## Quota mechanics (server-side)

- Stored on `users` as two columns:
  - `project_chat_used_this_period INTEGER` — running counter.
  - `project_chat_period TEXT` — `"YYYY-MM"` UTC string of the active period.
- **Lazy reset:** every read/write checks if the stored period matches the
  current calendar month. If not, the counter is virtually 0; the new
  period is materialized on the next decrement (no cron job).
- **Decrement:** only when GP actually processes a `send_to_gp_with_cta`
  outcome for a Free user. Preflight never decrements. `send_to_user_model`
  outcomes never decrement (GP never sees the query).
- **Tier upgrade:** Free → Plus/Pro via `/v1/verify-receipt` zeros the
  counter and stamps the current period.

## Defaults

| Setting | Default |
|---|---|
| `gp_chat_flag` | `"ssai_free_only"` |
| `free_quota_per_month` | `1` |
| `cta_strings.quota_remaining` | `"You have {remaining} of {total} free Project Chats remaining this month. Upgrade to Plus for unlimited."` |
| `cta_strings.quota_exhausted` | `"You've used your {total} free Project Chats for this month. Upgrade to Plus for unlimited."` |
| `cta_strings.unlimited` | `"Project Chat by Shoulder Surf."` |
| `cta_strings.login_required` | `"Sign in to use Project Chat."` |

Spanish and Japanese variants live in `tiers.es.json` and `tiers.ja.json`.

## Multi-device race

The verdict is recomputed on every preflight and `/v1/chat` call (no
short-lived tokens). If a user sends Project Chats from two devices
simultaneously, both decrement the counter; whichever DB write lands
last is what stays. At small scale this is fine — the worst case is the
counter ends up off by one for one period, and it'll auto-correct on the
next month boundary.
