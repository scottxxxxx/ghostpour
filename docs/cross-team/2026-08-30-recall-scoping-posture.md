# Recall scoping posture: what is left after the cue leg

**Status:** proposal, for Scott. Written 2026-08-30 by GP (session cloudzap-35).
**Decision it serves:** Scott ruled on 2026-08-30 that the deeper scoping
problem "becomes real work". This is the scoping, not the build.
**Not covered here:** the cue leg. CQ #351 closed it (word boundary plus a
project predicate) and is green and with Scott.

---

## What actually happened, in one paragraph

A 31 second tech-podcast recording tagged to the **Twit** project produced a
summary that recited a real customer engagement back at the user: named
individuals, an overdue commitment with an owner, and a production-promotion
decision, none of which were in the recording. It is keyed per user and
measured as one user only, so it is **not** a cross-user leak. The concrete
risk is a summary that gets forwarded or shared, and the share lane makes
that real rather than theoretical.

GP's half shipped as PR #825: a refusal must never recite the context block.
That holds regardless of anything below. Scoping shrinks the blast radius; it
does not make reciting safe.

---

## The constraint that should drive the design

Scott's live worry, and it is the right one to design against:

> we may be overfitting the product to business meetings run by project
> managers. His own recent use is a tire store and vet appointments for his
> dogs, none of which have a project at all.

So **"scope everything to a project" is not a posture we can adopt.** For a
large fraction of real use there is no project to scope to, and the value of
memory in that world comes precisely from association across everything the
user has said. A design that is correct for the ABM engagement and useless
for the vet is the wrong trade.

This is also why CQ's #351 is shaped the way it is, and that shape is the
precedent to follow: **the predicate engages only when the caller names a
project.** An unscoped request is untouched, same rows as before. Every
option below should inherit that property.

---

## What is still open, and it is two things

### 1. The entity / relationship header (`main.py:1023`, `:1050`)

Filters on `user_id` only. It has **no project concept at all**, not a
permissive one. This is the source of the "Projects: CTS", "People: Don, Sai"
and "works_on ABM / A2A" lines, so **this is where the actual customer names
came from**, not the cue leg.

It is also the hardest to fix well, because the header is doing real work: it
is what lets recall say "you know these people" across contexts. A person who
appears in three projects is legitimately one person.

### 2. The flat leg's null-permissive arm

`project_id = $2 OR project_id IS NULL OR ...`, against a corpus that is
about **94.7% unstamped** (behavior 1407/1486 null, person 422/423, insight
95/95, org 44/44). The permissive arm is correct in intent, since an
unstamped patch genuinely might belong anywhere, but with a corpus that is
almost entirely unstamped it means *nearly everything is visible in every
scope*. The predicate is doing approximately nothing.

Both of these are **design, not defect**. Neither is a bug someone
introduced; they are choices that were right when the corpus was small and
mostly unprojected, and have aged into an exposure.

---

## Options

### A. Do nothing further. Rely on #825 plus #351.

Not unreasonable, and worth stating properly rather than as a straw man. The
recital guard stops the user-visible symptom, and the cue leg was the loudest
of the three legs. What remains is that unrelated-project material still
*enters the model's context*, invisibly, on most turns.

Cost: zero. Risk: the model still sees it, so the guard is the only thing
standing between a customer's data and a shared summary. One prompt-level
mitigation, no defence in depth.

### B. Stamp the corpus, then let the existing predicate work.

The flat leg already has the right shape. It is starved of data, not
mis-written. Backfilling `project_id` where it can be inferred (via
`origin_id` to meeting to project) would make the predicate mean something
without changing a line of recall logic.

This is the option I would put first if the inference is reliable, because it
fixes leg 2 with **no behaviour change in the hot path**, which matters given
CQ's byte-stability-within-a-UTC-day prompt-caching dependency.

Unknown that has to be settled before costing it: **what fraction of
unstamped patches can actually have a project inferred?** Neither team has
measured this. If it is 20%, this option is not worth much.

### C. Give the entity header a project-aware mode.

The narrow version: when a request names a project, the header still draws
from the user's whole graph but **suppresses entities whose only connection
to the user is through a different project**. A person genuinely shared
across projects stays. A person who exists solely inside ABM does not appear
in a Twit meeting.

This preserves the header's actual purpose and drops the specific thing that
burned us. It is more work than B and it is CQ's code.

### D. A posture switch, per user or per project.

"Keep my projects separate" as a real setting. Honest, and it moves the call
to the person whose data it is, but it is a settings surface, a served
config, and a client change across two apps, and most users will never touch
it. I would not start here.

---

## Recommendation

**B and C, in that order, and only after the measurement in B is done.**

B first because it fixes the leg with the widest reach without touching hot
path logic. C second because it fixes the leg the actual customer names came
through, which B does not touch at all.

A is the honest fallback if the B measurement comes back poor and C prices
badly. It should be a decision, not a default.

D I would park.

---

## GP's own piece, which is ours regardless of the above

**`usage_log` does not store the call's project.** This is what stopped the
audit: I could not separate "legitimately an ABM meeting" from "leaked into
an unrelated project", so the widely-quoted **758 of 1196 is an upper bound
and not a leak count**. Nobody should carry it as measured.

Adding project to the usage row is GP-only, cheap, and worth doing on its own
merits. Note `usage_log` already carries `meeting_id`, so there may be an
indirect route that needs no schema change at all. **Not started, and I would
want your yes before a schema change against prod rows.**

Second gap, same shape: **GP does not log `matched_cues`.** `cq_recall_ok`
records `matched` as a *count* of matched entities, and the full `/v1/recall`
response is parsed and dropped. So cue-level attribution for this incident is
not recoverable, because it was never written down. Cheap going forward, but
CQ must first confirm their response actually carries that key, since logging
a name that may not exist is the exact bug class we spent this week on.

Both gaps have the same lesson: **the audit failed for want of
instrumentation, not for want of will.**

---

## What I could not verify, stated plainly

I could not read the stored `usage_log` row for the leaking turn
(`request_id 4b0617fb7270`). My session's permission layer blocked container
exec into prod partway through the evening. So:

- The **behavioural** half of #825 is unverified. The instruction is proven to
  reach the model correctly positioned on every path; the real leaking turn
  has **not** been replayed to watch the recital stop.
- CQ asked for the request text to identify which cue actually fired. I could
  not supply it, and their `matched_cues` substitute turned out not to be
  logged either.

Neither is a reason to delay a decision on the options above, but the first
one should be closed before #825 is called done.

---

## Costing note

Everything here is CQ's code except the two GP instrumentation gaps. CQ has
correctly refused to change recall behaviour on their own judgment: it is the
hot path, shared by every app, and byte-stability within a UTC day is a
documented upstream prompt-caching dependency. So B and C both need Scott's
go before CQ starts, and B needs its measurement first.
