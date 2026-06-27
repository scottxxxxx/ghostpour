# GP promo: CTA native render slice

Status: foundation shipped; per-item work staged. Contract locked with SS 2026-06-27.

The three CTA enhancements SS proposed (personalization, usage targeting, promo
codes) all sit on the native render slice. GP **authors, serves, validates, and
gates** the native card; SS **renders** it and reports behavior back. The renderer
itself is SS's view and is out of scope here.

Framing (locked both sides): GP is the brains, the app is the view. GP decides who
sees what, when, and what it says, and communicates to the user; the app renders and
reports behavior + usage back.

## Native card schema (v1)

Served verbatim on a resolved variant when `render: "native"`:

```jsonc
{
  "variant_id": "A", "weight": 45, "render": "native",
  "min_app_version": "1.6.0",            // capability gate (optional)
  "native": {
    "schema_version": 1,                  // ==1; versioned + ADDITIVE, unknown fields ignored
    "title": "Hey {first_name},",         // required string; may carry the personalization token
    "body": "...",                        // optional string
    "media": { "type": "image", "url": "https://..." },  // optional; image only, https only
    "personalization": { "first_name_fallback": "there" }, // see item 1
    "ctas": [ { "cta_id": "...", "action": { "type": "...", "value": "..." } } ]
  }
}
```

Validated on campaign create/update (`_validate_campaign`): `render: native` requires
a `native` block with `schema_version == 1` and a string `title`; `body` is a string
when present; `media` is image + https when present. CTA `action.type` stays on the
locked allowlist (`appstore | storekit_offer | paywall | url | deeplink | none`),
deeplinks on the per-app allowlist, `cta_id` a string when present.

## Capability gate (foundation — shipped)

Per-variant `min_app_version`. In `resolve`, after the campaign is chosen, variants
the client can't render are dropped, then the weighted pick runs among the rest
(A/B preserved among capable clients). **Fail closed:** an unknown `app_version` (no
telemetry) can't be confirmed capable, so a gated variant is withheld. This is what
keeps a personalization-token card or a `storekit_offer` card off a build that can't
substitute the token or redeem the code; older builds fall through to an ungated
variant in the same campaign, or the campaign yields nothing. Reuses the device
profile's `app_version` + `_version_in_range`. The profile is now also built when a
campaign capability-gates a variant, not only when it profile-targets.

## Item 1 — personalization (locked, gate staged)

Single template + fallback. The creative carries `Hey {first_name},`; the campaign
declares `native.personalization.first_name_fallback` (default `"there"`). The client
substitutes `{first_name}` **on device for everyone, including signed-in users**, so
the name never reaches GP and the served creative is identical for all. Any
token-bearing variant must set `min_app_version` to the substitution-capable build
(enforced when this item lands). GP owns the wording and the fallback.

## Item 2 — usage targeting (locked, buildable now)

Derived server-side from the `app_start` + meeting pings the app already sends — no
new client signal. New dim: `sessions_this_week` (count of `app_start` in the last 7
days). `days_since_install` is available as days-since-first-seen, bounded by the ~30
day telemetry retention (acceptable to SS for now; a true install-age would come from
a client-sent install date later, not a retention change). Usage bands are
within-retention, which is the right signal for active/heavy-user targeting.

## Item 3 — promo codes (shape locked, gated on pool handoff)

`storekit_offer` action with the one-time Offer Code as `action.value`, redeemed
through Apple (no typing). Offer Codes, not signed Promotional Offers (parked for
win-back). **SS mints** the pool on their App Store Connect (seed 500) and hands GP
the pool over a secure channel; **GP distributes** — one code per device, tracked
against `promo_presentations`, and reports the issuance rate so SS tops up before the
pool runs dry. GP never holds SS's App Store Connect credentials. The distribution
machinery (pool store + per-device issuance + resolve-time injection) is buildable
now; going live is gated only on the pool handoff.

## Build order

1. Foundation — native schema validation + `min_app_version` capability gate. **Shipped.**
2. Item 2 usage dims (`sessions_this_week`, bounded `days_since_install`). No client dep.
3. Item 1 personalization token contract + token-requires-gate enforcement + dashboard.
4. Item 3 Offer Code pool store + distribution + `storekit_offer` value validation. Go-live on pool handoff.

Open from SS: the `min_app_version` value(s) for the renderer build(s); the Offer Code pool.
