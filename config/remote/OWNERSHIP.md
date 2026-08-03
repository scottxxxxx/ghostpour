# Who owns what in this directory

Two paths can write these files: this repo bundle, and the admin dashboard
(or `PUT /webhooks/admin/config/{slug}`, which is the same path). Only one
wins at runtime, and until 2026-08-03 nothing said which.

## The rule

**The repo owns the content. Production is where it has to be pushed.**

The first draft of this file said the opposite, that the dashboard owned
user-facing copy, because production and the bundle disagreed on free-tier
marketing text and the obvious reading was that someone had edited it
live. That was wrong, and the test suite caught it.

Production was not ahead. It was **stale**. Both drifted fields were
deliberate, tested repo decisions that had never reached users:

- Free-tier copy quantified in meeting-hours rather than credits (Scott,
  2026-07-29: a credit balance carries no scale, so it cannot inform a
  purchase decision). Production still said "a small free credit".
- `model-routing` gained an `automation` tier per app. Production still
  had only free, plus and pro.

Both were merged. Neither was live. That is the actual failure mode here,
and it is not about ownership at all.

## Why merging is not deploying, for config

`seed_remote_configs` copies a bundled file into the persistent directory
only when it is **absent**, and never overwrites. That is deliberate: PR
#109 bumped `tiers.json` in the repo on 2026-05-01 and silently erased a
dashboard-added icon.

The consequence is a trap:

- **Adding a key propagates.** Hydration folds missing keys into the
  overlay on boot and bumps the version.
- **Changing a value does not.** The key already exists, so hydration
  leaves it alone and `config_drift` logs a warning nobody reads.

So a merged change to any existing string, number, or array sits in the
repo indefinitely while production serves the old one. Two decisions sat
that way for weeks.

**To ship a value change, push it deliberately** through
`PUT /webhooks/admin/config/{slug}`, which writes and hot-reloads, then
verify against production. Editing this directory alone does nothing to a
running server.

## Checking

`detect_overlay_drift()` reports every field where the bundle and the
overlay disagree, and it runs at boot. Treat a non-empty result as a list
of changes that have not shipped, not as noise. As of 2026-08-03 it is
down to one entry, which is a pending push rather than a mystery.

## Before you change any served config

Read `docs/decisions/prompt-composition-doctrine.md`. Every build in the
field decodes these payloads whole-file, so removing, renaming or
retyping a field discards the entire file on that client, and builds
older than 2026-08-02 cannot report that it happened. Adding is always
safe. `tests/test_served_config_shape.py` enforces what can be enforced,
and `tests/test_tier_copy_hours_framing.py` is the reason this correction
got caught rather than shipped.
