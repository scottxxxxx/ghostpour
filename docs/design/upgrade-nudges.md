# Upgrade nudges: when, why, and how

Scott, 2026-08-23: "fix this to align with the actual differences and
when, why and how we should nudge someone to upgrade from free to plus or
pro and from plus to pro."

## What was wrong

An inventory of every CTA prod served on 2026-08-23, read off
`/v1/config/tiers` rather than from memory:

| Tier | Trigger | Copy | Target |
|---|---|---|---|
| Free | monthly AI budget exhausted | "Upgrade to Plus to keep going" | paywall |
| Free | web search (no allowance) | pitch for web search | paywall |
| Free | file generation offered | "Want this as a real downloadable file?" | confirm |
| Plus | 75 searches exhausted | "Upgrade to Pro for higher limits" | paywall |
| Pro | 120 searches exhausted | "Searches resume on {reset_date}" | dismiss |

The entire Plus to Pro path was ONE trigger, and it was the weakest
argument Pro has: 45 more searches for five dollars. Nothing surfaced
what actually separates Pro from Plus (memory with no window, the
stronger model on reports, 360K context against 150K, 5 images against
3). Free's memory cell was `disabled` rather than `teaser`, so the thing
the tier sheet calls "THE value proposition" produced no Free nudge.

The sheet already contained the rule, aimed at Free only: "a teaser that
says upgrade for more context carries no information; one that says this
answer skipped 6 earlier meetings does." It is the rule for every nudge.

## The principle

**A specific, earned reason, at the moment it bites, with the number.**

- Specific: name the thing that just did not fit, not the tier's brochure.
- Earned: fire only when the user actually hit the limit. Never on a
  schedule, never on launch, never "while you're here".
- The number: how much was asked, how much the next tier allows, how many
  meetings were left out. A nudge without a number is an ad.
- Honest about fit: never recommend a tier that would fail the same way.
  If no tier fits, the only advice is to trim, with no upgrade affordance.
- Zero is silence. A count that is not there is never invented.

## The matrix

| Moment | From | To | Data that makes it true | Status |
|---|---|---|---|---|
| Memory found earlier meetings the People-scoped recall could not use | Free | Plus | CQ `excluded.by_scope.meetings` | GP built, **waits on CQ field** and on Free's cell flipping to `teaser` |
| Matching meetings older than the 30-day window were out of reach | Plus | Pro | CQ `excluded.by_window.meetings` | GP built, **waits on CQ field** |
| Project Chat context over the tier cap, and a higher tier's cap fits | Free, Plus | the lowest tier that fits | served `max_input_chars` per tier | **LIVE** (this PR) |
| Monthly AI budget exhausted | Free | Plus | budget gate | live before |
| Search allowance exhausted | Free, Plus | next tier | search dials | live before, copy unchanged |
| Report generated on a tier below Pro | Plus | Pro | routing: Pro gets the stronger model | **not built, and not a gate** (see below) |
| Image cap hit (3 on Plus, 5 on Pro) | Plus | Pro | image dials | client-side today; GP has the dials if SS wants a served CTA |

### What is deliberately NOT a nudge

The stronger model on Pro reports is real and is the hardest to
demonstrate honestly: there is no moment where a Plus user "hits" it. A
line on every Plus report saying "Pro would have done this better" is an
ad with no number behind it, and it violates the ghost-relay rule by
inviting the question of which model. If this is ever surfaced it should
be a comparison the user can see (the same meeting, both reports), which
is a product feature and not a nudge.

## Mechanism

One additive field from CQ carries both memory nudges. CQ applies the
scope predicate (Free's `recall_scope: people`) and the window predicate
(Plus's `max_age_days`), so CQ is the side that can count what each cut.
Asked 2026-08-23, on the `/v1/recall` response:

```json
"excluded": {
  "by_scope":  {"meetings": 6},
  "by_window": {"meetings": 4, "oldest": "2026-05-02T10:00:00Z"}
}
```

Counts are MEETINGS, not patches: a user thinks in meetings. Either
block may be absent. GP reads the raw recall response (it is not
modelled), so the field flows the moment CQ ships it and does nothing
until then.

GP renders served copy around the count into the chat envelope's
`feature_state` (the same slot as the generation teaser, which wins when
both exist because an in-flow offer is worth more than a nudge):

```json
{"feature": "context_quilt", "state": "teaser",
 "cta": {"kind": "memory_excluded_window",
         "text": "4 matching meetings older than 30 days were out of reach. Pro has no window.",
         "primary_action": {"label": "See Pro", "action": "open_paywall", "plan": "pro"},
         "secondary_action": {"label": "Not now", "action": "dismiss"},
         "details": {"excluded_meetings": 4, "window_days": 30}}}
```

Copy lives at `tiers{.locale}.upgrade_nudges.{key}` in served config, GP
owned, SS renders. Code carries an English floor so a locale missing the
block cannot ship a raw placeholder; a locale is free to ignore
placeholders it does not use (Spanish and French do not pluralise by
suffix).

The context block (`413 context_too_large`) keeps `action: trim_context`
as primary and adds `secondary_action: {action: open_paywall, plan}` plus
`details.fits_on` ONLY when a higher tier's served cap fits the request.

## CTA kinds SS will see

New: `memory_excluded_scope`, `memory_excluded_window`, and a
`secondary_action` plus `details.fits_on` on `context_too_large`. Unknown
kinds must be ignored by the client (already the contract); a build that
does not know them renders nothing, which is the right degradation.

## Open

- CQ: the `excluded` block. Until it ships, the two memory nudges are dormant.
- Scott: flip Free's `context_quilt` cell from `disabled` to `teaser`,
  which is the pending matrix PR. The Free nudge cannot fire on `disabled`
  because the People-scoped lane runs instead of the teaser lane; the
  honest build is to have the People lane report `by_scope` exclusions
  rather than add a second recall call.
- Image-cap CTA: only if SS wants it served rather than client-side.
