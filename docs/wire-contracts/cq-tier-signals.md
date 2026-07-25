# CQ tier signals — wire contract

GP forwards subscription tier on every CQ memory call and notifies CQ of
real subscription state transitions on a dedicated endpoint. Lets CQ
slice extraction metrics by tier and drive its own retention /
soft-delete policy without GP encoding it.

Last updated: 2026-05-01.

## What GP sends to CQ

### 1. `subscription_tier` in metadata (every call)

GP includes `subscription_tier` in the `metadata` object on every:

- `POST /v1/memory` (the `cq.capture()` payload — fired from `/v1/chat`'s
  after-LLM hook and from `/v1/capture-transcript`).
- `POST /v1/recall` (the `cq.recall()` payload — fired from `/v1/chat`'s
  before-LLM hook).

Value: the user's `effective_tier` — i.e. `simulated_tier` if an admin
is simulating, otherwise the real `tier` column. Possible values:
`"free"`, `"plus"`, `"pro"`, `"admin"`.

```jsonc
// POST /v1/memory body
{
  "user_id": "u-1234",
  "interaction_type": "meeting_transcript",
  "content": "...",
  "metadata": {
    "origin_id": "meeting-abc",
    "origin_type": "meeting",
    "subscription_tier": "free",
    "user_identified": true,
    "user_label": "Scott",
    "...": "..."
  }
}
```

```jsonc
// POST /v1/recall body
{
  "user_id": "u-1234",
  "text": "what did we say about Q2?",
  "metadata": {
    "project": "Q2 Planning",
    "subscription_tier": "pro",
    "locale": "en"
  }
}
```

If a request comes in before the user has any tier assigned (only
possible during account-creation race conditions), GP defaults to
`"free"`.

### 2. `POST /v1/users/{user_id}/tier-change` (transitions only)

Fire-and-forget, fired by GP **only on real subscription state
transitions** — not on idempotent re-verifications, not on renewals
into the same tier.

```jsonc
// POST /v1/users/u-1234/tier-change
{
  "old_tier": "free",
  "new_tier": "pro",
  "event_type": "upgrade",
  "occurred_at": "2026-05-01T18:42:13.482937+00:00"
}
```

#### `event_type` values

| Event | Fired by | Meaning |
|---|---|---|
| `upgrade`        | `/v1/verify-receipt`, `/v1/sync-subscription`, `/v1/apple-notifications` (SUBSCRIBED, DID_RENEW into a different tier) | User moved up the tier ladder (free→plus, plus→pro, free→pro) |
| `downgrade`      | `/v1/verify-receipt`, `/v1/sync-subscription` | Voluntary tier change down (pro→plus) |
| `trial_start`    | `/v1/verify-receipt` | User entered a free trial |
| `trial_to_paid`  | `/v1/verify-receipt`, `/v1/sync-subscription` | Trial converted to paid |
| `cancellation`   | `/v1/sync-subscription` (no `active_product_id`) | iOS-driven cancel reconcile |
| `expire`         | `/v1/apple-notifications` (EXPIRED, REVOKE, GRACE_PERIOD_EXPIRED) | Apple-side termination |
| `refund`         | `/v1/apple-notifications` (REFUND) | Apple-side refund |

Tier rank for upgrade-vs-downgrade classification:
`free=0, plus=1, pro=2, admin=3`. Movement to a higher rank ⇒ `upgrade`,
lower ⇒ `downgrade`. Equal ranks don't fire (idempotent re-verify).

#### Idempotency

CQ should treat `(user_id, occurred_at)` as the idempotency key. GP
generates `occurred_at` server-side via `datetime.now(timezone.utc).isoformat()`
at the moment of the transition. Duplicate notifications for the same
event won't have the same `occurred_at`, so this is best-effort dedup;
CQ should also be tolerant of double-firing (e.g., apply a tier
transition idempotently against current state).

#### Failure mode

GP fires via `asyncio.create_task` and never blocks the user's request.
A 4xx/5xx from CQ is logged as `cq_tier_change_error` and discarded —
no retry queue. If CQ misses a transition, it can re-derive state from
the next `subscription_tier` value seen on `/v1/memory` or `/v1/recall`.

#### When not configured

If `CZ_CQ_BASE_URL` is unset, the notify call no-ops (return early).
Same as `cq.capture()` and `cq.recall()`.

## What GP does NOT send

- **`previous_tier` on memory writes.** Considered and rejected — the
  dedicated transition endpoint is cleaner. CQ derives "what tier was
  this user at when this patch was created" from the transition log
  plus the `subscription_tier` stamp on the patch itself.
- **Account deletion events.** WIRED 2026-07-25 (App Review 5.1.1(v)):
  `POST /v1/account/delete` on GP purges all GP-side user data and
  fires the transition endpoint with `event_type: "account_deleted"`,
  `new_tier: "deleted"` (old_tier = the tier at deletion). This event
  is CQ's cue to purge everything it holds for the user; per the
  ownership split below, the purge itself is CQ policy, the signal is
  the request.
- **Calendar-month boundary defense.** GP's free quota uses calendar
  months (not rolling 30-day windows). A user could capture at 23:59
  Apr 30 and again at 00:01 May 1 within "their 1/month." Acceptable
  noise; CQ can detect abuse from the data once tier slicing is live.

## What CQ owns

GP intentionally does not encode retention policy. CQ decides:

1. **Retention of patches by tier.** Free-orphan, paid, trial, etc.
2. **Behavior on `tier-change` events.** E.g., soft-disable on
   downgrade, hard-delete on cancellation+90d, etc.
3. **Dashboard slicing of `extraction_metrics` by `subscription_tier`.**

If/when CQ's policy needs additional GP-side signal, file an ask and we
extend the metadata or the transition body.
