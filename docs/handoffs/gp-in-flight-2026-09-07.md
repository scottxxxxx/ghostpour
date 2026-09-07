# GP session close, 2026-09-07 (cloudzap-e1)

Continuation of the 2026-09-06 close, which ended mid-merge-chain. Prod =
main = `52767f6`. Zero PRs open. Served N-400 interviewer config **v29**,
verified by string on the container.

⚠ This file was written at v27 and EXTENDED TWICE after. **Read "SESSION CLOSE" at the bottom first**; it is the current state and the owed list.

⚠ Written at v27 and extended at v29. The sections below in
the original order still stand; the v28/v29 work and the retractions are at
the end, under "After v27". Read that part first if you are resuming.

## Everything the previous handoff left open is closed

It handed over three PRs "in flight, verify on main, do not trust a merge
report". None of them had landed. All three are now on main, and each was
checked by reading the file rather than the merge output.

| PR | What | How it was verified |
|---|---|---|
| #926 | No ContextQuilt recall reaches the N-400 lane | Resolved the entitlement the way `chat.py` resolves it, against the SERVED config: the hook cannot fire at free, plus, pro, admin or automation |
| #924 | Config loader skips underscore-prefixed ops sidecars silently | Container rebooted 00:13:20Z on the new image; the warning is gone (0 lines) |
| #925 | The 09-06 session-close handoff | Present on main |

**The #926 probe carried a positive control and that is the only reason it
means anything.** "Disabled at every tier" is also exactly what a probe
pointed at nothing prints. The same code, in the same run, still reported
ShoulderSurf `enabled` and FIRING at plus and pro. Feature-set parity is
exact in both directions, so nothing went dark through the replace-not-merge
behaviour of the per-app matrix, and `n400/budget` is still `20.0`.

**#924 has the same shape.** "No warning in the log" is also what a container
that never rebooted prints. The check confirms the running image contains the
skip, then shows the same boot still logging both `config_drift_expected`
lines, so the silence is the fix working rather than the sidecar having
vanished.

### ⚠ One item on the previous handoff was FALSE

It carried, twice and with a warning marker, "OPS OWED: scoped
`sync-from-bundle` on `n400/entitlements` or the recall disable is not
served". **No sync was owed.** `n400/entitlements.json` is a WHOLLY NEW
slug, and `seed_remote_configs()` copies any bundled file whose overlay path
does not exist, per file, at startup. The deploy seeded it by itself and the
boot log carried `Seeded remote config from bundle: n400/entitlements.json`.

There are three cases, not two, and the third is the one that gets
over-served:

- a **value change** needs a manual scoped sync;
- an **addition** hydrates only if top-level or dict-keyed;
- a **wholly new file** needs neither.

This matters beyond a saved step. Acting on the owed-sync belief puts you one
typo away from the blanket sync that resets Scott's raised N-400 cap from 20
to the bundle floor of 5. The cheap check is to `ls` the overlay path inside
the container before assuming a sync is owed.

## N-400 v27: the lane stops saying it, rather than us catching it

PRs #927 and #928, both merged, deployed, synced and read back. The auditor
asked for three things and was explicit about the form: *"both of us have now
shipped guards on fields she cannot see, they worked, and she was harmed
anyway. If the choice is between a new boolean and a change to what the lane
is allowed to say, take the second."* All three are prompt rules. No new
response field.

**1. `opening_questions`, the client's set.** The lane named its own four
before-we-begin questions in prompt text. Three states, and keeping them
distinguishable is the whole design: lines are the set exactly,
`[no opening questions]` means ask none, an EMPTY block means an older client
and the lane falls back to its four.

⚠ The marker is load-bearing rather than stylistic. `prompt_assembly` blanks
an absent declared-optional placeholder, so **a client that never sends the
field and one that sends an empty string assemble to byte-identical
prompts**. Without a distinct token for "none", "ask nothing" and "ask the
four you already know" are the same observation. A test pins it from both
ends: the marker must differ from absent, and an empty string must NOT, so
the reason survives as something that fails rather than as a comment.

**2. Closing language gated on the agenda.** ⚠ **This is not what #922
already does, and assuming it was would have left the harm live.**
`clear_interview_over_while_agenda_open` refuses the `interview_over` FLAG.
The auditor's measurement across 2212 turns in 52 runs found 12 fires, 10
genuine, and **eight of the ten spoke a closing SENTENCE, while the worst run
in the project never sets the flag at all.** A guard that clears a boolean
cannot un-say a sentence she has already heard.

`[agenda empty]` was read off our own wire rather than invented: 2359 logged
`n400_interviewer_turn` requests, the AGENDA block parsed out of the
assembled provider request in every one, 95 carrying exactly that literal and
the other 2264 carrying 1 to 8 node lines. A test asserts the string so a
client-side rename surfaces here instead of the rule quietly addressing a
token nobody sends.

**3. Claimed coverage limited to this turn's `facts` array.** The
`conf-es-full-1` turn 85 defect: one blanket yes reported as covering five
oath clauses while one of six minted. Written as mint-them-then-claim-them,
not as "never claim more than one", because a blanket yes genuinely can
answer several clauses.

**Ask 3 is not built** and the auditor asked that it not be revisited. A
response field naming which node the lane believes it satisfied; the output
schema is closed at 7 fields by Scott's 2026-09-02 ruling.

### ⚠⚠ #928: the closing rule banned the section checkpoint. Caught before it served.

The v27 draft said the reply may not imply "the interview, THE SECTIONS, or
the form" are complete while the agenda is non-empty. A section checkpoint
says ONE part is done and ends on its confirmation question, deliberately
with a non-empty agenda, and it is the mechanism the entire read-back walk is
built on. `conf-es-full-1` **t64** is the real case: Part 8 confirmed with
"esta todo completo y correcto?", closing flags set, agenda non-empty,
entirely legitimate. **Under my rule that turn was illegal.** The rule would
have broken the mechanism it was written to protect.

**Nothing on the writing side could have found it.** The prompt was
internally coherent. Every test was green. The sabotage pass had already
confirmed each of those tests could fail on the bug it was written for: eight
mutations, each verified to have reached the file, each turning exactly the
predicted tests red, caches cleared between runs. From inside the work it
looked finished.

What found it was the auditor reading the literal rule text against a
specific logged turn and asking whether it made that turn illegal. **Sabotage
proves a test detects the defect it was written for; it says nothing about a
defect you did not conceive of, and an over-broad prohibition is invisible to
a suite built from the same conception as the rule.**

The fix draws the line between ONE NUMBERED PART and THE WHOLE THING and
gives it an operational form, because a distinction the model has to feel is
one it will apply wrongly: *if you cannot name the one part number your
sentence is closing, you are closing the interview.* The test pins the
over-broad draft as FORBIDDEN as well as the carve-out as present, because
the regression here is a plausible-looking tightening, not a deletion.

## Verification practice, three failures worth carrying

All three share one shape: **the check reported success while measuring
nothing.**

1. **`docker exec` without `-i` silently discards a heredoc.** A whole Python
   verification block printed nothing and exited 0. An empty section under a
   confident heading reads as a clean pass.
2. **There is no `curl` in the ghostpour container.** The first sync attempt
   died on `curl: not found` and wrote nothing. The only reason it was not
   mistaken for a completed sync is that the read-back ran anyway and said
   NOT SERVING. Use `urllib` inside `docker exec -i`, reading `CZ_ADMIN_KEY`
   inside the container so it never crosses the shell.
3. **Negative assertions pad a verification.** When the sync had done
   nothing, two lines still reported OK: "opening_questions NOT required" and
   "over-broad draft wording GONE". Both are true of v26 and of an empty
   string. An absence check cannot distinguish the new version from the old
   one. Split PRESENT from ABSENT and let only the presence block decide the
   verdict.

## Cross-team

- **fable-auditor-55.** Owes nothing blocking, and has been told v27 is
  serving. They are re-running the premature-close measurement against
  post-deploy traffic and will give a fire rate as a number; if it holds near
  0.54% the prompt rule is not landing. Their instrument is now in
  `qa/audit.py` mirroring the Swift detector line for line. They are
  sabotaging their own premature-close tests before claiming the counter is
  trustworthy, prompted by the finding below. They are pinning
  `[agenda empty]` and `[no opening questions]` from the client end.
  ⚠ They are taking the read-back derivation and are explicitly NOT building
  a fourth field guard.
- **shouldersurf-e7.** Closed both open threads. The Spanish search turn was
  TYPED, relayed and acknowledged, no product bug, nothing owed either way.
  They then MEASURED the contextual-biasing lead and it is a **negative**:
  `SpeechTranscriber` ignores `AnalysisContext.contextualStrings` entirely,
  byte-identical output with and without, while `DictationTranscriber`
  through the same harness and audio picks up all three seeded names. It
  holds across presets and across en-US and es-ES, and the analyzer echoes
  the strings back from `analyzer.context`, so the context demonstrably
  arrives and is ignored. Harness at `scripts/bias-probe/` (their commit
  `ce8d68c`), meant to be re-run per iOS release. **Consequence for Scott:**
  wiring a roster into a context on the `SpeechTranscriber` path is provably
  zero-effect work that would have shipped looking like success. The fallback
  is switching to `DictationTranscriber`, an older engine, which is a real
  trade and a separate decision.

## ⚠ Waiting on Scott

1. **`com.weirtech.n400helper` is NOT in `CZ_APPLE_BUNDLE_ID`.** The prod
   value is exactly `com.shouldersurf.ShoulderSurf,com.weirtech.techrehearsal`.
   Every real identity token from the N-400 build will 401 on the audience
   check. Scott chose to make that edit himself rather than have me do it;
   the auditor has been told, and knows not to debug the 401 as a build
   problem. Appending keeps ShoulderSurf first, so the paths that use the
   FIRST entry (the App Store Server API `bid` claim, SIWA revocation) are
   unaffected.
2. The attorney question on how much the product may shape what an applicant
   attests to. Concretely: whether "yes to all" with one clause unexplained
   should record five answers or none, and where stating a true published
   fact tips into suggesting an answer.
3. The nominal-fee condition against commercial pricing. No code touches it.
4. The N-400 spend cap, still $20.
5. Whether ShoulderSurf's per-send web search toggle should reset after every
   send.

## Next

**Read-back derivation is still the priority and the auditor is acting on
it.** Coordinate rather than build in parallel: they were asked to say what
they want from the server side. The standing argument is unchanged, that
every guard on this lane governs a field she cannot see while she has been
harmed three times by a sentence she can, and where the sentence and the card
disagree the card wins.

Also still open on this lane: nothing pins that GP emits only `str:'yes'` /
`str:'no'` for `p9.oath_disability`, which the client's document gate holds
on via a string comparison rather than a flag.

## Housekeeping

The memory index was compacted from 23.8KB to 18.1KB. It was approaching the
read limit that would have truncated it for future sessions. Nothing was
dropped; entries were merged onto shared lines and superseded handoff detail
was folded into one row.

---

# After v27: v28, v29, two retracted counts, and a shipping blocker

Everything below happened after the section above was written. Prod = main =
`d43388e`, served config **v29**, verified by string. Zero PRs open.

## The auditor's v27 result: zero

`conf-es-v27`, 105 turns, same persona and language as the run that closed
with five of six oath fields blank. **PREMATURE CLOSE: 0**, against 3 fires
on that persona before and 12 across the whole v26 era. All six oath fields
recorded. The read-back walk covered Parts 1 to 13.

Their caveat, kept because it is the honest reading: one run, and zero out of
105 is consistent both with the rule working and with a run that did not
reach the shape. What raises confidence is that the specific turns that fired
last time have visibly different content now, not the count.

**Turn 103 is the one to show Scott.** The lane caught its own gap
unprompted, said Parts 12 and 13 still needed reading, read them, and only
then closed on an empty agenda. That is the closing rule and the
section-checkpoint carve-out working together, and the carve-out is the half
that would have been gone if the first draft had shipped.

`opening_questions` worked on its first live run: turn 1 opened on
eligibility, and language, interpreter and filing-for-self were never asked.

## v28 (#930): a deferral she is never told about

Of 225 turns that deferred something, the auditor measured 24 saying nothing
at all. Two clusters: `p4.prior_address1` read back as a settled date range,
and `p7.employer2` skipped past entirely.

⚠ **Half this rule was already in the prompt and had been since v1.** "A
promise with no entry is a defect" guards reply to deferred. The direction
that harms her, deferred to reply, was never stated. **A consistency rule
written in one direction reads as complete**, and re-reading does not reveal
the gap because the sentence you are reading is true. Both directions are now
adjacent, and a test asserts the ORIGINAL half was not replaced by its mirror,
because the realistic regression is tidying the pair down to the newer one.

Also v28: a part is not complete while a field in it is deferred, written so
the checkpoint still happens and only the CLAIM is constrained.

## v29 (#931): the same rule was written for ONE field

v28 said the spoken line must tell her "that one" is not settled. Singular,
with only single-field examples. **A reply that hedges one deferred field and
states another flat satisfies it**, and that is how it actually fails:

    conf-es-v27 t31  "Noted, zip code to verify, and since June 2020."
    conf-v21    t31  "I've noted the ZIP to check from your mail, and June
                      2020 for when you moved in."
    conf-v22    t32  "I've marked the ZIP to verify from your mail, and
                      noted June 2020 for when you moved in."

The ZIP is hedged. June 2020 is also deferred and is stated flat. Every one
sounds careful. v29 requires attachment to EVERY deferred field by name, with
an operational step: read your own `deferred` array back before speaking.

**The auditor was not auditing my rule.** They were trying to bound their own
count from above and found their probe asked whether a hedge appears ANYWHERE
rather than whether it attaches to the deferred field. My rule had the
identical hole, in prose instead of regex.

## ⚠⚠ BOTH counts were retracted within the hour

GP published 39 of 252. A second GP probe an hour later said 99. **Two of my
own probes disagreeing 2.5x means at least one is wrong and neither can be
quoted.** The auditor then retracted their 24 of 225: reading all 24, two
were false positives, and the probe cannot bound from above at all.

The defensible statement is a FLOOR: at least 22 of 225, higher by an
unmeasured amount.

⚠ **The retracted rate was inside the SERVED PROMPT**, quoted at the model
every turn as established. Fixed in v29 with a test pinning that it cannot
come back. **Evidence embedded in a prompt is a claim with a maintenance
cost, and neither team can see that text at runtime.**

**What survives is what was established by READING TURNS**: the two clusters,
the completion claim over a field deferred in the same turn, and the
self-employed dead end. Not one came from a count.

## ⚠ The evidence for v28/v29 is ASYMMETRIC and that is written down

The opposite direction, guarded since v1, is also violated at a raw count
higher than the one we fixed. It is NOT reported as a finding because reading
the samples shows the probe cannot support it. One direction measured, one
known non-clean and unmeasured, a rule shipped for both. The rule is right
either way.

## ⚠ Verifying it SHIPPED is not verifying it WORKS

The string read-back is solid and it closes the did-it-ship gap. It does not
verify the lane OBEYS the rule. The auditor built a per-field attachment
probe, validated it against a labelled set, got roughly 40% false positives
on known-good turns, and **killed it before publishing a number**. Clause
scoping cannot tell "October 2017 to June 2020, I've noted the exact days to
verify later" from stating it flat; sentence scoping fails the other way.

Neither team currently believes attachment can be measured by pattern. Their
method for the post-v29 number is hand reading, with a persona built to
manufacture partially answered fields. GP offered an LLM-judge option with
its hazard attached (never give the judge the rule text, or it reads our own
instruction back to itself) and is NOT building it unasked.

## Three methods, counted honestly

- Enumerating adjacent cases before shipping: both teams did it, **caught
  nothing**.
- Reading a rule's literal text against a NAMED REAL TURN: **caught all six
  findings** across both teams. Not "does this sound right". A turn id.
- Structurally present and actually occurring are **different questions**.
  The auditor found a hole in their own detector, measured that it does not
  occur in 2317 turns, measured that the obvious fix added 3 false positives
  for zero catches, and ended at do nothing, with the numbers in the source.

## Practices worth keeping

- **A rejected refinement belongs in the source WITH its numbers.** The
  rejected fix is usually the obvious one and the next person will have the
  idea.
- **Constrain a count with a second count** derivable a DIFFERENT way. ⚠ A
  partition that SUMS is not such a check; it validates bookkeeping, not
  classification.
- **Derive a verdict label from the data.** A read-back printed "v27 IS
  SERVING" on a correct v28 run, one line under "version is 28 OK", because
  the summary was a hardcoded literal. A summary that restates a constant is
  not a reading.
- ⚠ `docker exec` without `-i` silently discards a heredoc. There is **no
  curl** in the ghostpour container.

---

# The boundary session: four collisions between GP's guards and the client's

Everything below came from one exchange with the N-400 auditor after v29 was
serving. None of it was planned. All four were found the same way, and the
way is the finding: **two correct-looking readings of the same wire
disagreed, and somebody chased the difference instead of reconciling it.**

## 1. Opposite floors for the same collision

The lane sometimes emits one field as BOTH a fact and a deferral (18 times in
2555 live turns, most recent that day). GP's `drop_facts_that_are_also_deferred`
keeps the deferral and drops the fact. The client's `deferralConflict` kept
the FACT and refused the deferral. **Neither team knew the other's rule
existed.** Both were written from a real case and correct for it.

Cost, measured on their side: 12 turns through, **three invented days** in run
records (`2017-10-01` from "October twenty seventeen", `2018-01-01` from "from
2018 to 2021", which invents a month AND a day), plus six partials kept as
complete. In every one the client kept the value, refused the deferral, and
there was no open finding, so the gate would not block and she would never be
asked again. **QA personas, no real applicant.** The mechanism was live.

Resolved: they inverted to match. **The tiebreaker generalises better than the
direction: the two errors are not comparable, so the tie goes to ASKING.** A
spurious deferral costs one question; a spurious fact stands forever.

## 2. ⚠ A correct guard disarmed a correct safety valve

Found by sending them every GP guard that MUTATES the wire and asking which
they hold a rule on. `clear_interview_over_while_agenda_open` sets
`interview_over` false when the lane closes with the agenda open. Their client
holds the interview open until the read-back covers every part, with a STALL
BREAKER that closes anyway after three refusals so an applicant cannot be
trapped. It counted `interviewOver == true`. **GP cleared the flag first, so
the escape hatch was dead code in production, disarmed exactly where it was
needed.**

Measured: lane set `interview_over = true` 48 times, guard fires on 8, across
6 cases. **Case `67fc8f1f` hit the threshold exactly** (three clears in two
minutes, all with `q_p9_oath` the single open node).

The signal already existed: `interview_over_cleared` ({open_nodes, reason}),
set in the same mutation, surviving the full pipeline. They had grepped their
client for a reader, found none, and inferred it did not exist.

**Two guards that are each correct and resolve the same direction can still
combine badly, because one can remove the INPUT the other needs. Agreement on
direction is not enough; each side must know what the other CONSUMES.**

## 3. ⚠ GP silently drops `confirmed` and `confirmed_components`

Found by asking them to list every key they send. Two of fifteen never reach
the model: not declared, not in the template, no code path in `app/`. **Silent
by construction**: the assembler does `replace("{{name}}", str(value))`, so
with no placeholder the replace is a no-op, and the unreplaced-placeholder
warning only fires on braces that SURVIVE.

LATENT, not live, on three independent grounds they supplied: zero of 2845
recorded requests carry either key, the QA harness never builds them, and
**the app has never reached the live lane at all because the bundle is still
absent from `CZ_APPLE_BUNDLE_ID`.** So every recorded turn is the harness.

## 4. ⚠⚠ Two exemptions that would have removed EVERY floor

Designing the fix for 3 nearly created the worst defect of the set. Three of
GP's six guards key off the CURRENT utterance, and a confirmed resubmit is
definitionally a turn whose evidence lives in an earlier one, so GP proposed
exempting the declared field ids. Unknown to GP, **the client already exempted
confirmed turns from EVERY floor for ANY field**, which means GP's guards are
currently the only floor on that turn.

**Together those two exemptions leave the confirmed components with no floor
on either side**, on the one turn whose purpose is writing settled values to a
federal form. Caught before shipping only because a question asked for another
purpose surfaced the client's half.

⚠ Their narrowing must land REGARDLESS of the probe: a blanket exemption is a
hole on its own. GP's is conditional.

**Next step is one probe, not a design.** `user_content` on a confirmed
resubmit is the CANONICAL component text, not her approval, so whether the
evidence floor passes depends entirely on what provenance the lane attaches,
and nobody knows because the path has never run. Read off one raw response:
`facts[].provenance.utterance`, `facts[].field_id` vs the declared set,
`deferred`, `facts_dropped`, `intent`.

⚠ **Do not ship the confirmed path with a fabricated APPLICANT line.** Because
`user_content` is the canonical values, the `conversation` window would record
`"Torres Ramirez, Ana, Lucia"` as a thing she said. The system prompt states
twice that those lines are her words, and the evidence floor and non-answer
marker are BUILT on that promise. It is a false premise under three guards,
not merely a fidelity problem.

## The instruments that actually worked

- **COULD THIS COLUMN HAVE SHOWN ME THE FAILURE IF IT HAD HAPPENED?** Their
  form, and the best of the session. Three instances that day where a clean
  result was structurally incapable of being anything else: `raw_response`
  logged PRE-guard, a probe counting deferral markers as answers, and the
  assembled request logged POST-drop.
- **Structurally present and actually occurring are different questions.**
  Find it, measure whether it occurs, measure the fix, and be willing to end
  at do nothing.
- **A tiebreaker inherits the cost model of the cases it came from**, and that
  model is usually written nowhere. "The tie goes to asking" came from VALUE
  collisions where asking is cheap; it fails on a TERMINATION collision where
  asking can be unbounded. **Write down what the case ASSUMED, not only what
  it showed.**
- **A scope error is not an instrument error.** A grep over the client proves
  a fact about the client. Reading it as a fact about the protocol is one
  category too wide. Tell: you are about to assert an ABSENCE, or to ask
  another team to BUILD something that may already be on a wire you recorded.

---

# SESSION CLOSE. Read this first.

**prod = main = `dca59e5`. Zero PRs open. N-400 config v29 SERVING. 15 PRs
merged (#924 to #938). Working tree clean.**

## ✅ Plus file-generation cap, CLOSED the same session

Found by shouldersurfsocial-08 fact-checking the served catalog, ruled by
Scott, shipped and synced. **Nothing owed here; this is recorded so nobody
re-raises it.**

`feature_definitions.generation.generations_per_month` was **null on Plus,
and null means UNCAPPED**, confirmed in the enforcer docstring ("Absent block
or null value = uncapped") AND in `chat.py`'s `if _gen_cap is not None`
branch, which skips the check entirely. So Plus at $9.99 had unlimited file
generation while Pro at $14.99 was capped at 100.

⚠ **It was not a typo.** The tier sheet's proposal row for Plus reads just
`yes` with no number ("Scott sketch: Plus and Pro. Today Pro only; moving to
Plus is one config line"). Plus was ENABLED WITHOUT EVER BEING GIVEN A CAP,
the config carried null, and null happens to mean uncapped. Scott supplied
the missing number: **50**.

Shipped in #938 across all four locale slugs, then a SCOPED sync per slug and
a read-back of the served value AND of the enforcer's return:

    tiers    v60 -> v61   tiers.es/.fr/.ja  v59 -> v60
    served: free=5  plus=50  pro=100  in all four
    enforcer: free 5, plus 50, pro 100, automation 100

Two pinning tests hardcoded `None` and were updated with the ruling attached,
the same shape the matrix test already uses for the Pro `project_chat`
doubling that overrode the sheet. Pinned as a NUMBER so it cannot silently
return to null.

### ✅ Refetch verified 2026-09-07, from OUTSIDE the container

The one item the close left unverified: whether a device holding the OLD
catalog actually pulls the new one. It does. Probed over the public host
`https://cz.shouldersurf.com`, which matters because every earlier read-back
was taken inside the container and so could not see the proxy or the route's
short-circuit.

    X-Config-Version: 60  ->  200, full 19,567-byte payload, X-Config-Version: 61
    X-Config-Version: 61  ->  200, {"changed": false, "version": 61}

    served now:  tiers v61 (en), tiers.es / .fr / .ja v60
    all four:    free=5  plus=50  pro=100  automation=100

Both halves matter and only one of them is the presence check: serving the
body to a stale client is the claim, and the short-circuit at the current
version only proves the route still discriminates. A pass on the second
alone would be true of the pre-fix version too.

⚠⚠ **The two tier endpoints answer the version header differently, and
this already cost a peer team a probe.** Social ran the same check against
`/v1/tiers`, correctly saw no short-circuit at any version, and was about to
report a broken optimization. Both readings were right; they were different
routes.

    /v1/config/tiers  v=60 -> 19,567 bytes   v=61/62 -> 30 bytes
    /v1/tiers         v=60/61/62 -> 21,474 bytes, every time

`list_tiers` (`app/routers/chat.py:1381`) never reads `X-Config-Version`;
the short-circuit is only in `get_config` (`app/routers/config.py:548`). The
byte count and the key list tell them apart: 21.5KB with NO `upgrade_nudges`
is `/v1/tiers`, 19.5KB with it is `/v1/config/tiers`.

⭐ Follow the consequence one step, because it inverts a rule stated above.
"A value change without a version move is invisible to every device" is true
of `/v1/config/{slug}` and FALSE of `/v1/tiers`, which reassembles from
`app.state` on every request. `/v1/tiers` is the endpoint the app actually
calls, 248 to 1 on the edge log. So an unbumped value change is live to users
immediately on the surface they see, while the config-cache path still serves
the old document, and no version number anywhere records that the two
disagree. See [[reference_two_tier_surfaces]].

⚠ **Bundle `version` deliberately untouched.** Bundle is 3 behind the overlay
on every tiers slug (57/56/56/56 against 60/59/59/59), pre-existing. Had it
been bumped AND `/version` included in the sync keys, the overlay would have
landed at bundle+1, which is 58 against an overlay at 60: **a version going
backwards**. Reconciling that gap is a separate job.

⚠ Standing facts confirmed while in there, worth not re-deriving: **there is
no price key anywhere in `/v1/tiers`** and that is deliberate (GP is canonical
for FEATURES AND LIMITS, the App Store for MONEY). And **four surfaces, one
enforced**: `features`, `feature_bullets` and `feature_items` are DISPLAY;
`feature_definitions` plus the entitlement matrix is the pair that gates.

## ⚠⚠ Owed by Scott, in the order they bite

1. **Append `com.weirtech.n400helper` to `CZ_APPLE_BUNDLE_ID`.** Prod reads
   exactly `com.shouldersurf.ShoulderSurf,com.weirtech.techrehearsal`. He
   chose to make this edit himself. **This is now why the N-400 app has NEVER
   reached the live lane in ANY environment**, not just why SIWA 401s: every
   turn ever logged is the auditor's harness. Nothing else blocks them.
2. **The self-employment form ruling.** A required field with genuinely no
   value has no channel; both routes GP's prompt names terminate on the
   client's export gate. See [[project_n400_required_field_no_value_deadend]].
   The auditor's ask, not GP's, deliberately so he gets one framing.
3. **Compound given names.** "Ana Lucia" may be one given name or two, and
   nothing in the string or on the green card distinguishes it. The auditor's
   ruling to propose: THE LANE SHOULD ASK rather than split. Measured: 5 of 60
   named cases are four-word, 1 of 4 same-shape kept compound, and there is no
   corpus outside their slice. Thin evidence; rule on the principle.
4. **ShoulderSurf:** the People roster cleanup call (the `listening` flag is
   FORWARD ONLY, so Leo's 15 appearances survive the fix), and how the app
   decides a recording was listened to rather than taken part in.
5. Attorney question, nominal-fee condition, $20 N-400 cap.
6. ⚠ **The Mac data volume hit 100%** mid-session and broke tooling in two
   sessions. 8.8Gi free on 1.8Ti.

## ⚠ Two version flips that MUST land at App Store approval

SS 1.17 build 1659 is in review with **NO PHASED RELEASE**, so approval is a
step to the whole install base at once. shouldersurf-e7 will send
"approval is live, please move the appstore channel AND the latest fallback".

- `latest_by_channel.appstore` is pre-staged 1.0/803. Correct today and
  cannot fire a false toast by construction. Becomes 1.17/1659.
- ⚠ **The `latest` FALLBACK block is 1.15/836 with a TESTFLIGHT INVITE as its
  `upgrade_url`.** After release a client that omits `X-App-Distribution`
  would be pointed at a TestFlight join page. Reads to a real user as a
  broken update prompt and would be diagnosed from the wrong end.

`latest_by_channel.testflight` is already 1.17/1659, deployed and read back
off the container. Scott's device on 1660 sees no toast; 1618 and 836 do,
dismissible, nothing gated.

## Watches armed

- **Bifrost 429s, threshold ONE.** Baseline is 0 across four weeks of
  proxy-host access logs, and that zero is trustworthy because the same
  pattern yields 405x269 / 204x96 / 404x69. ⚠ The status sits after `] - ` in
  the NPM log format; a pattern expecting `] ` returns zero for everything.
- **First `POST /v1/people`.** Zero in four weeks, every people call at the
  edge is a GET. So the first POST is the signal with no threshold to argue.

## N-400: what happens next

The auditor (fable-auditor-55) DIRECTS this lane. Their queue: split the slice
into name and address (they are 22 turns apart), and build the harness key for
the confirmed-resubmit probe.

⚠ **Do not build the confirmed-resubmit path until that probe runs.** The path
is unexercised end to end. GP and the client were BOTH about to add exemptions
that together would have left it with NO FLOOR ON EITHER SIDE. The client has
since narrowed unconditionally; GP builds nothing until the probe says what
provenance the lane attaches. And do not ship it with a fabricated APPLICANT
line: the prompt tells the lane twice that those lines are her words, and three
guards are built on that being true.

## The one thing worth carrying into any project

**Nothing this session was found by reading our own code first.** Six or seven
real defects across two codebases, every one surfaced from the other side. The
mechanism that produced them is a two-part obligation, and neither half works
alone:

- **Sender:** describe your own work as a DEFECT WITH A RECEIPT, not a design
  improvement. "Widening the evidence floor to handle clarifications" would
  have made nobody look. "My guard deleted the applicant's name, here is the
  turn id" made them open their own floor and find the identical defect.
- **Receiver:** check whether you have THE SAME GUARD. Not the same rule.

Full write-up in [[feedback_found_by_the_other_team]]. The instruments that
actually caught things: [[feedback_constrain_a_count_with_a_second_count]],
[[feedback_two_teams_opposite_floors]],
[[feedback_peer_reads_the_rule_against_a_real_case]].
