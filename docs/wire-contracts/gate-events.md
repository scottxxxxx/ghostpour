# Feature-gate events — wire contract

`POST /v1/promo/events` reporting for the moment a user is stopped at a
gate and shown an ask. Extends the campaign event shape rather than
adding an endpoint, so a gate that a campaign has claimed and a gate
running on the served fallback copy report through the same path and are
comparable on the same key.

Last updated: 2026-08-04. Server-side live; SS renders and reports in
their next build.

## Why it exists

Campaign cards had weighted variants, targeting and four event types.
Feature-gate CTAs had a single string and reported nothing, so the copy
we most wanted to A/B test was the copy we could not measure. Baseline
is the missing half of a test: without it the first campaign has nothing
to beat.

SS could not build it from our side of the contract. `campaign_id` was
required, and it is also the conflict key for the frequency-cap upsert,
so a gate event had nothing to write against.

## Request

```http
POST /v1/promo/events
X-App-ID: shouldersurf
Authorization: Bearer <optional>
Content-Type: application/json

{
  "event_type": "impression",
  "device_id": "<uuid>",
  "feature": "chat",
  "surface": "orb",
  "block_reason": "quota"
}
```

`204 No Content` on success. Unauthenticated is fine; `device_id`
anchors the row and `user_id` is recorded when a token is present.

| Field | Required | Notes |
|---|---|---|
| `event_type` | yes | `impression` \| `dismiss` \| `click` \| `convert`. Closed set, 400 on anything else. |
| `device_id` | yes | |
| `feature` | see below | The gate. **Open vocabulary.** |
| `surface` | no | Where the ask rendered. Open vocabulary. |
| `block_reason` | no | `signed_out` \| `tier` \| `quota`. **Open vocabulary.** |
| `campaign_id` | see below | Present when a campaign supplied the copy. Absent on the baseline arm. |
| `variant_id` | no | Campaign arm only. |
| `cta_id` | no | Which CTA was tapped. |
| `visible_ms` | no | Impression/dismiss dwell. |

**One rule:** `campaign_id` or `feature` must be present. An event about
nothing is worse than no event, because it inflates a denominator and
cannot be attributed to anything.

## Which key names the gate

`feature` is **not** validated against the entitlement keys, deliberately.
Not every block is an entitlement. A zero-credit user stopped at the first
orb tap is a budget block, and there is no entitlement key for "asked the
AI". Validating against entitlements would leave budget gates dark, and
that is the gate that stops a free user soonest.

Send the key that our own block response already names in
`feature_state.feature`:

```jsonc
// the 200-with-block payload from POST /v1/chat
{
  "text": "",
  "feature_state": {
    "feature": "chat",          //  <- this value
    "credits_remaining": 0,
    "cta": { ... }
  }
}
```

Values in use today: `chat`, `project_chat`, `meeting_report`, `search`,
`document_generation`, plus the entitlement keys for tier gates
(`context_quilt`, `people`, ...).

Echoing the block response means neither side has a list to keep in sync,
including for gates that do not exist yet. An unrecognized value is
recorded rather than rejected: a client must be able to report a gate we
have not named, because the alternative is silence, and silence reads as
"nobody hit that gate".

The same reasoning governs `block_reason` and `surface`. Widening a
vocabulary is safe for every build in the field; retyping a field is not.
We would rather name a value late than version the shape.

## Which variant field carries the gate copy

A campaign card has a title, a body, media and buttons. A gate has an
explanation and one button. The mapping is by shape, and it is the one SS
inferred:

| Gate slot | Variant field | Served fallback it replaces |
|---|---|---|
| Button label | `native.ctas[0].label` | `feature_definitions.<feature>.upgrade_cta` |
| Explanation | `native.body` | `feature_definitions.<feature>.teaser_description` |
| What tapping does | `native.ctas[0].action` | Whatever the gate does today |
| Reporting key | `native.ctas[0].cta_id` | n/a, echo it back on `click` |

`native.title` is required by the authoring validator because a card needs
one. A gate has no headline slot, so **ignore it at `feature_locked`**. It
is authored as a label for whoever is reading the campaign in the
dashboard, not as something to render.

For a gate the action is normally `{"type": "paywall", "plan": "plus"}`.
The full allowlist is `appstore`, `storekit_offer`, `paywall`, `url`,
`deeplink`, `none`.

**A holdout at a gate falls through to served copy.** `render: "none"` is
the holdout arm, and on a launch card it means show nothing. At a gate,
showing nothing would leave a blocked user staring at a dead feature with
no explanation, so it means "no campaign copy here" and the served
`teaser_description` / `upgrade_cta` render instead. The holdout arm is
therefore the baseline arm, which is exactly what you want to measure
against.

## Asking for a moment: `GET /v1/promo/resolve`

```
GET /v1/promo/resolve?device_id=<uuid>&placement=feature_locked&feature=context_quilt
```

| Param | Meaning |
|---|---|
| `device_id` | Required. Anchors targeting and frequency for signed-out users. |
| `placement` | The moment. `launch`, `feature_locked`, ... |
| `feature` | Which gate is on screen. Only meaningful for a gate placement, and it is the same key you send on the event. |

`{}` means no campaign, which is the normal case: render the served copy.

Until 2026-08-04 resolve **ignored `placements` entirely** and returned the
highest-priority matching campaign for the app whatever moment was asked
about. That was harmless while `launch` was the only moment anyone
rendered. With a second moment it means a gate serves the launch card, and
the launch ping serves gate copy with no gate behind it. It now filters.

Two compatibility rules keep everything already live unchanged:

- A client that sends no `placement` gets the old behaviour. Build 803
  does not send one and never will.
- A campaign that declares no `placements` matches any moment.

A placement entry may name the gate it belongs to:

```jsonc
"placements": [
  {"placement": "feature_locked", "feature": "context_quilt", "priority": 50}
]
```

An entry that names a feature serves only that gate. An entry that names
none serves at **any** gate, which is how you write copy like "Plus
unlocks this" once instead of once per feature. A request with no
`feature` never matches an entry that names one, because we cannot tell
which gate is on screen and guessing puts Memory copy on the People gate.

Per-placement `priority` beats the campaign-wide `priority`. A priority is
a statement about a moment, so a loud launch card must not win every gate
it also happens to claim.

A malformed placement entry is now a 400 at authoring time. It used to be
inert; now it means the campaign silently never appears, which is
indistinguishable from a flat test result.

## Why the block reason exists

The shape could tell a campaign arm from a baseline, but not why the user
was blocked, and those are different denominators. The funnel ends in
`convert`, meaning subscribed, and signing in is not subscribing.

| `block_reason` | Meaning | Right ask |
|---|---|---|
| `signed_out` | No account yet. People today. | Sign in |
| `tier` | The plan does not include it. Project Chat on Free. | Upgrade |
| `quota` | The plan includes it, this period is spent. Budget gate, memory capture. | Upgrade, or wait for reset |

`quota` earns its own value rather than folding into `tier` because
neither an upgrade nor a sign-in is unambiguously the right ask, and the
user already has the entitlement.

## What counts as an impression

The narrow definition, on purpose: **the user tried to use a gated
feature, we blocked them, and showed the ask.**

Not the paywall opening from Settings, because a user browsing pricing
never wanted any particular feature and pollutes every per-feature
denominator. Not CTA text rendering inside a tier comparison table,
because that is a glance and not an ask. The only rate that means
anything is "of the people who wanted this and were told no, how many
tapped".

## What the server does with it

- Every event writes a `promo_events` row.
- A **campaign-less** event stops there. Frequency capping answers "have
  we shown this card enough"; a gate has no card and no cap, so it never
  touches `promo_presentations`.
- A campaign event advances the per-device presentation row exactly as
  before. Unchanged.

## Never render a CTA for an internal feature

A feature definition whose `category` is `internal` is machinery
(`tag_centroids`, `speaker_consolidation`), not something a user can buy.
Suppress on that positive signal, not on an empty or absent
`upgrade_cta`: a positive signal survives copy drift where an absence
does not.

That makes `category` load-bearing in a system that, correctly, fails
open when a definition is missing, so a definition that lost its category
would render exactly the CTA the signal exists to suppress. GP holds that
end: `tests/test_served_config_shape.py` asserts every definition in
every locale carries a category, that the `features.yml` fallback path
carries one, and that the internal features stay marked internal. The
sibling-key rule alone does not cover it, because it only fires when some
sibling still has the key.

## Related

- `docs/design/gp-promo-decision-engine.md` — campaign resolution, targeting, variants
- `docs/wire-contracts/budget-gate.md` — the block payload that names the feature
- `docs/decisions/prompt-composition-doctrine.md` — sibling-key and locale-coverage rules
