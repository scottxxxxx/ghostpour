# Memory policy per tier: two dials, no assumed combinations

Scott, 2026-08-26 (via CQ), in spirit: "be flexible and forward and
backward compatible so that if we decide later memory works for all
tiers, or that we only give Free the last 15 days of memories, GP
controls that decision and it is visible to me and configurable in the
dashboard."

Every tier's memory policy is exactly two served dials. No code path
assumes a combination; every combination works by configuration alone.

## Dial A: mode

`entitlements.matrix.context_quilt.<tier>` = `disabled | teaser | enabled`.
Dashboard: Entitlements tab, the features × tiers matrix (live). Resolver:
`app/services/entitlements.entitlement_state`.

- `enabled`: full recall on every chat turn.
- `teaser`: the People-scoped recall lane (what the user's own People
  screens show) plus CQ's tier-blind teaser payload; `excluded.by_scope`
  counts what full recall would have added.
- `disabled`: the People-scoped lane only (People launches at full value
  on every tier); no teaser payload.

## Dial B: window

`tiers.<tier>.feature_definitions.context_quilt.recall_max_age_days` =
integer days, or `null`. Absent == `null` == unlimited. Dashboard: Tiers
tab, "Memory recall window (days)" per tier (live, mirror to the repo).
Resolver: `app/services/recall_window.recall_max_age_days`.

Applied on the SAME dial, independent of mode:
- to CQ as `metadata.max_age_days` on every recall leg the hook fires
  (full, teaser, People-scoped, dossier), for any tier;
- to the project chat meeting blocks server-side
  (`clamp_meeting_blocks`, sliding from today, inclusive at N);
- to served copy: `{recall_window_days}` in a tier's own strings reads
  that tier's dial; shared strings (feature_definitions, cta_strings,
  paywall) read the lowest tier whose mode is `enabled`, the upgrade
  target. An unset dial renders "recent" and logs
  `recall_window_copy_unfilled`.

## Nudges read the dials, never tier names

`upgrade_nudges.memory_excluded_cta`:
- `by_scope` (CQ found matches a not-enabled tier's lane could not use)
  sells the lowest higher tier whose mode is `enabled`.
- `by_window` (older than the tier's N days) sells the lowest higher tier
  whose window is wider or unlimited, and says that tier's window.
- No such tier: silence. Zero or absent counts: silence.

Copy placeholders (served `tiers.upgrade_nudges`, all locales):
`{excluded} {plural} {window} {tier_name} {next_tier} {next_window}`;
`next_window_none` / `next_window_days` ("{n}") give the localized window
phrase. Display names come from `tiers.<tier>.display_name`.

## Combinations, by configuration alone

| free mode | free window | plus | pro | what happens |
|---|---|---|---|---|
| teaser | absent | enabled 30 | enabled null | today: Free People-scoped + teaser, Plus 30 days sliding, Pro everything; Free→Plus on by_scope, Plus→Pro on by_window |
| enabled | 15 | enabled null | enabled null | Free gets 15 days of memory AND meeting content; by_window nudges Free→Plus ("Plus has no window") |
| teaser | 15 | enabled 30 | enabled null | teaser names what it skipped inside 15 days; by_scope still sells Plus |
| enabled | null | enabled null | enabled null | memory for all tiers, no window anywhere, no memory nudge can fire |
| enabled | null | enabled 30 | enabled null | Plus narrower than Free is allowed; by_window still sells Pro |
