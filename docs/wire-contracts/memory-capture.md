# Memory capture — wire contract

GP-controlled gating + CTA injection for end-of-meeting Memory captures.
Intentionally invisible to SS — the wire shape on `/v1/capture-transcript`
and `/v1/quilt/{user_id}` is unchanged. GP just decides what to do.

Last updated: 2026-04-30.

## Concepts

- **`feature_state`** — the user's tier-resolved state for `context_quilt`:
  - `enabled`  → Pro: full capture, no CTA.
  - `teaser`   → Plus: existing recall-only chat hook continues to recall;
    capture-transcript becomes a no-op (no extraction, no CTA).
  - `disabled` → Free: gated by quota.
- **`free_quota_per_month`** — Free tier's monthly cap on
  `capture-transcript` calls that produce real Memory. Decrements only on
  `capture_with_cta` outcomes. Calendar-month, UTC, lazy reset (mirrors
  `project_chat_quota`). Lives on `features.yml` ▸ `context_quilt` block.
  Default: `1`.
- **Verdict** — what `/v1/capture-transcript` does for one call. One of
  `capture`, `capture_with_cta`, `skip_with_cta`, `recall_only`.
- **Synthetic CTA card** — a fake CQ patch GP appends to the next
  `/v1/quilt/{user_id}` response. iOS' meeting-end view filters by
  `metadata.origin_id`, so the card only appears in *that* meeting's
  view. Cleared after one render.

## Verdict matrix

| Tier | feature_state | has_quota | Verdict | Side effects |
|---|---|---|---|---|
| Pro    | `enabled`  | (any) | `capture`           | `cq.capture()` fires. No CTA stamped. |
| Plus   | `teaser`   | (any) | `recall_only`       | No capture. No CTA stamped. |
| Free   | `disabled` | True  | `capture_with_cta`  | `cq.capture()` fires. Quota -1. CTA stamped (`free_within_quota_footer`). |
| Free   | `disabled` | False | `skip_with_cta`     | No capture. CTA stamped (`free_no_quota_only`). |

The local `meeting_transcripts` write is **always** performed regardless of
verdict — `meeting_reports` is independent of `context_quilt` gating.

## Wire surfaces (unchanged for SS)

### `POST /v1/capture-transcript`
Request and response shapes are identical to today. SS does not need to
inspect the response — it's still `{"status": "queued"}`.

### `GET /v1/quilt/{user_id}`
Response is CQ's native shape — passes through unmodified except for the
synthetic CTA injection:

```json
{
  "user_id": "...",
  "facts": [...],
  "action_items": [...],
  "deleted": [...],
  "server_time": "..."
}
```

When a CTA is pending, GP appends a synthetic fact to the `facts` array
with this shape (mirrors a real fact + adds a `metadata` detection bag):

```json
{
  "patch_id": "cta:<cta_kind>:<origin_id>",
  "fact": "<rendered CTA copy>",
  "category": "cta",
  "patch_type": "cta",
  "source": "synthetic",
  "created_at": "<iso8601 utc>",
  "origin_id": "<the meeting that triggered the CTA>",
  "origin_type": "meeting",
  "participants": [],
  "owner": null,
  "deadline": null,
  "project": null,
  "project_id": null,
  "permanence_override": null,
  "permanence_override_source": null,
  "connections": [],
  "metadata": {
    "is_synthetic": true,
    "cta_kind": "free_within_quota_footer" | "free_no_quota_only",
    "action": "open_paywall"
  }
}
```

iOS detects the upsell card via `metadata.is_synthetic == true` and
routes taps via `metadata.action`. Real CQ facts have no `metadata`
field, so the check is unambiguous.

The flag clears after one fetch — refreshing the view does not
re-surface the card.

### CTA copy

Templates live in `config/features.yml ▸ context_quilt ▸ cta_strings`.
Template variables: `{remaining}`, `{total}` (only `{total}` is used in
v1 since both variants are about exhausted/low quota).

| `cta_kind` | Default English copy |
|---|---|
| `free_within_quota_footer` | "✨ This was your free Memory of {total} this month — Upgrade to Pro to keep building Memory." |
| `free_no_quota_only` | "✨ Want your AI to remember meetings? Upgrade to Pro to start building Memory." |

iOS detects the upsell card via `metadata.is_synthetic == true` and/or
`metadata.action == "open_paywall"`. iOS may render the card identically
to a real Memory (highest visual integration) or apply a subtle
treatment — GP is agnostic.

## Localization (deferred to v2)

V1 reads `cta_strings` only from `features.yml` (English source of truth).
A follow-up PR will mirror the strings into the locale-specific
`feature_definitions.context_quilt` blocks in `config/remote/tiers.es.json`
and `config/remote/tiers.ja.json` and add a locale-aware loader on the
proxy interceptor.

## Quota counter behavior

- Stored in `users.memory_used_this_period` + `users.memory_period`
  (`YYYY-MM` UTC).
- **Lazy reset**: any read that sees a stale `memory_period` returns
  `used = 0`. The fresh period is materialized atomically on the next
  decrement.
- **Tier upgrade**: Free → Plus/Pro via `/v1/verify-receipt` zeros the
  counter and stamps the current period so the new subscriber doesn't
  ghost-decrement on first virtual reset (mirrors Project Chat).
- **No cron job** — same model as Project Chat quota.

## Test plan (server)

- Unit: `tests/test_memory_capture_policy.py` — verdict matrix.
- Unit: `tests/test_memory_capture_quota.py` — period rollover, exhaustion,
  unlimited, null-period.
- Integration: `tests/integration/test_memory_capture_gating.py` — Pro
  unconditional capture, Free within-quota fires + decrements + stamps,
  Free over-quota skips capture but stamps, quilt fetch injects + clears,
  Pro quilt fetch is pass-through.

## Test plan (TestFlight, manual)

- Free user, first meeting of the month: capture fires → quilt view shows
  real Memories *plus* the within-quota footer card. Refresh: card gone,
  Memories remain.
- Free user, second meeting same month: capture skipped → quilt view shows
  only the no-quota CTA card. Tapping the card opens the paywall.
- Pro user: no CTA card on quilt fetch. Memories appear as today.
- Plus user: no Memories produced from end-of-meeting capture (recall on
  chat path still works).
- Cross-month: roll the device clock forward → next capture acts as if
  quota is fresh.
