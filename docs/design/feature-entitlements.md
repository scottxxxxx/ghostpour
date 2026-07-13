# Feature entitlements — visibility, matrix, grants

Status: DRAFT for approval (SS ask, 2026-07-09). Nothing here touches the
documents rollout; this is about where the next features land.

## 1. What SS asked for

1. **Read-only dashboard view** of every client-facing flag: what it does,
   current served value, which tiers get it.
2. **An editable feature entitlement matrix**: rows = features, columns =
   tiers, cells = on / off / teaser — availability as data, not a decision
   baked into each feature's config shape.
3. **A per-user grant lane** generalizing documents' `allowed_users`: a
   per-user entitlement that overrides the tier default, so an IAP add-on
   purchase becomes a receipt that writes a grant, with zero client change.

## 2. What already exists (we are more than halfway)

- **The matrix is already data.** `config/features.yml` defines features
  (display name, description, teaser copy, CTA strings); `config/tiers.yml`
  assigns per-tier state with exactly the three cell values SS wants:
  `enabled` / `teaser` / `disabled`. The teaser state is a first-class
  runtime behavior (check runs, apply skipped, upgrade CTA returned).
- **The client already reads resolved entitlements**: the `features` dict
  on `/v1/usage/me`. SS keeps tier words out of the UI today.
- **The grant lane has a seed**: documents' `allowed_users` (per-identity
  passthrough override, shipped for e2e).
- What is NOT in the matrix: the newer config-shaped knobs — documents
  (`enabled`/`min_tier` in client-config), project chat char limits,
  web-search entitlement, model routing. Each grew its own shape.

## 3. Phases

### Phase 1 — read-only visibility (no behavior change, buildable now)

New admin endpoint + dashboard "Entitlements" section that aggregates, per
app:

- the features × tiers matrix (from features.yml × tiers.yml), with each
  feature's description and teaser copy;
- the config-shaped knobs outside the matrix (documents key incl.
  allowed_users, project chat char caps, search caps, max_images), with
  their live served values;
- where each value comes from (bundle vs overlay), reusing the drift
  machinery's view of truth.

Pure read. This answers "what does my app actually do right now" in one
place.

### Phase 2 — the editable matrix (single source of truth)

DECIDED 2026-07-13 (Scott): no fallback layer. The matrix lives in exactly
one place, the dashboard edits it, and server-side enforcement reads the
same object the app-facing config is served from. An earlier draft had
`entitlements.json` overriding tiers.yml per cell — rejected as split-brain
(a dashboard-touched cell would silently shadow later tiers.yml edits, and
enforcement would read a different home than the dashboard shows).

- New remote config `entitlements.json` per app (flat = ShoulderSurf,
  `techrehearsal/` = TR): the FULL `{feature: {tier: state}}` matrix,
  nothing else. tiers.yml keeps limits, pricing, display strings;
  features.yml keeps definitions and copy.
- In the same phase, tiers.yml's `features:` blocks and
  `TierDefinition.features` are REMOVED. No fallback, no second home. A
  missing cell resolves `disabled` (today's default); startup logs a
  completeness warning for any known feature × tier cell that's absent.
- One resolver — `entitlement_state(app_id, tier, feature)` — reads the
  live `app.state.remote_configs` entry: the same object the dashboard
  PUT hot-reloads on write and `/v1/config/entitlements` serves to apps.
  All current `tier.feature_state()` call sites (chat.py, cq_proxy.py,
  usage/me) route through it. A dashboard cell flip is therefore
  simultaneously the enforcement change and the served-config change,
  because they are the same read. Nothing reads tier feature state from
  boot-loaded YAML anymore.
- Write-path validation (the closed-enum lesson): the config PUT rejects
  unknown features, unknown tiers, and any state outside
  `enabled|teaser|disabled`. A malformed matrix never loads; the last
  good config stays live on a rejected write.
- The repo bundle `config/remote/entitlements.json` carries the initial
  matrix (copied from today's tiers.yml assignments) and seeds fresh
  deploys ONCE; after that the persistent, dashboard-owned file is the
  only truth. config_drift flagging repo-vs-live differences becomes
  "update the repo seed to mirror live" housekeeping, so a fresh
  deploy/DR restore seeds current reality, not launch-day's.
- Migration lands dark: first ship the resolver reading a seeded matrix
  bit-identical to current tiers.yml assignments (no behavior change),
  verify, then delete the YAML blocks.

Decision this bakes in: availability changes (documents moving Pro→Plus)
become a dashboard cell flip, no deploy, no client change.

### Phase 3 — per-user grants (the IAP lane)

- New table `user_entitlements(user_id, feature, state, source, granted_at,
  expires_at)` — source is `test` | `comp` | `iap`.
- Resolution order everywhere entitlements are read (usage/me, feature
  hooks, documents gate): **user grant beats tier matrix beats default**.
  This is not a second config home — grants are a different axis
  (per-user purchases/comps with receipts), and both layers resolve
  inside the same single resolver from Phase 2.
- IAP add-on purchase = verify-receipt writes a grant row. Client change:
  none — it already renders resolved entitlements.
- documents' `allowed_users` migrates into grants (source=test) AFTER the
  documents rollout completes; until then it stays exactly as shipped.
- Expiry semantics: grants may carry `expires_at` (subscription-style
  add-ons); a null expiry is permanent (lifetime purchase).

## 4. Non-goals

- No change to the documents launch shape mid-rollout.
- No client wire change in any phase.
- No per-app entitlement divergence beyond what X-App-ID scoping already
  provides (SS/TR stay autonomous apps).

## 5. Open decisions for approval

1. ~~Phase 2 home for editable state~~ — DECIDED 2026-07-13 (Scott):
   single source of truth. Full matrix in `entitlements.json`, tiers.yml
   `features:` blocks deleted in the same phase, enforcement and served
   config read the same live object. No fallback/override layering.
2. ~~Grant semantics~~ — DECIDED 2026-07-13 (Scott): grants are BINARY
   (enabled/disabled). Teaser stays a tier-level matrix state. Principle:
   targeted teasing is a campaign, not a grant — showing a teaser/CTA to
   a targeted user group (locale, usage hours, app starts) is the promo
   decision engine's job (targeting, frequency, priority, reporting);
   entitlements only answer "what may this user do." Marketing never
   writes entitlement rows.
3. Phase order confirmation: 1 (read-only) → 2 (matrix) → 3 (grants), with
   3 gated on getting serious about IAP add-ons.
4. Whether documents' min_tier folds into the matrix at phase 2 or stays
   config-shaped until phase 3 migrates allowed_users too.
