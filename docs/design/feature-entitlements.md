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

### Phase 2 — the editable matrix

Move per-tier feature STATE out of repo YAML into the remote-config
overlay pattern so the dashboard can edit it:

- New remote config `entitlements.json`: `{feature: {tier: state}}` only —
  the matrix, nothing else. tiers.yml keeps limits, pricing, display
  strings; features.yml keeps definitions and copy.
- Loader resolves state as `entitlements.json` value, falling back to the
  current tiers.yml assignment — so shipping the config empty changes
  nothing, and each cell becomes dashboard-editable when first touched.
- Existing sync-from-bundle / config_drift machinery applies unchanged.
- Dashboard renders the matrix editable (three-state cells), writing
  through the existing config PUT.

Decision this bakes in: availability changes (documents moving Pro→Plus)
become a dashboard cell flip, no deploy, no client change.

### Phase 3 — per-user grants (the IAP lane)

- New table `user_entitlements(user_id, feature, state, source, granted_at,
  expires_at)` — source is `test` | `comp` | `iap`.
- Resolution order everywhere entitlements are read (usage/me, feature
  hooks, documents gate): **user grant beats tier matrix beats default**.
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

1. Phase 2 home for editable state: new `entitlements.json` remote config
   (recommended — smallest blast radius, reuses overlay/drift machinery)
   vs making tiers.yml itself dashboard-editable.
2. Grant semantics: is `teaser` grantable per user, or are grants
   binary enabled/disabled? (Recommended: binary — teaser is a marketing
   state, not an entitlement.)
3. Phase order confirmation: 1 (read-only) → 2 (matrix) → 3 (grants), with
   3 gated on getting serious about IAP add-ons.
4. Whether documents' min_tier folds into the matrix at phase 2 or stays
   config-shaped until phase 3 migrates allowed_users too.
