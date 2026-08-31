# Recall scoping posture: what is left after the cue leg

**Status:** proposal, for Scott. Written 2026-08-30 by GP (session cloudzap-35).
**Revised the same evening** after CQ measured the input this was gated on.
The recommendation CHANGED as a result, and one of my framing numbers was
wrong; both are marked inline rather than quietly edited out.
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

`project_id = $2 OR project_id IS NULL OR ...`. The permissive arm is correct
in intent, since an unstamped patch genuinely might belong anywhere.

⚠ **My original framing of this was wrong and CQ corrected it with a
measurement.** I said the corpus was "~94.7% unstamped" and treated that as
the size of the problem. It is not, because **several types are universal BY
DESIGN**: a preference does not belong to a project, and the flat leg serves
it into every scope on purpose. Stamping those would be *wrong*, not missing.
Separating them out changes the number by a lot:

    active patches                                     5194
      stamped                                          2628   50.6%
      unstamped                                        2566   49.4%
        universal by design (stamping would be WRONG)  1091
        the actual gap                                 1475

So the gap is 1475 rows, not "almost everything". Anyone quoting 94.7% off
the original incident write-up, including me, was counting rows that are
doing exactly what they should.

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

#### MEASURED (CQ, 2026-08-30). The headline number is good and should not reassure anyone.

Method: same-meeting inheritance only. A project-null patch counts as
inferable when its origin has sibling patches carrying exactly one distinct
`project_id`. Where siblings disagree it was counted **ambiguous rather than
guessed**, which is the right call. Other signals (a `parent` connection to a
stamped patch, entity overlap) are unmeasured, so **82.1% is a floor for this
method, not a ceiling for all methods**.

    over the 1475-row gap
      inferable, siblings agree on one project         1211   82.1%
      ambiguous, siblings name more than one             20    1.4%
      meeting has NO stamped sibling at all             195   13.2%
      no origin_id to inherit from                       49    3.3%

**82.1% is one type wearing a trenchcoat.** 1210 of those 1211 are `behavior`:

    behavior      1210 / 1407    86.0%
    role             1 /    1   100.0%
    commitment       0 /   17     0.0%
    takeaway         0 /   17     0.0%
    decision         0 /    9     0.0%
    deliverable      0 /    8     0.0%
    constraint       0 /    4     0.0%
    blocker          0 /    1     0.0%

Of the 68 non-behavior rows in the gap, **exactly one** is inferable. The
zeroes are not coincidence: those meetings have no stamped sibling because
**they have no project at all**. They are the tire store and the vet visit.

#### What that does to this option

1. **"Stamp the corpus" is really "stamp `behavior`"**, and that is a decision
   about one type rather than a corpus-wide backfill. Behavior is 1407 of the
   1475 gap rows, 95.4% of it. Much smaller and more tractable than my
   original framing implied, and it closes a real leak: behaviour is what came
   through the null arm in the original incident.
2. **The remaining 68 rows can never be inherited, by any amount of
   stamping.** They are Scott's casual-use case, and they will not shrink.
   What a project-less meeting is entitled to is a **design question**, not a
   backfill problem.
3. **The ordering question this document posed partly dissolves.** If stamping
   is one type, and the header has to solve project-less meetings regardless,
   then stamping first is cheap but does **not** buy down the header work. The
   two are closer to independent than sequential.

CQ explicitly declined to call the ordering settled on their number alone,
which is correct: they measured one input to options I wrote, and neither of
us should conclude on the other's half.

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

## Recommendation (REVISED after CQ's measurement)

The measurement is in, so this is no longer gated.

**Do B, scoped down to `behavior` only. Treat C as independent work, not as
something B is a prerequisite for.**

B is now a small, well-bounded job: one patch type, 1210 inferable rows,
inheriting from same-meeting siblings that agree, leaving the 20 ambiguous
ones alone. It closes the leg that the original incident actually came
through, with no hot-path change. It is worth doing on that basis.

But B **no longer earns its "first" billing in the way I originally argued**.
I put it first on the theory that it would shrink the problem C has to solve.
It does not: the 68 non-behavior rows are untouchable by inheritance and are
precisely the project-less case C has to answer for. Sequencing them was my
error, and it came from assuming a corpus-wide backfill where the data says
one type.

C remains the leg the actual customer names came through, and nothing in the
measurement makes it smaller.

A stays the honest fallback and should be a decision rather than a default.
D stays parked.

---

## ⚠ A finding that may outrank this entire document

While measuring the above, CQ looked at Scott's **tire store** meeting, the
case this document is designed around. CQ kept **one** patch from it: a
single behaviour observation, from the dedicated behaviour call. The main
extraction produced **nothing at all**. No price, no commitment.

The **vet visit**, by contrast, got a project and produced ten patches
including a real commitment. So this is *not* "personal topics fail", and
neither team knows yet what differs. CQ is explicitly not asserting a cause.

If that generalises, then for the casual-use case that motivated the whole
scoping constraint, **the header and the null arm are both downstream of a
capture gap that leaves almost nothing to serve.** Scoping recall better does
not help when there is nothing in it.

I would not let this block B, which is worth doing regardless. But I would
want it understood before anyone prices C, because C is largely justified by
serving project-less meetings well, and we do not currently know whether
project-less meetings have anything to serve.

**This is unexplained, not diagnosed.** One meeting each way is an
observation, not a pattern.

### CQ measured further and the honest result is "cannot separate"

CQ tried to split the two hypotheses (is it the absence of a project, or did
the vet visit simply have more extractable content) and reports that **neither
instrument available to them can answer it**. Two things came out of the
attempt that stand on their own:

- Their first cut printed a perfect correlation, 100% of project-less
  meetings silent and 0% of project-having meetings silent. **It was
  spurious.** Only 79 of 1486 behavior patches are scoped, so "every patch is
  behavior" nearly entails "no patch is scoped": two variables that were one
  variable. They caught it and did not send it.
- `origin_project_assignments` (their migration 43) is the independent signal,
  since it records what the USER decided, but only 4 of 162 meetings have a
  row, because most meetings take their project from the ingest request.

What does stand:

    34 of 162 meetings produced ONLY behavior patches, EVER
      their patch counts: min 1, median 5, max 17
      all meetings:       min 1, median 14, max 43

(First reported as 38. Four were artifacts of the 14-day window: 17 of the
original 38 were ingested inside one three-minute span on 08-17, which is a
BACKFILL running the behaviour call over historical meetings. Their own
non-behavior patches were created weeks earlier and sat outside the window,
so they looked behavior-only while being nothing of the kind; one had 28
non-behavior patches from 2026-06-25. Silence is now tested unwindowed, "this
origin has no non-behavior patch ever", and the four are reported as excluded
artifacts rather than dropped quietly.)

A meeting yielding **seventeen** behaviour observations and not one
commitment, decision or takeaway is not explained by "too short". So the
more-content hypothesis is **dented, not eliminated**, and it may still be
the whole story for the tire store specifically. One hypothesis weakened is
not the other confirmed.

CQ's caveat, which they put in the script rather than only in a commit
message: dedup means a meeting whose content merged entirely into EXISTING
patches writes no new rows and looks identical from here. So the 34 counts
meetings producing no non-behavior patch, which is not quite "extraction
returned nothing". For the tire store the stronger claim does hold, since
there was no prior tire content in the corpus to merge into.

### The instrument EXISTS, on GP's side, and is backfillable

**Transcript length against extraction yield can be measured retroactively
over a rolling 30 day window.** It does not have to be instrumented at ingest
and waited for.

(An earlier draft of this section led with CQ's "can only be measured going
forward, never backfilled" and corrected it underneath. CQ asked for that
struck rather than footnoted, because the first sentence is the one that gets
quoted and as written it foreclosed an option that exists. They were right
and it is struck. The constraint was true of CQ alone and was asserted across
a boundary without checking the other side, which is rule 5: name which side
you proved it on.)

**GP retains transcripts for 30 days** (`TRANSCRIPT_RETENTION_DAYS = 30`,
`app/services/retention.py:29`), and `meeting_transcripts` carries
`meeting_id`, the `transcript` text itself, **and `project` / `project_id`**,
all written on every `/v1/capture-transcript`
(`app/routers/cq_proxy.py:306`). So transcript length against CQ's extraction
yield **is backfillable over a rolling 30 day window from GP's side**, joined
on `meeting_id`. It is not "measure it going forward or never".

**The same join also partly reopens the audit this document says was
blocked.** I stated that `usage_log` has no project column, which is true,
but I let that stand as "the split cannot be done". `usage_log` has
`meeting_id`, and `meeting_transcripts` has `project_id`, so
`usage_log.meeting_id -> meeting_transcripts.project_id` is a path to the
call's project for any meeting still inside the transcript window. The leak
window (2026-08-15 onward) is inside 30 days.

Caveats, because a schema path is not a measurement:
- Only meetings that actually went through `/v1/capture-transcript` have a
  row. Chat turns without a capture do not.
- Whether `project_id` is reliably POPULATED rather than merely present is
  **unmeasured**. The column exists and the write is unconditional on the
  client's value; that is not the same as it being non-null in practice.
- **I could not run either query.** Prod container exec was blocked for this
  session, so this is a path identified by reading the schema and the write
  site, not a result. It should be run before anyone relies on it.
- CQ adds, from their side, that the ingest-request project is what stamps CQ
  patches, so **a null `project_id` would be systematic rather than random**.
  Check the null rate before building on the join.

**The cohort to run it against is landed and ready:**
`2026-08-30-silent-meeting-cohort.md` carries CQ's 34 silent meetings, a
10-meeting productive control (without which lengths prove nothing), and four
EXCLUDED ids that a naive 14-day join on GP's side would reproduce as false
positives.

So **758 of 1196 may be splittable after all**, and I would rather correct
that here than have my "upper bound, cannot be split" framing harden into a
fact. It was an accurate description of what I could do, not of what the data
supports.

---

## GP's own piece, which is ours regardless of the above

**`usage_log` does not store the call's project.** This is what stopped the
audit: I could not separate "legitimately an ABM meeting" from "leaked into
an unrelated project", so the widely-quoted **758 of 1196 is an upper bound
and not a leak count**. Nobody should carry it as measured.

Adding project to the usage row is GP-only, cheap, and worth doing on its own
merits.

⚠ **But the indirect route is more than a "maybe" and I under-sold it.**
`usage_log` carries `meeting_id`; `meeting_transcripts` carries `meeting_id`
AND `project_id`, written on every capture and retained 30 days. That is a
join, not a hope, and it needs **no schema change at all** for any meeting
inside the transcript window. See the correction under the tire-store section
for the caveats, chiefly that I could not run it and that whether
`project_id` is reliably populated is unmeasured.

A schema column is still worth having, because it survives the 30 day purge
and covers calls with no capture. But it is an improvement on an audit that
is probably already possible, not the thing standing between us and one.
**Not started, and I would want your yes before a schema change against prod
rows.**

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
