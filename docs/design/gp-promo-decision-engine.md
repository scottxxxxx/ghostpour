# GP design: promo decision engine + campaign config

Status: design for the SS sync. Pairs with `ShoulderSurf/docs/GP-promo-interstitial-proposal.md`.
The client contract is settled; this is GP's half. Author: GP side.

## The split

**GP is the brains.** GP owns everything that decides what a device sees and how often:
- Campaign management and targeting on **language, app version, usage, and device**.
- Variant assignment.
- **Frequency.** GP keeps a presentations table of what was shown to which device and when, and GP
  decides how many times (and how often) to present. Frequency is enforced server side, at decision
  time. The client does not track or cap anything.
- The payload to render.

**SS is the view.** The app renders what GP returns and reports back: impression (it was shown),
dismiss (optionally with time-to-dismiss), and click (if there's a link). That's it. No frequency
state, no enforcement, no decision logic on the client.

v1 reaches the whole install base via the app_start ping, not just signed-in users.

## Carriers (two, by audience)

1. **Primary — `app_start` ping response (`POST /v1/events/ping`).** Unauthenticated, fires on every
   cold launch, anchored on `device_id`, carries `X-App-ID` + device/locale/version (+ usage signals
   like `meetings_recorded`). Today ingest-only; we make it return the session's promo decision.
   Reaches everyone (signed in, BYOK, on-device). **One decision per session**: GP returns the
   eligible promos for the session, each tagged with `placement`; the client shows each at its moment.
2. **Enrichment — `/v1/usage/me`.** Authenticated, signed-in only. Carries the within-session
   reactive, tier-targeted cases the launch ping can't (e.g. a post-session "you hit your cap"
   upsell needing live tier + usage). Optional; rides next to the existing `features` map.

`promo_enabled` in the `features` map is the global kill switch (default on, fail open).

## What GP sends the client (lean — no frequency rules)

Because GP enforces frequency, the client only receives what it needs to render and report. No
`max_impressions`, no intervals — those never leave GP.

```jsonc
// in the ping response
{
  "promos": [                      // empty / absent => show nothing
    {
      "campaign_id": "tr_crosspromo_2026_07",   // echoed back in events
      "variant_id": "B",                        // GP-assigned; echoed back
      "placement": "launch",                    // launch | review_tab | post_session | manual
      "render": "native",                       // native | html
      "native": {                               // when render=native
        "schema_version": 1,                    // versioned + ADDITIVE; unknown fields ignored
        "title": "...", "body": "...",
        "media": { "type": "image", "url": "https://cdn/.../hero.png" },
        "ctas": [ { "label": "Try Tech Rehearsal", "action": {"type":"appstore","value":"id000"} } ],
        "style": { /* optional layout/style hints */ }
      },
      "html_url": "https://cdn/.../promo_b.html" // when render=html (first-class escape hatch)
    }
  ]
}
```

`cta.action.type`: `appstore | url | deeplink | storekit_offer | none`. Native schema is versioned and
strictly additive (forward-compatible, same as the iOS config decoder). `html` render serves a
`html_url`; inline HTML is not used.

## The decision engine (server side)

On a ping (and on usage/me for the enrichment case):

0. **Global short-circuit.** `promo_enabled` off or no active campaign for this app => return `{}`
   immediately. No table reads. Keeps the launch path free in the common case.
1. **Resolve the device profile:** `device_id`, `app_id`, language, app version, usage signals
   (`meetings_recorded`, recency), device, `is_signed_in` + tier when signed in.
2. **Filter to eligible campaigns** for this `app_id`: active window, targeting predicate matches the
   profile on language / version / usage / device, placement supported.
3. **Apply frequency from the presentations table** (the brain): for each candidate, read this
   device's history for the campaign and drop it if it's hit `max_impressions`, is inside
   `min_interval` since last shown, or inside `cooldown_after_dismiss`, or the user already converted.
   This is the server-side gate, not a client hint.
4. **Resolve conflicts per placement:** at most one promo per placement, highest `priority` wins;
   mutual-exclusion groups so two campaigns never fight for the same moment.
5. **Assign variant** deterministically: `hash(device_id + campaign_id) -> bucket(0..99)` mapped to
   the campaign's split from config (sticky; holdout = a "show nothing" variant).
6. **Emit** the per-placement promo list, and **record the intent to present** (so closely-spaced
   sessions don't double-serve before an impression event lands — see state).

## GP state (the real build — two tables)

GP holds two things: the **campaign store** (authored, dashboard-managed) and the **presentations
table** (runtime, event-written). The "per-device profile" is not a third table — targeting signals
come off the pings, presentation history comes from the events.

### Campaign store schema (the authored object)

Dashboard-managed, same pattern as remote configs, so campaigns ship without an eng deploy.
**Only `variants[].render` and the placement enum cross the wire to SS** — everything in `targeting`,
`frequency`, and `schedule` stays GP-internal and can change freely without touching their app.

```jsonc
{
  "campaign_id": "tr_crosspromo_2026_07",
  "name": "Cross-promote Tech Rehearsal to active SS free users",
  "status": "active",              // draft | active | paused | archived
  "app_id": "shouldersurf",        // which app this runs IN (X-App-ID it targets)
  "schedule": { "starts_at": "2026-07-01T00:00:00Z", "expires_at": "2026-08-01T00:00:00Z" },

  // GP-INTERNAL: targeting. All fields optional; absent = no constraint; AND-ed together.
  "targeting": {
    "locales":            ["en", "en_US", "en_GB"],  // device locale, prefix or exact
    "app_version":        { "min": "1.4.0", "max": null },   // semver range
    "meetings_recorded":  { "min": 3, "max": null }, // usage band
    "days_since_install": { "min": 2, "max": null },
    "devices":            null,                        // e.g. ["iPhone15","iPhone16"] family prefixes
    "tiers":              ["free"],                    // applied only when signed in
    "signed_in":          null                         // true | false | null
  },

  // GP-INTERNAL: frequency. Enforced from the presentations table at decision time.
  "frequency": {
    "max_impressions": 3,
    "min_interval_seconds": 172800,        // >= 2 days between shows
    "cooldown_after_dismiss_seconds": 604800,
    "stop_after_convert": true
  },

  "placements": [ { "placement": "launch", "priority": 10 } ],  // higher priority wins a moment
  "mutual_exclusion_group": "crosspromo",  // optional: campaigns in a group never co-occur

  // SS-FACING CONTRACT: variants. weights sum to 100. GP picks one, returns its render block.
  "variants": [
    {
      "variant_id": "A", "weight": 45, "render": "native",
      "native": {
        "schema_version": 1,
        "title": "Prepping for an interview?",
        "body": "Tech Rehearsal runs mock interviews with you.",
        "media": { "type": "image", "url": "https://cdn.shouldersurf.com/promo/tr_a.png" },
        "ctas": [ { "label": "Get Tech Rehearsal", "action": { "type": "appstore", "value": "id000000000" } } ],
        "style": { "accent": "#5B8DEF" }     // optional hints
      }
    },
    { "variant_id": "B", "weight": 45, "render": "html", "html_url": "https://cdn.shouldersurf.com/promo/tr_b.html" },
    { "variant_id": "control", "weight": 10, "render": "none" }   // holdout
  ]
}
```

Field notes:
- **Identity/schedule** — `status=active` + within `schedule` + global `promo_enabled` = eligible.
- **Targeting** — every field optional (absent = no constraint), present fields AND together. This is
  the "language / version / usage / device" management, entirely GP-side. `tiers` only applies to
  signed-in users (rides the usage/me enrichment path). `app_version` min/max targets new builds
  ("what's new") or old builds ("upgrade").
- **Frequency** — read against the presentations table; drop the campaign if `max_impressions` hit,
  inside `min_interval`/`cooldown_after_dismiss`, or `stop_after_convert` and converted.
- **Placements** — at most one promo per placement, highest `priority` wins; `mutual_exclusion_group`
  stops related campaigns stacking.
- **Variants** — the only SS-rendered block; `render` is `native | html | none` (none = holdout),
  weights sum to 100. The `native` schema is versioned + additive (unknown fields ignored).

### Presentations table (runtime)

```sql
CREATE TABLE promo_presentations (
  device_id          TEXT NOT NULL,
  campaign_id        TEXT NOT NULL,
  variant_id         TEXT,
  app_id             TEXT,
  shown_count        INTEGER NOT NULL DEFAULT 0,
  first_shown_at     TEXT,
  last_shown_at      TEXT,        -- drives min_interval
  last_dismissed_at  TEXT,        -- drives cooldown_after_dismiss
  last_clicked_at    TEXT,
  converted_at       TEXT,        -- drives stop_after_convert
  PRIMARY KEY (device_id, campaign_id)
);
CREATE INDEX idx_promo_pres_campaign ON promo_presentations(campaign_id);
```

Written from the client's events: `promo_impression` upserts `shown_count += 1` +
`first/last_shown_at` + `variant_id`; `promo_dismiss` sets `last_dismissed_at`; `promo_click` sets
`last_clicked_at`; `promo_convert` sets `converted_at`. The decision engine reads this for frequency.

Accuracy: frequency counts from confirmed **impression events**, so they reflect what was actually
shown, not just what was returned (the client may suppress a promo mid-meeting). To avoid
double-serving when a device launches twice before its impression lands, step 6 can stamp a
short-lived "served" marker that the impression event reconciles.

## Events (the client's only job besides rendering)

On `POST /v1/events/ping`, tagged with `campaign_id`, `variant_id`, `device_id`, timestamp, `app_id`:
- `promo_impression` — it was shown. **Drives the presentations table** (frequency) and the funnel.
- `promo_dismiss` — optionally with time-to-dismiss, for measurement.
- `promo_click` — when there's a link/CTA, for measurement.
- `promo_convert` — subscribed / installed the cross-promoted app, when detectable.

GP records these and surfaces a per-campaign, per-variant funnel (impression -> click -> convert) with
holdout lift on the dashboard.

## Phasing (mirrors SS Phase 1/2/3)

- **Phase 1 (MVP):** usage signal on the ping; one campaign; native render at one placement; GP
  frequency (max_impressions + min_interval) from the presentations table; `promo_impression` +
  `promo_dismiss`; global `promo_enabled` kill. GP: campaign store (single campaign, dashboard) +
  profile-from-pings + presentations table + ping decision path + short-circuit.
- **Phase 2:** A/B variants + holdout; CTA routing; html_url render; usage/me enrichment for signed-in
  reactive cases; convert + click events.
- **Phase 3:** multiple concurrent campaigns with priority + mutual exclusion; richer targeting on the
  full signal set; per-app cross-promo (SS<->TR).

## Open GP-side questions for the sync

- Campaign authoring: dashboard-managed (rec) vs code/deploy.
- Profile + presentations freshness: live read per ping vs a small cache, given the ping is high
  volume. The presentations write is event-driven so it's naturally async.
- Confirm the exact usage/device/version signals on the ping + a privacy review before they ride the
  unauthenticated endpoint.
- How expressive the native `style`/layout hints are in v1 (start small, grow additively).
