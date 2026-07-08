# Promo targeting + geo — design

Status: DRAFT for approval. Extends the promo decision engine
([gp-promo-decision-engine.md](./gp-promo-decision-engine.md)) so campaigns can
target by **language, app version, usage behavior, device, tier, and
geography**, authored in the dashboard and evaluated server-side at resolve
time. No client change required for the non-geo dimensions.

## 1. Goal

Let campaign management segment who sees a promo by:

- **Language** the app is running in
- **App version** (target new builds, or sunset old ones)
- **Usage behavior** (meetings recorded, recency / active vs dormant)
- **Device** family and **tier** / user type
- **Geography** — country, region, and city

Decisions stay entirely GP-side: the client reports signals (it already does),
GP decides, the client renders.

## 2. Data sources — what we already have

The telemetry ping (`POST /v1/events/ping`) already records the lifecycle Scott
described and most signals:

| Dimension | Signal | Source today | New work |
|---|---|---|---|
| Language | `app_locale` (e.g. `en_US`) | `telemetry_events.app_locale` | evaluate in resolve |
| App version | `app_version` (semver) | `telemetry_events.app_version` | semver matcher |
| Usage behavior | `app_start` / `meeting_start` / `meeting_stop` counts + recency | `telemetry_events.event_type` | aggregate per device/user |
| Device | `device_model` → family | `telemetry_events.device_model` + `to_marketing_name` | family-prefix matcher |
| Tier / user type | `users.tier` | `users` | **done** (MVP) |
| Signed-in | auth presence | resolve | **done** (MVP) |
| **Geo** | country / region / city | **not captured** | **new — see §4** |

Everything except geo is data we already collect; this is mostly "finish wiring
up the targeting the original design already specced."

## 3. Targeting schema (campaign `targeting` JSON, extended)

All fields optional. Absent = no constraint on that dimension. **AND** across
dimensions; **OR** within a list. Geo is a nested object.

```jsonc
"targeting": {
  "signed_in":         true | false | null,
  "tiers":             ["free", "plus", "pro"],     // signed-in only
  "users":             ["email-or-id"],             // signed-in only
  "locales":           ["en", "en_US"],             // prefix or exact match
  "app_version":       { "min": "1.4.0", "max": null }, // semver range, inclusive
  "meetings_recorded": { "min": 3, "max": null },   // usage band (lifetime count)
  "active_within_days": 7,                          // recency: last app_start <= N days
  "device_families":   ["iPhone16", "iPhone15"],    // marketing-name prefix
  "geo": {
    "countries": ["US", "CA"],        // ISO 3166-1 alpha-2
    "regions":   ["US-CA", "US-NY"],  // ISO 3166-2 subdivision
    "cities":    ["San Francisco"]    // exact, case-insensitive (sensitive — see §5)
  }
}
```

## 4. Geo collection

We honor `X-Forwarded-For`, so we see the raw client IP for a moment at
ingestion — today we SHA-256 it and discard it. The plan keeps that stance:

1. **At ingestion** (telemetry ping), GeoIP-resolve the raw IP → `country`,
   `region` (subdivision), `city`.
2. **Store the derived coarse location** as new nullable columns on
   `telemetry_events` (`country`, `region`, `city`). Continue hashing the IP.
   **Never store the raw IP.** No lat/long, no street.
3. **GeoIP source: MaxMind GeoLite2 City** DB on the box (free, self-contained,
   ~60 MB, refreshed periodically). No per-request external call. (Alternative:
   an edge geo header like Cloudflare `CF-IPCountry` — country-only, needs the
   app to sit behind such an edge; MaxMind gives region + city without it.)
4. **Profile at resolve:** the device's latest non-null country/region/city
   comes from telemetry, same enrichment the dashboard already does for
   device/locale. No client change.

## 5. Privacy review

City/region location is **personal data** under GDPR/CCPA and more sensitive
than country. Principles baked into the design:

- **No raw IP, ever** — we keep hashing it; we store only derived coarse fields.
- **Granularity is opt-in per layer:** store country + region by default; **city
  behind a config flag** so we can ship region targeting without city if the
  privacy review prefers. (Scott selected country + region/city; this flag lets
  us dial city off without code changes.)
- **Disclosure:** privacy policy gains a line that we derive approximate
  location from IP for analytics and to tailor in-app offers. Legitimate-
  interest basis + disclosure; revisit explicit consent / opt-out for EU.
- **Minimum-audience guard (anti-deanonymization):** a geo-targeted campaign
  whose matched audience is below a floor (e.g. < 25 devices) does not activate,
  so a city + tiny segment can't single someone out.
- **Retention:** geo columns follow telemetry retention; optionally truncate
  city → region after N days.
- **Operator visibility:** the dashboard shows derived country/region (city
  gated), never raw IP.

**Open privacy decisions (need sign-off):** (a) default city on or off; (b) EU
consent vs legitimate-interest + opt-out; (c) the minimum-audience floor value;
(d) city retention/truncation.

## 6. Resolve evaluation

At resolve, GP builds the **device profile** (locale, app_version, device
family, usage counts, recency, geo) from the latest telemetry + usage aggregate
for that `device_id`/`user_id`, then walks active campaigns in priority order
and returns the first whose `targeting` matches. Matchers:

- `locales`: exact or prefix (`en` matches `en_US`).
- `app_version`: semver compare against `min`/`max` inclusive.
- `meetings_recorded`: lifetime `meeting_start` count within `min`/`max`.
- `active_within_days`: latest `app_start` within N days.
- `device_families`: marketing-name prefix (`iPhone16` matches `iPhone 16 Pro Max`).
- `geo`: set membership on country / region / city.
- existing: `signed_in`, `tiers`, `users`.

Profile is cached briefly per device to keep resolve fast. (Long-term option:
the client sends locale + app_version on the resolve call for real-time vs
last-known — the original design's intent — but server-lookup needs no client
change for v1.)

## 7. Dashboard authoring

Replace the raw `targeting` JSON textarea with **structured fields**: signed-in
select, tiers multiselect, locales tags, version min/max, usage min/max +
recency, device families, and a geo block (country / region / city pickers).
Keep a raw-JSON "advanced" view. The funnel/activity view already shows
locale + device; it gains country/region once collected.

## 8. Phasing

- **Phase 1 — existing-data targeting** (no new collection, no client change):
  locale, app_version, usage band + recency, device family into the resolve
  engine; structured dashboard targeting fields. Buildable immediately.
- **Phase 2 — geo:** MaxMind ingestion + `country`/`region`/`city` columns +
  privacy-policy update + minimum-audience guard + geo targeting + dashboard geo
  fields. Gated on the §5 privacy sign-offs.
- **Phase 3 (optional):** client sends profile on resolve (real-time); A/B
  holdout analytics by segment.

## 9. Decisions — APPROVED (Scott, 2026-07-08)

1. Geo source: **MaxMind GeoLite2 City**. (Country/region via GeoLite2 was
   already live in telemetry by approval time — #354 shipped ingestion +
   dashboard breakdown — so this confirms the status quo and extends the
   same DB to city.)
2. Profile source: **server-side telemetry lookup** for v1. No client
   change for non-geo dimensions.
3. Privacy: **city targeting ON from day one**, minimum-audience floor of
   **25** enforced at both campaign authoring and resolve. Defaults unless
   Scott revises: EU basis = legitimate interest with privacy-policy
   disclosure (consistent with existing analytics posture); city retention
   follows the same policy as the existing country/region telemetry fields.
4. Build order: **Phase 1 all at once** — every dimension including geo
   evaluation ships in one build.
