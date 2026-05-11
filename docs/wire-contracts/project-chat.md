# Project Chat — wire contract

GP-controlled routing policy for Project Chat sends. Returned by the
`/v1/features/project-chat/check` preflight and re-resolved server-side
inside `/v1/chat` so a client can't skip the preflight to bypass routing.

Last updated: 2026-05-10.

> **Slice 5 (2026-05-10):** the count-quota described in earlier revisions
> of this doc is gone. `free_quota_per_month`, `feature_state.quota_*`
> fields, `send_to_gp_with_cta` verdict, and the `quota_remaining` /
> `quota_exhausted` / `unlimited` CTA kinds were removed. The budget gate
> (see `budget-gate.md`) is now the only Free-tier blocker — it emits
> `feature_state.credits_*` + a `budget_exhausted` CTA on the blocked
> envelope. This policy file is purely about routing.

## Concepts

- **`gp_chat_flag`** — server-controlled policy mode. Values:
  `"all"` | `"ssai"` | `"ssai_free_only"` | `"logged_in"` | `"plus"`.
  Stored in `feature_definitions.project_chat.gp_chat_flag` in the
  localized tiers config.
- **`selected_model`** — what model the user has picked client-side.
  Binary signal: `"ssai"` (SS AI) or `"external"` (anything else: BYOK,
  Apple FM, etc.).
- **Verdict** — one of: `send_to_gp`, `send_to_user_model`,
  `login_required`.

## Mode semantics

| `gp_chat_flag` | Behavior |
|---|---|
| `all` | Anyone (logged in or not). Routes by `selected_model`. |
| `logged_in` | Auth required. Routes by `selected_model`. |
| `ssai` | Auth required. SS AI overrides user model for all tiers. |
| `ssai_free_only` | Auth required. ssai semantics for Free; logged_in semantics for paid. **Day-1 default.** |
| `plus` | Auth required. Plus/Pro route by `selected_model`. Free always routes to GP. |

## Preflight contract

```
POST /v1/features/project-chat/check
Authorization: Bearer <jwt>   # optional — unauthenticated callers get login_required when applicable
Content-Type: application/json

{ "selected_model": "ssai" | "external" }
```

Response:

```json
{
  "verdict": "send_to_gp" | "send_to_user_model" | "login_required",
  "policy_mode": "<gp_chat_flag>",
  "cta": {
    "kind": "login_required",
    "text": "<localized>"
  }
}
```

`cta` is only present on `login_required`. `policy_mode` echoes the
resolved `gp_chat_flag` so iOS can confirm the active mode for telemetry.

## Server-side enforcement on `/v1/chat`

When `prompt_mode == "ProjectChat"`, /v1/chat re-resolves the verdict
with the same inputs the preflight saw. Outcomes:

- `send_to_gp` → proceed through the normal LLM path
- `send_to_user_model` → `422 {"code": "use_user_model"}` (client must
  route to its selected model)
- `login_required` → `401 {"code": "login_required"}` (defense in depth
  — `/v1/chat` already requires JWT)

Free-tier blocking happens after this resolver in the budget gate. A
Free user who's over their monthly spend cap gets the `budget_exhausted`
envelope from `app/services/budget_gate.py`, not from this resolver.
