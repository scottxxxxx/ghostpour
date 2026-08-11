# GP review of the SS segregation design

Date: 2026-08-11. From GhostPour, reviewing ShoulderSurf's
"People/Memory segregation design" (this directory, same date), before
the TestFlight build cuts. Our boundary doc asked to see this design
before it ships rather than review it after; this is that review, done.

**Outcome: ACK. Nothing here blocks the build.** Two confirmations
requested below, both answerable in a sentence, neither requiring a
design change unless the answer is no.

**Amended 2026-08-11:** confirmation 1 originally argued from the
synthetic upsell card in the quilt payload, which was retired the same
day (#664) and had never rendered on any build. The confirmation still
stands and the amended text gives the argument that survives. Nothing
else in this review changed, and the ACK is unaffected.

## The rule, and why we are signing it

"Memory is about what the user knows; People is about who they know" is
the right rule, and it lines up exactly with the boundary the ratified
decision drew for recall: the people-scoped lane serves who the user
knows, the paid lane serves what they know. The same sentence now
governs the screens and the assistant, which is the whole point of the
"the assistant may know exactly what the user's own screens show them"
rule. One boundary, two renderings of it.

## Entitlement boundary: clean

Nothing in the design moves paid Memory content onto a free surface,
and nothing narrows what the ratified decision made free. The full
per-person surface stays on People on every tier; Memory stays the paid
surface with its gate copy unchanged. Person patches continuing to
arrive in the quilt payload to free devices is not a new leak: that is
our own standing call (the quilt accumulates and the entitlement gates
what renders, so an upgrade reveals history instead of an empty room),
and this design changes nothing on the wire, which is exactly what
"filter-and-links only" should mean.

## Composition contract (doc 17): clean

The design puts no prompt content and no composition decisions in the
client. The one copy split it makes is the right one: the paid-gate
copy stays server-served (our cta_strings, unchanged), and the new
first-run copy is ordinary client UI text describing what the system
does. The rule we want on the record for future edits: any string that
describes what a PLAN withholds stays served, so a pricing or tier
change never needs an SS build. The design as written already conforms.

Related, so SS does not wait on it: the people-scoped recall lane is
server-side only. When it lights up for free users, the assistant
starts speaking People content with zero SS work, which is doc 17
behaving as designed.

## Empty states: confirmed

"First-run states, not tier states" was our ask, verbatim, and the
design answers it: copy says what the system does, not what the plan
withholds. Signed.

## The proxy layer: no surprises, and every dependency is live

- No client-side person store and no client-built name-matching join:
  this is the answer we wanted, and the reasoning ("duplicating CQ's
  entity resolution on device is the split-brain this design exists to
  prevent") is the reasoning we would have written.
- Every verb the design names is carried at our edge and pinned against
  the route table: rename, merge, keep-separate, confirm, both
  not-a-person verbs, and the ledger verbs. All nine people routes are
  live and wire-verified in production as of this review, so one status
  line in the design is stale in the good direction: not-a-person is no
  longer "CQ is building", it is built, carried, and deployed. No
  dependency remains on our side for the build to cut.
- owner_entity_id is live on quilt action items, so the owner chips
  light up on current data, not on a promise.

## The two confirmations

1. **The Memory filter is subtractive, not an allowlist.** Confirm the
   filter's polarity: "hide category person", never "render only known
   memory categories".

   *Corrected 2026-08-11, after this review was written.* The original
   version of this point argued from the synthetic fact-shaped upsell
   card GP injected into the quilt `facts` array. That card was retired
   the same day (#664), so the harm it described cannot occur: the route
   is now a pure passthrough and the free-tier Memory upsell rides the
   gate and teaser lane on served `cta_strings`. Worth noting why it was
   retired, because it strengthens rather than weakens the point: SS's
   decode audit found the card had NEVER rendered on any build, since
   their `PatchType` is a closed enum and an unknown `patch_type` fails
   item decode and is dropped before any rendering code runs.

   The ask stands on better ground without it. An allowlist of known
   memory categories silently drops any category CQ adds later, which is
   the additive-vocabulary rule applied to rendering: a reader must
   tolerate a value it does not recognise. A subtractive filter hides
   what it is told to hide and passes everything else through, so a new
   CQ category appears rather than vanishing, and nobody has to remember
   to update a list on a client release cycle.

   To be explicit, so nobody answers a question about a payload that no
   longer exists: there is no synthetic card to preserve, and SS should
   confirm the polarity on the future-category argument alone.

2. **The name/alias fallback on person taps resolves against served
   data only.** patch_id-first is right. When it falls through, the
   name/alias match must run against the names and aliases the People
   list itself serves, never a client-side fuzzy or normalized match,
   and an unresolved tap should degrade to no navigation rather than a
   best guess. If that is what is built, say so and this point closes;
   it is the one place in the design where a second entity-resolution
   opinion could sneak back onto the device.

## Status

With the two confirmations answered yes, GP's review is complete and
the hold on the TestFlight build lifts from our side. Anything found on
the device pass still follows the standing rule: three-way audits
resolve cross-team data disputes.
