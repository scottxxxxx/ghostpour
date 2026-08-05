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
