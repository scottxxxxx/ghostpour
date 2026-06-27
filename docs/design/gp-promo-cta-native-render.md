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

## Item 3 — promo codes (shape locked, GP owns minting)

A `storekit_offer` CTA carries a one-time Subscription Offer Code as `action.value`,
redeemed through Apple's own sheet (`presentOfferCodeRedeemSheet` / redemption URL),
never a custom coupon field. Offer Codes — not signed Promotional Offers (parked for
win-back), not Introductory Offers (broad public first-time discounts).

**Ownership (corrected 2026-06-27): GP owns minting AND distribution; the app only
redeems.** SS and the apps live under one weirtech Apple account we control, so
there is no cross-party handoff. The app's whole role here is to present Apple's
redeem sheet for the code GP hands it (plus the on-device `{first_name}` substitution
from Item 1). Everything else is GP.

GP mints programmatically through the App Store Connect API:
`POST /v1/subscriptionOfferCodeOneTimeUseCodes` creates a batch (`numberOfCodes`,
`expirationDate`), then `GET /v1/subscriptionOfferCodeOneTimeUseCodes/{id}/values`
pulls the code strings back as CSV — no manual ASC download. Apple limits: ≤150,000
codes per quarter per app, codes expire ≤6 months from creation.

The only piece Apple forces into App Store Connect is a **one-time** setup, not an
ongoing task: the Subscription Offer Code *configuration* on our auto-renewable
subscription (eligibility, discount, duration). After that, batch generation,
retrieval, issuance, and reporting are fully GP-driven.

**Eligibility caveat:** Apple "New subscribers" eligibility means *never subscribed*.
A previously-subscribed, now-lapsed, now-free user is rejected at the sheet. So the
recipient targeting for these codes must be "never subscribed," not merely "currently
on the free tier." Lapsed-now-free win-back is a separate path (Promotional Offers),
out of scope here.

**Entitlement:** redemption happens entirely Apple-side, so GP learns of it via App
Store Server Notifications (or receipt validation) and reflects the upgraded tier
server-side. This is **not** optional — GP gates tier and budget server-side, so ASSN
is how the upgrade and the issuance→redemption funnel close.

### Build surface

- **Credential.** A scoped App Store Connect API key (issuer id + key id + `.p8`),
  least-privilege role, in Secret Manager (same posture as other secrets; supersedes
  the old "GP never holds ASC credentials" assumption, which only fit a cross-party
  framing). The key + the offer config are the sole human setup step.
- **ASC mint client** (`app/services/offer_codes.py`): JWT-signed ASC API client;
  `mint_batch(count, expires_at)` → POST batch, then GET `/values`, returns code
  strings. Idempotent per batch id; never re-pulls a consumed batch.
- **Pool store** (table `promo_offer_codes`): `code` (unique), `batch_id`,
  `expires_at`, `state` (available|issued|redeemed|expired), `device_id`,
  `issued_at`, `redeemed_at`. A low-water-mark check triggers a refill mint.
- **Per-device atomic issuance.** At resolve, a `storekit_offer` variant claims one
  `available` non-expired code in a single transaction (`UPDATE ... WHERE
  state='available' ... LIMIT 1 RETURNING code`), so no code issues twice under
  concurrency. Re-presenting to the same device returns its already-issued code, not
  a new one (idempotent per device, tracked against `promo_presentations`).
- **Resolve-time injection.** `resolve` fills the chosen variant's
  `storekit_offer.action.value` with the claimed bare code; the app builds the
  prefilled redeem URL / presents the sheet. Pool exhaustion → withhold the variant
  (fail closed, same as the capability gate), never serve an empty offer.
- **`storekit_offer` value validation.** In `_validate_campaign`, an authored
  `storekit_offer` declares the offer/product reference, not a literal code (GP
  injects the code at resolve); reject a hand-authored raw code.
- **ASSN webhook** (`/webhooks/appstore/notifications`): on redeem, mark the code
  `redeemed`, flip the user's tier, and feed the redemption funnel.
- **Reporting.** Issuance + redemption rates per campaign in the dashboard funnel;
  low-pool alert via the existing alert categories so we refill before it runs dry.

Schema + ownership are locked; go-live needs only the ASC API key and the offer
config. No SS dependency beyond the redeem-sheet button they already planned.

## Build order

1. Foundation — native schema validation + `min_app_version` capability gate. **Shipped.**
2. Item 2 usage dims (`sessions_this_week`, bounded `days_since_install`). No client dep.
3. Item 1 personalization token contract + token-requires-gate enforcement + dashboard.
4. Item 3 — ASC mint client + offer-code pool store + per-device atomic issuance +
   resolve injection + `storekit_offer` value validation + ASSN entitlement webhook.
   Go-live needs the ASC API key + the one-time offer config; no SS dependency.

Open: SS sends the renderer build's `min_app_version` (DONE for native — app 1.14).
For Item 3, the human setup is ours: create the scoped ASC API key (→ Secret Manager)
and the Subscription Offer Code config (eligibility = New subscribers) on the
auto-renewable subscription.
