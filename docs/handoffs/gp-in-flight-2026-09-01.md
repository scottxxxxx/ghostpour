# GhostPour: session handoff (2026-09-01, session cloudzap-ff [61256e])

Prod and main both at **0c85ef4**, deployed and healthy, verified by
EXECUTION in the container rather than off workflow status.
**Zero open PRs.** Supersedes `gp-in-flight-2026-08-31-close.md`.

---

## ⚠ STILL BLOCKING, UNCHANGED ALL SESSION

**Scott cannot comp anyone.** Offer-code pool is at 10; the load of batch
549815 was DENIED by the auto-mode classifier and never retried. Needs
Scott's approval or his own hands. Full recipe (script shape, offer id,
product id, the never-print-code-strings rule) is in
`gp-in-flight-2026-08-31-close.md`, which remains correct on this. It has
been the top item for a full session and is the ONLY one needing hands
rather than a decision.

## Waiting on Scott (nothing is blocked on GP)

1. **N-400's `budget` block.** Their `gp-ask.md` §1 proposes
   `monthly_cost_limit_usd: ~5.00`. GP registered them with NO budget
   block on purpose (client-gated non-consumable, no per-call entitlement
   for the gate to key on). **So the suggestion is unresolved, not
   declined**, and it still reads as agreed in their contract.
2. **Legal review ownership for the policy matrix.** Their §3 is the
   compliance core, State x Form x Capability, fail-closed. They say
   outright the state-by-state content needs formal legal review. Building
   the engine is ordinary work; being the system of record for
   legally-reviewed immigration rules is a different decision.
3. **Does tonight's inference-hardening lesson earn a CLAUDE.md rule 9?**
   It cost real work (see below). Scott's call, and only his: that file is
   identical across three teams by design.

---

## Shipped this session

- **#840** `9a416f0` — `artifact: "title"` on `/v1/translations`. Closes
  SS's untranslated-headline bug. ENGINE_VERSION deliberately NOT bumped
  (3 legacy prompts byte-identical; a bump re-translates every cached
  rendition for real money). Hash-pinned so a future prompt edit forces
  that decision consciously.
- **#841** `cb44555` — per-app tenancy design doc.
- **#842** `bfe7247` — N-400 Helper registered as tenant `n400`.
- **#843** `8203889` — cross-team CLAUDE.md rule 8, byte-identical to SS.
- **#844** `fe2fd9b` — `GET /v1/projects/{user}/resolve` CQ passthrough.
- **#845** `0c85ef4` — **the live log records query strings.** It never
  did, for any request, ever.
- **#846** `cd088da` — resolve route: never firing is the expected result.

---

## ⚠⚠ THE THREE FINDINGS THAT MATTER MOST

### 1. The live log has never recorded a query string (#845)

`main.py:402` adds `StreamingBypassMiddleware`; line 403 says out loud
that `RequestLoggingMiddleware` is NOT added. The one that runs had no
`query` key. The class beside it that sets one, twice and correctly, never
executes. **Correct-looking code, same file, reachable by reading, dead.**

So the one instrument built to answer "what did the client actually send"
was blind to `?since=`, `?delta=true`, `?limit=`, `?offset=`,
`?project_id=`. Part of why the `offset` cache-key bug was found by
sabotage rather than by logs.

**Found by verifying a promise, not by reading code.** I had already told
SS that GP could say whether a missing param arrived at our edge or was
lost inside our hop. Checking that claim before relying on it is what
surfaced it. The promise was hollow.

⚠ **Still missing: what GP forwards ONWARD to CQ.** `_cq_proxy` logs
nothing on the success path, so the chain (edge log -> GP inbound query ->
GP's response body carrying CQ's echo) localises a dropped param to
"before our edge", "at the proxy", or "inside GP or CQ", but cannot split
that last pair. Next obvious observability work.

### 2. There was never any project UUID drift

An inference from a symptom hardened into a shared premise across three
teams and generated an SS client converge, a CQ endpoint (`efa1e12`),
GP's #844, and a ruling from Scott. **Nobody read `projects.json`.** SS
finally did: every project held its own id, double space intact, file
untouched for 8h before the observation. The wrong id came from a reused
SwiftUI view whose `@State` load flag survived a swap. On the device,
invisible to any server log on either side.

**Cross-team agreement is not evidence; it is the same claim with more
authors.** Ask WHO HAS READ THE FILE and accept only a name.

Second-order trap this leaves GP: #844 is now a safety net for an
unobserved defect, so **zero repairs is its permanently expected result**.
#846 says so at the declaration site so a future reader does not delete it
as dead code, which would be the same inference-from-behaviour error one
step out. SS carries the identical framing in `93a4a75`.

### 3. GP's keyword prefilter caught 5 of 216 real asks

Answering N-400's intent-recognition ask (§7). Our vocabulary prefilter,
written by people who knew the product, matched **5 of 216** real artifact
asks. Wrong by 40x. People do not say "spreadsheet", they say "give me the
breakdown". Also told them our precision evidence is near worthless (a
22-turn control is a smoke test) and corrected their premise: **SS never
ran their exercise.** Found a real hole in their plan: lane 0 ships day
one while their eval gates lane 1, so the first-shipping lane is
unmeasured, which is exactly our mistake. They reordered task 14.

---

## The open pairing (pick this up first)

**SS's sweep has not fired. Scott has not opened the app.** On his next
cold open of build 1443, SS predicts at GP's edge:

    ~60  project_id=  calls
     0   name=        calls
     0   repairs

**Report the real counts either way, including the boring one.** Any
`name=` traffic means their audit is over-asking and they want it from our
edge rather than from a user. Any repair is a FINDING, not a success.

How to check (both traps are live):

    container project-bifrost-app-1, /data/logs/proxy-host-4_access.log
    is the cz.shouldersurf.com file. CONTROL ON THAT FILE SPECIFICALLY.

⚠ A bare `grep resolve` matches `/v1/promo/resolve`, a live high-volume
unrelated route, and will show healthy traffic on a night when the sweep
sent nothing. Scope to `projects/` first.
⚠ `tail` on a `*access*.log` glob lands on whichever host sorts last and
returned timestamps OLDER than lines already seen. A stale file looks like
a quiet one. That mistake was made and caught tonight.

Now that #845 is live, the query strings of those calls WILL be visible in
`/webhooks/admin/live-log` (ring buffer, evicts, resets on deploy, so read
it promptly).

---

## Cross-team state

**SS** (`shouldersurf-f8`; `shouldersurf-09` ended mid-session). Owes GP
nothing. Shipped the card-title client half (`2bd4c09`), the resolve client
half (`4a38449`), `formatVersion` 2 as three states (`cb3238d`), and the
never-firing framing (`93a4a75`). Corrected themselves **three times**,
twice unprompted. Build 1443 on Scott's devices; `93a4a75` is NOT on them
(comments plus one log string), so observed behaviour is 1443's.

**`formatVersion` RULED and relayed**: bump to 2. GP proved its own half
first, by running the deployed parser at versions 1, 2 and 99: all parse
identically, `shareLanguage` resolves, `opens_on` picks the same rendition.
**GP carries the version, it does not judge it**, so no GP deploy was
needed. The 21 bundles already at version 1 stay ambiguous forever and
there is deliberately no backfill.

**N-400** (`n400helper-f6`). Tenant `n400` live. Their §7 answered; the
build work (interview_turn prompt config, then policy engine) waits on the
two Scott items above. ⚠ Their spend draws down the user's SHOULDERSURF
allowance: `record_cost` does `UPDATE users SET monthly_used_usd` with NO
app scoping. Live and unfixed; the generalized per-app budget gate in #841
is the fix. Also: no `config/remote/n400/` dir and no version floor, both
fail open. Their bespoke config names 404 loudly (safe); the 13 colliding
flat names would serve ShoulderSurf's content silently.

**CQ**: rule 8 was sent to them by SS; SS is chasing it, deliberately not
GP, so CQ does not get pinged twice.

---

## Method notes worth keeping

- **A control that does not prove the right thing is not a control.** Hit
  twice tonight: a `/var/log/nginx/` probe where the query AND its control
  both returned empty (wrong host entirely), and the `tail`-on-glob above.
- **Counts are not names.** A sabotage run reported "1 failed" three times
  with the FAILED lines eaten by ANSI colour. One failure is not evidence
  the RIGHT test failed; re-run with `--color=no` and read the names.
- **A mispointed grep says "mutation did not land" for one that did.**
  `if artifact == "title"` appears twice in translations.py; the first
  proof grepped a string that survived the mutation.
- **`60 is 60` is True.** An identity assert cannot distinguish a live
  import from a duplicated literal for small ints. Move the value and see
  if the other side follows.
- **A string assertion can pass while the wrong handler runs.** Asserting
  the forwarded path ends in `/resolve` survives a shadowing handler that
  builds the identical string. Assert the identity of what RAN.
- **A shadowing route can also bypass the ownership check**, not merely
  answer the wrong question. Found because the sabotage was realistic
  rather than minimal.

## Standing

Two `gh pr create` calls hit "already exists" this session; both times the
PR was mine and nothing was clobbered. A stale `gitStatus` in context also
showed this session's own commits as if they predated it. **Check before
concluding you overwrote someone.**
