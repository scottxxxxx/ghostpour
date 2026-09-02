# GhostPour: session handoff (2026-09-02, session cloudzap-54 [f792a1])

Second GP session of 2026-09-02. The earlier one is
`gp-in-flight-2026-09-02.md` (session cloudzap-01), which this supersedes
but does NOT replace: everything in it is still true, and its
Accept-Language section was corrected in place by #859 below.

**Prod `9a27d96`. Main `549672c`.** The gap is two DOCS-ONLY commits and
is deliberate: `docs/**` is in the deploy workflow's `paths-ignore`, so
docs do not rebuild the image or restart the container. Prod is NOT
behind on code. **ZERO open PRs**, clean tree, no branches waiting.

This session resumed from a ContextMeter handoff, shipped two docs PRs,
answered a cross-team ask with a code audit, and landed Scott's ninth
cross-team rule. No code changed. No deploy fired.

---

## ⚠ STILL BLOCKING, NOW UNCHANGED FOR FOUR SESSIONS

**Scott cannot comp anyone.** Offer-code pool is at 10; the load of batch
549815 was DENIED by the auto-mode classifier and never retried. Needs
Scott's approval or his own hands. Recipe is in
`gp-in-flight-2026-08-31-close.md`, still correct. Untouched again today.
This is the only item on the waiting-on-Scott list with a live user
impact; the rest are decisions that can wait.

## Shipped

**#859 (`ea0b7ee`), docs.** The 09-02 handoff still carried a section
headed "⚠ UNMERGED WORK, ON A BRANCH, AT SESSION END" naming branch
`fix/forward-accept-language-to-cq`. True when written, false an hour
later: #858 merged and deployed as `9a27d96` and the branch was deleted
on merge. The correction was already committed locally on
`docs/handoff-858-merged` by the previous session, which had deliberately
stopped short of pushing to ask Scott first. Scott said push and merge.

**#860 (`549672c`), docs.** Cross-team rule 9 in GP's CLAUDE.md. See
below.

## Rule 9, and how it was verified

Scott ruled a ninth cross-team rule into all three CLAUDE.md files. CQ
has it at `67726ab`, SS at `1530511`, GP now at `549672c`.

The rule: a mechanism that fits the observable is a hypothesis, and a peer
with the same habit cannot test it. The check is somebody READING THE CODE
that produces the behaviour, and that check cannot be delegated to a team
that fails the same way, because two parties reasoning from the observable
will confirm each other indefinitely. Before passing a mechanism to a third
team, name the file that would settle it and say whether anyone has opened
it.

⚠ **It arrived by relay from CQ and was NOT landed on that relay.** A
CLAUDE.md edit on a peer's authorisation is the boundary the previous
session held when it refused a prod read on CQ's relayed authorisation.
Held again. Landed only after Scott said go directly in this session.

Verified rather than pasted, which is the rule applied to its own arrival:

- The text CQ relayed was diffed against CQ's OWN COMMITTED COPY in
  `/Users/scottguida/contextquilt` and is byte identical. Both partner
  repos are on this machine and readable; use them.
- GP's landed block is word for word identical to CQ's and SS's, 1739
  characters each after whitespace normalisation.
- **CQ and SS already differ from each other on line WRAPPING**, 75
  columns against 77, while agreeing on every word. That is what
  established that WORDING is the property and fill is not. GP's file
  wraps at 73 elsewhere; matched CQ's 75 exactly rather than inventing a
  third width. ⚠ If a future check ever diffs these three files
  LITERALLY it will fire on SS, and the answer is "wrapping, checked
  2026-09-02", not drift.
- Re-verified on `main` AFTER the squash rather than assumed to survive it.

## The cq_proxy route enumeration (the substantive output)

CQ asked, and this is now also in project memory under
`reference_cq_capture_wire_shape`. All 43 routes in
`app/routers/cq_proxy.py` read. **9 bind a typed model, 20 take a raw
`dict`, 14 have no body.**

The 20 dict routes forward `body=body` untouched, including the whole
people surface and both alignment routes: fully additive. 8 of the 9 model
routes both declare `extra="allow"` AND forward via `model_dump()`, which
is why `refresh_headline` works on the patch route today.

**`/v1/capture-transcript` is the ONLY non-verbatim forwarder, and it is
deliberate and already named**: `tests/test_cq_proxy_passthrough.py` has
`NOT_VERBATIM_FORWARDERS = {"/v1/capture-transcript"}` with a written
reason, so a new route that fails to forward must be added there on
purpose. An unmodelled TOP-LEVEL key on capture is accepted by pydantic
and read by nothing: 200 and vanish. Extras must ride inside `metadata`
AND be in `CAPTURE_METADATA_ALLOWLIST`
(`app/services/context_quilt.py:366`).

⚠ **Real finding, narrow: 5 of the 8 model routes build the payload with
`if v is not None`** (patches create, patches update, both assign-project,
reassign-speaker), so an EXPLICIT NULL reaches CQ as ABSENT. A tri-state
field collapses to two states there and both ends look correct. The other
3 (connections create and delete, rename-speaker) pass `model_dump()` raw
and DO send nulls. **No test pins this, BY AGREEMENT**: CQ checked and has
no tri-state field today (they clear with `""`). THE TRIGGER: the day CQ
ships a field where absent and null differ, they ask and GP writes the
request-side case per route plus the sabotage.

The enumeration paid for itself anyway. CQ's own 400 text said a value
"must be one of [...] or null", inviting a client to send null to clear
and get a silent 200. Fixed on their side, CQ PR #411, prod `dc830f2`.

## ⚠ INCOMING, NOT YET RULED: `material_kind` and GP SHIPS FIRST

CQ PR #412 is a docs-only PROPOSAL, not a decision, for material Scott
CONSUMES rather than participates in. It came from a podcast recording
that produced five patches, all `behavior`, and nothing from the main
extraction. It needs a capture-time flag `metadata.material_kind`, absent
meaning today's behavior.

**That lands on the one closed route.** `material_kind` is a GP CODE
CHANGE PLUS A DEPLOY, not an additive field that rides through. If Scott
rules it in, GP adds the key to `CAPTURE_METADATA_ALLOWLIST` and deploys
BEFORE CQ ships anything that reads it and BEFORE SS ships anything that
sends it. A client sending it early gets a 200 and silent nothing.

CQ sent the constraint before the decision on purpose. The precedent they
cited is their uncomplete route 404ing from every device because a
sequencing constraint arrived late. In project memory as
`project_cq_material_kind_capture_flag`.

## Verified this session

Full suite run alone: **3555 passed, 5 skipped, zero failures, 378.80s**,
exit 0. Same numbers as the previous session, which is expected since only
markdown changed. ⚠ Local `.venv` is off the prod pin (fastapi 0.135 local
against 0.115 prod), so a green local run confirms the code and is NOT the
honest gate. CI is.

Prod container healthy, "Up 5 minutes" at 15:02 CDT, which matches the
#858 deploy run finishing at 19:57 UTC exactly. Cross-checked rather than
accepted.

Neither docs merge fired a Build and Deploy run. Confirmed against
`gh run list`, not assumed from the `paths-ignore` config.

## Accept-Language, unchanged and still open on the other side

Proved on OUR HOP only, inside the container on the outbound call:
forwarded verbatim with q-weights, allowlist honoured, a client's own
Authorization not reaching CQ, headerless still headerless. **That is not
a device.** The acceptance test for CQ's #406 is localized words on a
phone, never a 200, because CQ's headerless output is byte-identical to
English and every layer looks correct while only the words differ.

Scott's phone is English, so closing it needs his device locale set to es,
fr or ja, or an SS debug override. CQ is putting that to him. Nothing is
blocked on GP.

## Cross-team state

CQ is `contextquilt-9d`. **GP owes them nothing.** Their prod commits
below are THEIR REPORT of their own state, relayed and not verified by GP:
`acb1768` at the start of this session, then `dc830f2` after their #411.
The six misnamed day keys are retired and deployed. `refresh_headline` is
live and reaches them via `extra="allow"`, re-verified this session by
opening `cq_proxy.py:536` and `:560` rather than relaying the previous
session's read. The "6 meetings" report is CLOSED as an SS decoder bug;
there is no dropped response field to hunt.

SS is `shouldersurf-c7`. Nothing exchanged with them this session.

## Waiting on Scott, unchanged

1. **He still cannot comp anyone.** Four sessions.
2. The **shared tier** across the three apps. A Plus bought in SS makes
   you Plus in TR and N-400. Largest remaining instance of his own
   multitenancy ruling, and a revenue decision, not a leak to close
   quietly.
3. Unwinding Tech Rehearsal spend already recorded in people's
   ShoulderSurf allowances. Rewrites recorded history.
4. Policy-matrix legal review, four questions in issue #849.
5. Whether the per-app counter migration is worth doing.
6. NEW: CQ PR #412 once they finish it, which carries the GP-first
   sequencing above.

## Gotchas earned today

⚠ **`gh pr checks <n> --watch` can exit 0 having proved nothing.**
Started before the workflow registered, it printed "no checks reported on
the branch" and exited SUCCESSFULLY. Exit code 0 with no failures reads as
green at a glance, and a `gh pr checks` re-check showed the test actually
pending. **Read the words, not the exit code**, and re-check independently
before merging on a watch result.

⚠ **A reader that depends on ARGUMENT ORDER tests the signature, not the
behavior.** An AST pass over the enumeration read `_cq_proxy` calls
POSITIONALLY and reported all 20 dict routes as forwarding no body at all.
They pass `body=body` as a KEYWORD. Sending that would have told CQ that
20 routes drop everything. Caught only by opening two of them. This is the
same lesson already written into the passthrough test's own history, which
is how little it transfers.

⚠ **ContextMeter auto-retires sessions, and "at rest" is not "finished".**
The `Read <handoff> and continue that work from where it left off.` prompt
is the APP, not Scott: `autoHandoffEnabled=1`, `autoHandoffPercent=85`,
`autoHandoffRestart=1`, and `SessionRestart.run` types `/exit` then
`claude` then the instruction over tmux `send-keys`. It arrives recorded
as `promptSource: typed`, so a transcript cannot tell it from a person.
The previous session was retired holding a committed but UNPUSHED branch
it had deliberately not pushed. Nothing was lost only because the note
said so. **ON RESUME, run `git branch -vv` before believing any handoff's
"in flight: nothing"**: a local branch with no upstream is the shape this
leaves behind. In memory as `reference_contextmeter_auto_retire`.

Both partner repos are readable at `/Users/scottguida/contextquilt` and
`/Users/scottguida/ShoulderSurf`. When a partner relays text or a claim,
the committed source is one `git show` away. Use it.

## In flight

Nothing. No background tasks, no uncommitted work, no open branches, no
unanswered partner messages.
