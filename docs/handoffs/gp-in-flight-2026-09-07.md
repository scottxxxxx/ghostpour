# GP session close, 2026-09-07 (cloudzap-e1)

Continuation of the 2026-09-06 close, which ended mid-merge-chain. Prod =
main = `f62771d`. Zero PRs open. Served N-400 interviewer config **v27**,
verified by string on the container.

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
