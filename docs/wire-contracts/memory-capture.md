# Memory capture — wire contract

GP-controlled gating for end-of-meeting Memory captures. Intentionally
invisible to SS: the wire shape on `/v1/capture-transcript` and
`/v1/quilt/{user_id}` is unchanged. GP just decides what to do.

Last updated: 2026-08-11 (synthetic CTA card retired, see below).

## Retired: the synthetic CTA card (2026-08-11)

Earlier versions of this contract had GP append a synthetic fact-shaped
upsell card (`category: "cta"`, `metadata.is_synthetic`) to the next
`/v1/quilt/{user_id}` fetch for free users. SS's decode audit
(contextquilt-ops docs/cross-team/
2026-08-11-SS-confirmations-and-a-decode-finding.md) showed the card
had NEVER rendered on any build: SS's PatchType is a closed enum, and a
patch with an unknown `patch_type` fails item decode and is dropped,
logged, before any rendering or filtering code runs. Scott's decision:
the free-tier Memory upsell rides the existing gate/teaser lane with
served copy, no synthetic objects in data arrays, and SS builds no
`cta` patch type. GP therefore stamps nothing per meeting and injects
nothing; `GET /v1/quilt/{user_id}` is a pure passthrough of CQ's body.
The `users.memory_last_origin_id` / `memory_last_cta_kind` columns
remain in the schema (migration history) but are written by nothing.

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
  `capture`, `capture_with_cta`, `skip_with_cta`, `recall_only`. The
  `cta_kind` on a verdict names the free-Memory copy state; since the
  card retired it drives no server-side write.

## Verdict matrix

| Tier | feature_state | has_quota | Verdict | Side effects |
|---|---|---|---|---|
| Pro    | `enabled`  | (any) | `capture`           | `cq.capture()` fires. |
| Plus   | `teaser`   | (any) | `recall_only`       | No capture. |
| Free   | `disabled` | True  | `capture_with_cta`  | `cq.capture()` fires. Quota -1. |
| Free   | `disabled` | False | `skip_with_cta`     | No capture. |

The local `meeting_transcripts` write is **always** performed regardless of
verdict — `meeting_reports` is independent of `context_quilt` gating.

## Wire surfaces (unchanged for SS)

### `POST /v1/capture-transcript`
Request and response shapes are identical to today. SS does not need to
inspect the response — it's still `{"status": "queued"}`.

### `GET /v1/quilt/{user_id}`
Pure passthrough of CQ's native shape:

```json
{
  "user_id": "...",
  "facts": [...],
  "action_items": [...],
  "deleted": [...],
  "server_time": "..."
}
```

Nothing is appended, mutated, or cleared on this route.

### Upsell copy (gate/teaser lane)

The free-Memory upsell strings are served, not baked: they live in
`feature_definitions.context_quilt.cta_strings` in `config/remote/
tiers.json` and its `.es` / `.fr` / `.ja` locale files (with
`config/features.yml` as the compiled English fallback), alongside
`upgrade_cta` and `teaser_description`. The client renders them through
the existing gate/teaser lane, which already fires impressions
(promo_events, the 2026-08-04 feature-gate CTA telemetry). Two copy
states exist and keep their names:

| `cta_kind` | Meaning |
|---|---|
| `free_within_quota_footer` | Free user, this month's free capture just ran |
| `free_no_quota_only` | Free user over quota; capture skipped |

Copy changes are config-only and never need a GP code change or an SS
build (doc 17's rule applied to gate copy: strings that describe what a
plan withholds stay served).

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
  unconditional capture, Free within-quota fires + decrements (no
  stamp), Free over-quota skips capture (no stamp), quilt fetch is a
  pure passthrough even for a user carrying a legacy pre-retirement
  stamp.

## Test plan (TestFlight, manual)

- Free user, first meeting of the month: capture fires → quilt view shows
  real Memories; the gate/teaser lane surfaces the within-quota copy.
- Free user, second meeting same month: capture skipped → no new
  Memories; the gate/teaser lane surfaces the no-quota copy, and its CTA
  opens the paywall.
- Pro user: Memories appear as today; no upsell surfaces.
- Plus user: no Memories produced from end-of-meeting capture (recall on
  chat path still works).
- Cross-month: roll the device clock forward → next capture acts as if
  quota is fresh.
