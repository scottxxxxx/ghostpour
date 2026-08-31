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
| Meetings in the project hold memory the People-scoped render cannot use | Free | Plus | CQ `excluded.by_scope.meetings` | GP built, copy matches CQ's definition; **waits on CQ #325 deploy** and on Free's cell flipping to `teaser` |
| Meetings in the project older than the 30-day window | Plus | Pro | CQ `excluded.by_window.meetings` | GP built, copy matches CQ's definition; **waits on CQ #325 deploy** |
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
CQ's contract (delivered 2026-08-23, CQ PR #325, awaiting Scott's merge
word because it touches the recall hot path). Top-level on the `/v1/recall`
response, sibling of `context` and `patch_count`, NOT inside metadata.
Rides in CQ's render cache, so a cached hit carries the same block as the
miss that built it. Real values from Scott's ABM project:

```json
"excluded": {
  "by_window": {"meetings": 60, "oldest": "2026-04-21T16:05:23.078562+00:00",
                "max_age_days": 30, "definition": "..."},
  "by_scope":  {"meetings": 67, "definition": "..."}
}
```

- Only on PROJECT-SCOPED requests (the chat flow is). `by_window` only
  when `metadata.max_age_days` was sent; `by_scope` only when
  `metadata.recall_scope == "people"`.
- The key is ALWAYS present. `null` = CQ did not compute it (no project
  scope, or no condition applied); a block with zero = a condition applied
  and nothing was kept out. Both are silence for GP; the wire keeps the
  two facts apart. (Amended 2026-08-24 to match the wire: the first draft
  said absent, the proxied proof showed null, CQ ruled null.)
- Counts are MEETINGS (distinct origin_id) the tier could not use, never
  counts returned. **They are not matches that scored.** `by_window` is
  the age predicate inverted over the project scope (last observation
  older than the window; universal self-disclosure types excluded because
  they are never windowed). `by_scope` is cheaper on purpose: on a
  people-scoped request no memory leg runs, so no scored set exists to
  subtract from; it is the meetings in the project holding memory the
  People render cannot use. CQ will not pay the full fetch legs on every
  Free turn for an honest "matches that scored", and GP agreed.
- Cost, measured on the largest scope on prod (1745 rows): ~5 ms warm per
  condition, 43 ms cold once; one indexed COUNT per condition, never a
  second recall. Day-bucketed, byte-stable within a UTC day.

Because the counts are project counts, the copy claims the project
count and never "memory found" or "matching". `tests/test_upgrade_nudges.py`
pins that in all four locales and in the code floor, and renders CQ's
real block above with its extra keys ignored.

GP renders served copy around the count into the chat envelope's
`feature_state` (the same slot as the generation teaser, which wins when
both exist because an in-flow offer is worth more than a nudge):

```json
{"feature": "context_quilt", "state": "teaser",
 "cta": {"kind": "memory_excluded_window",
         "text": "This project has 4 meetings older than 30 days, outside the Plus window. Pro has no window.",
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

- CQ: deploy of #325 (the `excluded` block). Until it is live, the two memory
  nudges are dormant. Then prove it on the proxied path: one project-scoped
  recall carrying `max_age_days`, one carrying `recall_scope=people`, block
  read off the raw JSON on the GP side.
- Scott: flip Free's `context_quilt` cell from `disabled` to `teaser`,
  which is the pending matrix PR. The Free nudge cannot fire on `disabled`
  because the People-scoped lane runs instead of the teaser lane; the
  honest build is to have the People lane report `by_scope` exclusions
  rather than add a second recall call.
- Image-cap CTA: only if SS wants it served rather than client-side.
