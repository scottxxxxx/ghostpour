# GP session close, 2026-09-06

**prod = main = `ff3bd7f`.** Merged and deployed tonight: #900, #902, #904,
#905, #906, #907, #909, #910. Served N-400 config is v25, verified by
reading the served copy back by string.

**THREE PRs ARE OPEN, GREEN, AND DELIBERATELY UNMERGED:**
- **#911** the checkpoint guard regression fix. ⚠ #909 shipped a guard
  that fires on a STALE agenda; it produced 8 false positives in 11 and
  the outcome inverts (it drops the correct claims and keeps the wrong
  ones). Read the #911 section below before touching the lane.
- **#908** the drift signal-to-noise fix, plus an ops step after merge.
- **#903** the generated-files purge invariant + a flaky-test fix.

⚠ **#903 WAS REPORTED MERGED DURING THE SESSION AND WAS NOT.** The merge
was refused (branch behind), rebased, and never retried, and "merged"
was then carried forward in every later summary. Verified against main
rather than against the report. The lesson is in
`feedback_right_answer_wrong_question`: the question answered was "did I
run the merge", not "is it on main". **A CI flake in
`test_serve_endpoint_auth_ownership_expiry` therefore STILL EXISTS on
main** and may waste time until #903 lands.

## What needs Scott

1. **APNs auth key.** Developer portal, Keys, enable Apple Push
   Notifications service. He keeps the `.p8` on his Mac and gives the Key
   ID. ⚠ The key file must NOT travel through a peer socket or relay. GP
   base64s it into Secret Manager as `apns-private-key-b64` with
   `CZ_APNS_KEY_ID` and `CZ_APNS_TEAM_ID` (F22KGHDYAE). The build is
   DORMANT while the key is blank, so #900 is live and inert.
2. **The N-400 flat cap is still $20**, raised on his word 2026-09-05 as a
   temporary measure. Lower it when he says.
3. **#911 first**, then **#908**, then **#903**. All green, all held on
   purpose so they land against a rested reading rather than at the end of
   the longest session this project has run. #911 fixes a live regression
   and should go first. #908 is the drift signal-to-noise fix. After merging
   it, ops must write the sidecar `_intentional_overrides.json` in
   `/app/data/remote-config/` for the two deliberate overrides
   (`n400/budget` `/monthly_cost_limit_usd`, `client-config`
   `/share/_note`). Until then behaviour is identical to today.
4. **The nginx edge retry is DESCOPED.** It was raised as needed and then
   withdrawn when the code fix took most of the window. Do not action it
   without re-measuring.

## The deploy window: solved, with the number that matters

Every deploy answered 502 at the edge for **17 to 31 seconds, highly
variable**. Found while chasing two 502s the N-400 auditor hit mid-run;
they were ours.

**Cause: the cold migration sweep**, 155 statements doing random metadata
access across a 190MB database on an HDD-backed disk. 15.09s of a 17.31s
boot. #907 fingerprint-gates it: **now `path=skipped ran=0`, 0.01s**,
proven on a real cold boot.

**Honest totals: lifespan 17.31 -> 9.27s, end-to-end 30.8 -> 20.3s.** An
earlier 2.55s reading was a warm boot and was flagged as such rather than
quoted as the result.

⚠ **`retention_purge` INHERITED the bill: 0.02 -> 6.69s**, nothing about
it changed. See `feedback_instruments_examine_representations` in memory:
a measured cost belongs to the ORDER, not the component, so every phase
timing in that log is provisional.

**Next, in size order, NOT started:** ~11s before uvicorn's process starts
(container + interpreter, and the only figure immune to the reordering
effect); the 6.69s purge (tractable, `_retention_sweep_loop` already runs
it hourly, so the boot pass can go behind the bind). ⚠ A prediction is on
record: moving the purge behind the bind cuts pre-bind time by NOTICEABLY
LESS than 6.69s because the next phase inherits the cold cache. Check it.

Also unbuilt, one line: log `GIT_SHA` at startup. The boot log currently
cannot tell a deploy from a restart. `/health` can (`git_sha` +
`uptime_seconds`), which is how the auditor's harness does it.

## N-400 lane: v25 serves

#904 (v25) plus #909. Guards proven LIVE inside the running container, not
just in CI: three refusal cases fire, and a legitimate checkpoint is NOT
refused, which is the half that proves it discriminates.

- **`section_checkpoint.part`** now names the part the reply reads. v24
  already said so, but 3000 chars from the field it governed while the
  field's own definition was silent on which part.
- **`defer_dates_with_unspoken_day`**: a YYYY-MM-DD fact whose day is not
  in the utterance becomes the deferral the prompt always prescribed.
  Turn 35 asserted 2017-10-19 from "around October 2017". Day words in
  EN+ES including apocopated and multi-word compounds (21..31 generated,
  not typed: "treinta y uno" is three tokens and was missed first pass).
- **`mark_facts_minted_on_a_non_answer`**: MARKS, does not drop. Designed
  as a dropper; the auditor measured it against nine runs before it
  shipped, 13 of 13 would have been wrong drops. A weaker dropper is a
  pure no-op because the evidence floor gets there first.
- **`normalize_reply_shape`**: a bare-string `reply` becomes `{"en": ...}`
  at the gateway. It crashed a GP guard and, worse, was a NON-RETRYABLE
  terminal error on the client.
- **#909 refusals**: a `section_checkpoint` is refused when the agenda
  still lists that part (turn 79) or when KNOWN FACTS holds no confirmed
  fact for it (turn 104). Refused and RETRIED once, because the harm is in
  the reply she hears; dropped only if the retry fails. `interview_over`
  cannot be set while a node is open. Marker on the wire:
  `checkpoint_refused: {part, code, reason, retried, resolved}`.

⚠ **STILL OPEN, the auditor's deeper point:** the read-back text is
NARRATED from what the model believes, not DERIVED from what is on file.
#909 constrains WHICH parts may be read back, not what the sentences
claim. Field-level derivation is next, and the constraint on it is that a
bare list of values is not a person checking her answers with her.
`known_facts` is complete and authoritative (confirmed from both clients
AND by reading a real request off `usage_log`); "on file", the
confirmed-empty marker and both deferral forms count as RECORDED, only
"(mentioned earlier, not yet confirmed)" is ineligible.

⚠ **The opening-question slip is the CLIENT's fix, agreed.** Five
instances, four wordings, three configs, two languages. Prompt work was
retired on that behaviour by prior agreement; the client will stop SENDING
opening questions its own context answers.

## How to work, learned tonight

- **`feedback_right_answer_wrong_question`** is the through-line: six
  instances, each a correct answer to a slightly wrong question, none of
  which looked wrong. Includes the sixth, committed by the auditor within
  an hour of naming the pattern, which is the entry that shows the rule
  does not inoculate you.
- **`feedback_verify_served_copy_by_string`**: guards ship in the IMAGE,
  prompts in the CONFIG. `curl` is NOT in the image and a sync silently
  no-opped, leaving a v24 prompt against v25 guards. The drift warning
  fired correctly and was read as part of a familiar block.
- **`feedback_sabotage_revert_trap`** 6th instance. And a no-op sabotage
  (`pass` before a `return`) read as a robust suite.
- **`feedback_invariant_held_by_one_function`**: instrument a flake, do
  not rerun it.

## Auditor state at close

fable-auditor-55 had both runs open against `55ca0cc`, English about
halfway, Spanish queued. Six agreed reads, oath first: does one yes still
mint all six oath fields (separates a fixed behaviour from a silenced
symptom); are any checkpoints refused and with which code; does the
read-back still restart after Part 13; is `interview_over` ever set; does
`p4.current_address.state` mint from "Dallas, Texas" (a clean
before-picture exists in `usage_log`); does turn 19's shape recur.
