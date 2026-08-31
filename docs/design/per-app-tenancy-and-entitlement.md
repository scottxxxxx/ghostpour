# Per App Tenancy: Identity, Entitlement and Budget

Status: Draft proposal
Author: (you)
Date: 2026-08-31

## Summary

Every app shipped under the Weirtech Apple developer account shares one
`users` row per person, permanently. Apple issues the Sign in with Apple
subject identifier per developer TEAM rather than per bundle, and
`users.apple_sub` is `UNIQUE NOT NULL`, so one human is one row across
ShoulderSurf, Tech Rehearsal, the N-400 helper and every app after them.
This is not a decision we get to make and it does not change with app
count.

The consequence is structural: **the account can never be the tenant
boundary. `app_id` has to be.** Most of that boundary is already built
and works. Three pieces are not, and they are the three that decide
whether "these apps are separate" is true in the way it is meant.

1. **Entitlement is shared.** `users.tier` is one value, so a
   ShoulderSurf Pro subscriber arrives at any new app already Pro.
2. **Money is shared.** `monthly_used_usd` and `overage_balance_usd` are
   one bucket, so one app's spend consumes another's allowance.
3. **The boundary is self asserted.** `X-App-ID` is read from the request
   header on every call and never compared against the `app_id` we
   already stamp on the session at sign in, so it is advisory rather
   than enforced.

This proposes generalizing the Tech Rehearsal budget gate into a
registry driven per app gate, giving each app its own entitlement axis,
and deciding deliberately what `X-App-ID` is worth as a trust signal.

## Why now

The N-400 helper is app four, and Scott has stated that every app he
builds will use the same Apple developer account. So this stops being a
Tech Rehearsal quirk and becomes the permanent shape of the system.

The forcing function is `app/services/tr_budget.py`. It solves leak 2
correctly and it is app specific by construction: `_APP_ID =
"techrehearsal"` at module scope, the `X-TR-Entitlement` header name
baked into its call sites, and Tech Rehearsal's name inside the fallback
user facing copy. It is the right mechanism wearing one app's clothes.
Copying it for the N-400 helper is how a fourth copy arrives, and the
cost of generalizing it is lowest now, at two callers, rather than at
four.

There is also a launch hazard with a deadline attached. Config
resolution deliberately FAILS OPEN: an unregistered `X-App-ID` resolves
to ShoulderSurf and serves ShoulderSurf's config with nothing but a
logged warning (`app/routers/config.py` `resolve_app_dir`, and the
comment block at the top of `config/apps.yml` says so outright). That is
correct for ShoulderSurf's months of field builds and exactly wrong for a
new tenant: the N-400 helper's first TestFlight, if it ships before the
registry entry, runs on ShoulderSurf's answers and nothing errors.

## Goals

- One tenant boundary, `app_id`, applied consistently to entitlement and
  spend as it already is to config and telemetry.
- Adding app five is a registry entry plus a decision, not a new module.
- A change made for one app cannot silently change another's behavior,
  and where it can, something fails loudly.
- Tech Rehearsal's live behavior does not change.

## Non goals

- Separating the `users` row. Not possible under one developer team, and
  pursuing it would mean abandoning Sign in with Apple or shipping under
  separate developer accounts, both of which cost more than they buy.
- App Store subscription state at Apple. Already deliberately untouched
  by account deletion and out of scope here.
- Reworking ShoulderSurf's own tier model. Its defaults stay the account
  level defaults.

## What is already true

Grounded, because the useful half of this document is the part that says
what does NOT need building.

- **Identity on the wire.** `X-App-ID` is read by
  `app/middleware/request_logging.py:256` into `request.state.app_id`.
- **A registry.** `config/apps.yml` maps app id to a config `dir`,
  `label`, `bundle_id` and optional `cq` identity, `budget` and
  `tier_overrides` blocks. `default_app: shouldersurf`.
- **Per app client config.** `GET /v1/config/{name}` resolves through
  `resolve_app_dir(app_id)` (Phase B, #249), with flat fallback.
- **Per app model routing.** `model-routing.json` keys `apps.<app_id>`.
- **Per app tier overrides.** `tier_overrides_for_app`
  (`app/routers/config.py:100`), applied at `app/routers/chat.py:1091`
  and `:1398` and `app/routers/webhooks.py:1708`. These REPLACE the
  account level value for one app without editing `tiers.yml`.
- **Per app ContextQuilt identity.** `apps.<id>.cq.app_id` plus a
  secret setting, resolved in `context_quilt.py _cq_identity()`.
- **Per app version floor.** `bundle_id` into `config/app-versions.yml`
  via `version_gate.py`, failing open when it cannot resolve.
- **Per app attribution.** `usage_log.app_id` and
  `telemetry_events.app_id`, both indexed (`app/database.py:341-344`).
- **A membership ledger.** `user_apps (user_id, app_id, first_seen_at,
  last_seen_at)` with an index on `app_id`. This is the closest thing we
  have to a tenancy table and it already exists.
- **Session attribution.** `refresh_tokens.app_id` and
  `search_usage.app_id`, both added for scoped deletion.
- **Per app dashboard.** Every admin endpoint takes an optional `app`
  query param; `tests/test_dashboard_app_filter.py` pins the filter
  across dashboard totals, users, errors, user detail, telemetry and
  media metrics.
- **Per app account deletion.** `app/services/account_deletion.py`
  sorts every user keyed table into `APP_SCOPED_TABLES` (deleted for the
  deleting app only), `APP_OWNED_TABLES` (a domain belonging to exactly
  one app), and `ACCOUNT_TABLES` (properties of the person, surviving
  until the last app goes). **A schema pinning test fails CI until a
  newly added user keyed table is classified.** That is already the
  "tell me when a change affects the other tenant" guarantee, on one
  axis, and it is the model the rest of this should follow.

  Verified rather than quoted: removing `ad_attribution` from
  `APP_SCOPED_TABLES` fails `test_every_user_keyed_table_is_classified`
  (`tests/test_account_deletion.py:382`) and fails nothing else. Which
  also names the instrument's LIMIT precisely, and the migration plan
  below depends on knowing it: the pin catches a table nobody
  classified, and catches nothing about a table classified into the
  WRONG bucket. Moving a name from `APP_SCOPED_TABLES` to
  `ACCOUNT_TABLES` would keep the suite green while changing what
  survives a delete. So step 4 of the migration is "CI will stop you
  forgetting", never "CI will check your answer".

## The three leaks

### 1. Entitlement is shared

`users.tier` is a single value on the shared row. A ShoulderSurf Pro
subscriber signing into any other app is Pro there on arrival, with
ShoulderSurf's `monthly_cost_limit_usd`.

Tech Rehearsal already answered this the right way and the answer should
be generalized rather than admired: TR's free and paid plan is a TR side
entitlement sent per call as `X-TR-Entitlement: free|paid`, deliberately
independent of the SS tier. `tier_overrides` covers the adjacent case,
where an app wants a different value for an account level knob.

### 2. Money is shared

`monthly_used_usd` and `overage_balance_usd` live on the same shared
row, so the ShoulderSurf budget gate cannot be reused per app: spending
in one app would draw down another's allowance.

`tr_budget.py` solves this by ignoring the shared bucket entirely and
summing `usage_log.estimated_cost_usd` filtered on `user_id` and
`app_id` for the current UTC month, against an entitlement keyed cap
read from `apps.yml`. It is dormant until `budget.enabled` is true, it
fails open in every ambiguous case, and it serves its own explanatory
copy rather than letting the client invent a reason. The design is
right. Only its scope is wrong.

### 3. The boundary is self asserted

This is the one worth deciding rather than inheriting.

`X-App-ID` arrives as a plain request header and is read fresh on every
request. We already record the caller's app on the session at sign in:
`app/routers/auth.py` stamps `app_id` on the `refresh_tokens` row and
records membership in `user_apps`, and the docstring explains why. **We
never compare the two.** Nothing reads the session's app and checks it
against the header.

So a client holding a valid token can present itself as any registered
app and receive that app's config, model routing, tier overrides and
budget treatment. Today the stakes are low, because the differences are
mostly config. Once per app budget caps are real, the incentive becomes
concrete: claim whichever app has the loosest cap. "Completely separate"
would then be true of our storage and untrue of our enforcement.

## Proposal

### A. Generalize the budget gate

Rename `tr_budget.py` to `app_budget.py` and parameterize the three app
specific things:

- `_APP_ID` becomes an `app_id` argument threaded from
  `request.state.app_id`.
- `tr_budget_config(registry)` becomes `budget_config(registry, app_id)`,
  reading `apps.<app_id>.budget`, which is already the shape on disk.
- The entitlement header name moves into the registry as
  `apps.<id>.entitlement_header`. Tech Rehearsal keeps
  `X-TR-Entitlement` verbatim so no TR build changes; new apps declare
  their own, and the recommended default for anything new is
  `X-App-Entitlement`.
- `_FALLBACK_EXHAUSTED` currently names Tech Rehearsal in its text. The
  fallback becomes app neutral, with the app specific sentence coming
  from the served `<app_dir>/budget` config document, which is already
  how the non fallback path works.

Everything else about the module carries over unchanged, including the
fail open behavior and the `OVERAGE_TOLERANCE_USD` alignment with the
ShoulderSurf gate.

The two existing call sites (`app/routers/chat.py:2086` and
`app/routers/reports.py:281`) pass `techrehearsal` explicitly, so Tech
Rehearsal's behavior is byte for byte what it is today. That claim needs
proving by sabotage, not by inspection: mutate the app id threading and
confirm the TR budget tests go red, and confirm they go red for the
reason expected rather than merely going red.

### B. Give each app its own entitlement axis

Registry gains, per app, an explicit statement of where entitlement
comes from. Three honest options, and each app picks one:

- `account` — the app rides `users.tier`. Correct for ShoulderSurf, which
  IS the account level subscription.
- `header` — the app sends its own entitlement per call, with
  `entitlement_header` naming it. Tech Rehearsal today.
- `none` — the app has no paid tier yet, and the budget block, if
  enabled, applies one flat cap.

Making this explicit matters more than the mechanism. An app that
silently inherits `account` today is inheriting a decision nobody made.

### C. Decide what `X-App-ID` is worth

Three options, in increasing strictness.

1. **Leave it advisory.** Cheapest. Acceptable only while no per app cap
   is worth gaming.
2. **Warn only, then measure.** Compare the request header against the
   session's `refresh_tokens.app_id` and log a counter on mismatch
   without changing behavior. Run it for a period, look at the number,
   then decide with data instead of with intuition. A mismatch is also
   expected legitimately at least once, when a header-less older build
   holds a session stamped with an app, so the measurement has to
   distinguish "absent" from "different" before anyone reads a rate off
   it.
3. **Enforce.** Reject or downgrade a request whose header disagrees with
   its session.

**Recommendation: 2, then 3.** Enforcing directly is a client facing flip
against builds already in the field, and we do not currently know the
mismatch rate. Going straight to enforcement is the shape of change that
looks safe, ships, and produces a support load nobody can attribute.

### D. Make registration a gate, not a hope

Config resolution should keep failing open, because ShoulderSurf's field
builds depend on it. But an unrecognized `X-App-ID` currently produces
only a log line. It should also increment a counter that is visible in
the dashboard, so "a new app is live and unregistered" is something we
SEE rather than something we later reconstruct. The registry comment
already tells the next person to register before launch; a metric is what
makes that instruction enforceable.

## Migration

1. Register `n400` (final id to be chosen) in `config/apps.yml` with its
   `dir`, `label`, `bundle_id`, entitlement source and, if wanted, a
   dormant `budget` block. **Before the app's first TestFlight**, per the
   fail open hazard above.
2. Create its config directory so resolution stops falling back.
3. Rename and parameterize the budget module, TR call sites passing
   `techrehearsal`. No TR behavior change, proven by sabotage.
4. Classify the N-400 helper's user keyed tables in
   `account_deletion.py`. The CI schema pin will fail until this is done,
   which is the intended behavior and the reason it exists.
5. Land the `X-App-ID` mismatch counter in warn only mode. Read it before
   proposing enforcement.

Steps 1 and 2 are the launch blockers. The rest can follow.

## What this does not solve

The `users` row stays shared, so anything genuinely account level stays
account level: the person's identity, their App Store subscription, their
welcome letter, their subscription history. That is correct rather than
merely tolerable. A subscription belongs to a person, not to an app.

It also does not make the apps invisible to each other in the dashboard,
and should not. Seeing one operator view across all tenants, filterable
to one, is the stated requirement.

## Open decisions

- **The N-400 helper's entitlement source.** `account`, `header`, or
  `none`. This is a product call, not an engineering one, and everything
  in section A and B waits on it.
- **Whether to bind `X-App-ID` to the session**, and if so whether to
  take the warn only step first. Recommended above, but it is a
  client facing change and therefore Scott's.
- **Budget numbers for the new app**, if it gets a cap at all.
- **The app id string itself**, which is permanent once a build ships
  with it.
