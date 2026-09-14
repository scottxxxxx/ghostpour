# GhostPour session close, 2026-09-14

⚠ **READ THIS FIRST.** It supersedes `gp-in-flight-2026-09-11.md` for
everything it covers. That file still owns the language-directive bug, the STE
experiment and the four-defects section. This one owns current state.

**prod = main = `ab7ff0c`, read off `/health` over the public hostname.**
Nine PRs merged and deployed this session, #971 through #979. Zero PRs open.
One branch held on purpose (below). Working tree clean apart from the same
seven untracked files as every close since August.

The session ran 2026-09-13 into 09-14 on Scott's instruction to "work and bring
up any subagents you need to fix what we need to fix or improve." Six agents
ran in isolated worktrees; every diff was read before a PR went up. The Fable
Auditor (`fable-auditor-f5`), ContextQuilt (`contextquilt-e2`), ShoulderSurf
(`shouldersurf-a9`) and Bifrost (`bifrost-ac`) were live throughout.

---

## ⚠⚠ OWED BY SCOTT, in the order it bites

1. **The CQ deploy gap, measured, decision yours.** Every ContextQuilt deploy
   is 27 to 34 seconds of 502 on every CQ-backed route through GP, of which
   17 to 24 seconds are AFTER docker reports the container started (CQ's app
   booting through imports and migrations before it binds). Five samples in
   one afternoon, migration or not. GP's #953 connect retry fires and covers
   only the docker swap; the very first caller after a kill gets a 502 in
   190ms regardless, because a live keep-alive is RESET, not refused, and
   httpx does not retry a reset. Bifrost's 49-second edge chain does not fire
   because `proxy_intercept_errors` is unset, so it covers "GP unreachable"
   and not "GP up, CQ down". Three shapes: (a) Bifrost scopes
   `proxy_intercept_errors` to the GP server blocks (covers the whole 30s for
   GET/HEAD; replaces GP's own 502/503 bodies; holds a deliberate 503 for
   49s); (b) GP sizes its retry to ~35s (cheap, but every caller HANGS 30s
   instead of failing fast, and it cannot help the first caller); (c) CQ
   shortens the boot, which fixes the cause. **Lead with (c). Do not do (b)
   blind.** CQ owes which PR each restart was and what the app does in those
   seconds.
2. **`com.weirtech.n400helper` into `CZ_APPLE_BUNDLE_ID`.** Unchanged since
   Wednesday. The cap is 500 and finite, so the ordering rule is satisfied.
   ⚠ New consequence: the verbatim interview payloads in `qa/` are persona
   data only BECAUSE no real Apple identity can reach the lane. Whoever
   extracts inputs after the bundle id lands has to make that call again.
3. **CQ's deploy-notice hook.** A `PreToolUse` hook, written and sabotaged on
   their side, that blocks `gh pr merge` in the contextquilt repo unless a
   confirmed notice receipt exists. Needs a `settings.json` change only you
   can make. Self-attested, so it converts "forgot" into "deliberately
   bypassed" rather than making it impossible; forgetting was the failure
   both times on 09-12.
4. **The two-windows question (rules 1 and 3).** Both cannot ship as ruled:
   the client sends GP an 8-node agenda window, so "can we do the address
   first" from Part 1 names a node GP was never shown and `drop_stale_asking`
   correctly drops it (live receipt: unpred-1 turn 4). Rule 1's "whole current
   session" is ONE part in the only fixture, so built as ruled it would shrink
   the volunteer window. The auditor brings both as one question. GP owes
   nothing on either; the guard stays armed and conf-v19 English 60 is its
   pin.

---

## ✅ SHIPPED THIS SESSION

**#971, the two deferral readers become origin-aware.** GP's half of rule 2.
`deferral_origin()` reads `"origin": "applicant" | "capture_gap"` off a
deferred entry; absent means applicant. Reader 1
(`drop_facts_that_are_also_deferred`): capture_gap inverts, the FACT wins and
the stale deferral leaves `deferred` into `deferred_dropped`. Reader 2
(`_ids_settled_in_this_response`): a capture_gap does NOT settle a node, so a
checkpoint over one is refused. Unknown origin reads as applicant AND is
marked `deferred_origin_unknown`. `BOTH_REASON` split into two constants. Five
sabotages exact by NAME, reader 2's false-positive direction tested first. The
auditor's client four are on their main; unknown top-level keys tolerated,
pinned.

**#972, v31: `intent` is an object, always.** The last irreversible step of
structured intent. The auditor's two edits applied verbatim, plus their
replacement of "`intent` is exactly one of:" with "`intent.type` is exactly
one of:" (two sentences had disagreed about what `intent` IS, the exact shape
that came back as 11 of 39 turns disagreeing with themselves). The tightened
decoder-contract test was SEEN RED against v30 before green against v31.
**SYNCED and read back two ways**: served version 31, 65,251 chars,
byte-identical to the bundle in the running image, every phrase once, the old
opening ABSENT, v30's rule and counterweight intact. ⚠ The auditor's watch for
the first object-shaped live turn is running; that receipt has not arrived.

**#973, the anonymous user row.** Scott: "sorts for our users where the
columns fall out of alignment". The anonymous-device row was three cells short
of the 22-column header, so "last active" rendered under Transl. Sorting was
the exposure, not the cause. `tests/test_admin_user_table_cells.py` derives
the count from the page. ⚠ The test's first version could not tell the fixed
page from the broken one (a backtick-excluding regex versus a nested template
literal), and I then "proved" it with a `git stash` that stashed nothing. Both
are in the commit message as they happened.

**#974, a repeated meeting upload replaces its transcript.** GP's capture did
`INSERT OR REPLACE` under a fresh-uuid PRIMARY KEY with a non-unique index, so
REPLACE had never fired and a replay appended. Proven by two inserts against
the real DDL: two rows. UNIQUE index on `(user_id, meeting_id)`, dedupe and
index in ONE transaction, latest wins. 0 of 265 prod rows were duplicates.

**#975, time to first token recorded.** `usage_log.ttft_ms`, stamped in the
Anthropic stream adapter on the first text or tool delta (not `message_start`,
not `thinking`), NULL where unmeasurable. ⚠ Only the streaming lanes produce
it: SS `query`, `meeting_chat`, its follow-up, `tr_brief_analysis`, roughly
13% of calls. Every N-400 turn, summary, report, generation build and
non-Anthropic call is NULL by construction. TTFT on those lanes is a separate
decision: stream the upstream call internally.

**#976 then #979, the Performance tab.** The Latency tab was one sorted list
of `response_time_ms` across every model and call type; it could not show a
trend by construction. Now: KPI strip with deltas against the previous equal
window, first-token p50 on the SAME axis as total p50/p95/p99 (gap, never 0,
where nothing streamed), a **Stacked** toggle (first-token band plus
generation band summing to the STREAMING median with the all-calls median
over it), small multiples and click-to-filter tables by model and query type,
error rate, tokens/s, streaming share, cost per call. Verified on prod after
deploy, not only on the seeded local server. ⚠ Deliberate deviation: the
stacked generation band is `streaming_p50 - ttft_p50`, not all-calls
`p50 - ttft_p50`, which goes negative when non-streaming calls are fast.
⭐ **First real finding off the tab: p95 latency this week 19.7s against
7.7s the week before, median flat at 3.5s.** Cause not investigated; the
model and query-type filters exist for that. Sonnet 4.6 p95 was 51s over 226
calls on the earlier read, probably the generation lane.

**#977 and #978, the sync script.** Phrase lists are now PER VERSION, located
by block text rather than a line number; an unlisted version refuses. The
guard names both directions of a sha mismatch (a NEWER running image is not
"the OLD bundle"; #977's own deploy landed between the health check and the
v31 sync and tripped exactly that).

---

## ⚠ HELD, on purpose

**`feat/cq-origin-delete-route`**, GP's half of the meeting-level delete
(Scott's 09-07 ruling). Built against CQ's spec, five sabotages exact including
`test_cq_subject.py` catching the raw-user-id mutation on its own. CQ's half is
#483, merged in their batch. **Next step: CQ sends the executing-test bytes
from the LIVE route, GP re-pins the response-side reference to them, merges.**
Their real body gained `origin_type`, `presence`, `by_type`,
`spared_self_typed` and `limits`; preview wins if both flags are sent; the
route docstring must say so.

---

## ⚠⚠ THE RETRY TEST RAN, and it is the thing to carry

Five windows, months of "did not run", and the reason was never bad luck. The
condition was a `GET /v1/people` IN FLIGHT against GP at the restart second.
Interviewer turns never touch CQ. The auditor's loop had to be running INTO
CQ's first merge rather than started after it (restart 1 of the batch was
missed by 59 seconds for exactly that reason). Once it was, restarts 2 through
6 each produced 7 or 8 consecutive 502s. Full table and reasoning in
`memory/project_cq_connect_retry_never_exercised.md`.

Two corrections of mine along the way, both caught by opening a file: "the
auditor's lane is the source of GP-to-CQ traffic" (it was `/v1/people` being
called from outside; the line above the one I quoted said so), and "GP-to-CQ
calls come ONLY from `/v1/people`" (the surface is large, and `tier-change`
is server-initiated, so a restart can be crossed by traffic nobody tapped).
And one instrument lesson: CQ's inside timestamp and my "outside" capture
matched to the digit and were the SAME `docker inspect` read twice. Two
readings of one instrument are not corroboration.

---

## Open threads, with the exact next action

- **deferred_origin PROMPT (v32).** Scott gave the go. The text is the
  auditor's; the anchor facts were sent (`"deferred": ` schema line occurs
  once, `"origin"` zero times). **Waiting on their edits.** Then: pin a test
  that `"origin"` is declared and absent-means-applicant is stated, seen red
  against v31; `VERSIONS[32]` in the sync script; dossier v32 entry; PR; CI;
  merge; `/health` equals the merge sha; `scripts/sync_n400_prompt.py
  --expect-sha <running sha>`; read back; send the string to the auditor.
- **The import ingest gap.** The "edge click" NEVER EXISTED (no upstream
  block; the resolver 502s before `proxy_next_upstream` is reached). SS's
  ledger is the fix, about ten lines at `MeetingStore.swift:4246`. SS HOLDS
  until CQ #482 (ingest-side dedupe on the `X-CZ-Recovery` marker, Scott's
  ruling) reports live with a file name. ⚠ **GP owes SS the "on main" for
  #974 plus that file name in ONE message; not yet sent.** CQ's one-time
  cleanup dry run reports the after-stamp unmarked repeat count; if nonzero, a
  producer of duplicates nobody has named.
- **`served_patch_ids` as `X-CQ-Served-Patch-IDs`.** CQ #481 is live and
  adds `served_patch_ids` on `/v1/recall`; it does NOT reach SS because recall
  is consumed inside GP's chat pipeline. GP forwards it as a header; SS
  switches the "Almost Had It" teaser to it. NOT built. Same for
  `deduplicated: true` on `/v1/memory` (consumed inside GP; surface only if SS
  wants it). The dismiss route is CLEAN both ways (`body: dict | None`
  forwarded untouched, response passed through), so `mute`/`muted` need
  nothing.
- **The 11 unstable turns** are unrecoverable (only the count survived;
  `ste_run.py` posted to `api.anthropic.com` directly so `usage_log` could
  never hold them), NOT a subset of the 15 blind-pack turns, and regenerable
  for about $2 with the harness on VM host `/tmp`. ⚠ "Instability
  concentrates on 3+ deferred fields" is NOT a GP hypothesis; the auditor's
  handoff attributed it wrongly and has corrected it.
- **The two disclosure metrics** exist on the auditor's side:
  `disclosure_rate_conv0906` (rev 2, flag off, 39.0%) and
  `v30_conditional_compliance` (rev 3, flag on, 36.6%). The 2.4 points is one
  label. Both label revisions are in two repos.

---

## Gotchas added this session

- ⚠⚠ **Agent worktrees are LOCK-HELD by the harness** and one of them holds
  `main` after its branch is deleted. `gh pr merge --delete-branch` then fails
  in the primary checkout with "'main' is already used by worktree". Prune
  finished worktrees with `git worktree remove -f -f`; every agent branch is
  on origin so nothing is lost. Or use `gh pr update-branch` server-side.
- ⚠ **Strict main serialises everything.** Each merge puts every other PR
  BEHIND and CI reruns (~15 min). Four PRs is four rounds. Adjacent appends to
  `MIGRATIONS` in `app/database.py` conflict on rebase; resolve in main's
  order and run the migration tests for BOTH.
- ⚠ **A chained command can commit on a red run.** Gate an amend on the
  test's EXIT CODE, never on reading a line.
- ⚠ **`git stash push <file>` on an already-committed file stashes nothing**
  and the "broken" run is the fixed page again. Check out main's copy instead.
- ⚠ **A monitor can arm itself broken.** Read what it captured at arm time
  (`baseline=unknown` means it will never fire), not that it started.
- ⚠ **Container `/tmp` dies on deploy, VM host `/tmp` dies on reboot.** The
  STE harness (`ste_run.py`, `ste_judge.py`, `ste_analyze.py`) is still only on
  VM host `/tmp`.
- ⚠ **The sync guard refuses a NEWER running image too.** Confirm the running
  bundle's version, then re-run with the running sha. That is the right
  refusal; the message now says which direction it might be.
