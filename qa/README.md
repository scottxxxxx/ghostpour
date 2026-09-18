# Hand-made N-400 evaluation artifacts

Everything in this directory was made by hand or hand-adjudicated. **None of it
is regenerable at any price.** A model run that dies can be re-run for a couple
of dollars; a person's reading of 42 interview turns cannot.

It is here because it was living in `/tmp`. On 2026-09-11 the deploy for #964
recreated the container and wiped container `/tmp`, taking `ste_full.json` (252
generations, about $4 of API calls) with it. The files below were on the VM
HOST `/tmp`, which survives a container recreate but not a VM reboot and not a
tmpfiles sweep. That is not a place to keep something you cannot rebuild.

## What each file is

| file | what it is |
| --- | --- |
| `labelled-deferral-turns-rev2-frozen.json` | **Revision 2**, the FROZEN historical baseline. 42 turns, 70 deferred-field instances, labelled by fable-auditor-55 on 2026-09-06. Counts: told 16, not_told 25, 1 ambiguous excluded. |
| `ste-label-pack.json` | The 30-item blind pack: 15 judge-vs-baseline disagreement turns across BOTH prompt arms, shuffled, one rep each. Carries no variant label and no judge verdict. |
| `ste-label-unblinding-key.json` | The key that un-blinds that pack. Was `ste-label-key-GP-ONLY.json`. |
| `n400-deferral-ste-inputs.json` | The 42 verbatim lane requests, so a prompt A/B REGENERATES replies rather than re-judging stored ones. |
| `judge.py` | The per-field LLM judge, validated at 41/41. Reads its credential from settings at call time and contains none. |
| `ste_run.py` | The STE A/B generator as it ran on 2026-09-11: v29 served vs v29 with the DEFERRALS line swapped, called straight to the Anthropic API. |
| `ste_judge.py` | Runs the validated judge's SYSTEM and QUESTION over `ste_run.py` output, per field, with deferral counts beside the rate. |
| `thinking-ab-preregistration.md` | The thinking off vs on design for the interviewer lane, written 2026-09-14 BEFORE any generation. No gate: arm A is the baseline. Runs after v32. |
| `ste_analyze.py` | The A/B report, gate first. Imports `deferral_disclosure.py`, the mechanical scorer, which lives with the auditor in `N400 App/qa/`, not here. |

## The STE harness is a RECORD, not a tool you can point at a new question

Moved in on 2026-09-14 from a GP session scratchpad on the Mac, byte-identical
to those copies (sha256 `f315399c` run, `c4926b2c` judge, `4a8e10e7` analyze).
⚠ **Those copies are from 2026-09-11 13:18.** The VM host `/tmp` copies ran
later and may have been edited after; nobody has compared them. If the VM
copies still exist, diff them against these before trusting either as "the"
harness.

Running them for anything but the STE question gives wrong answers
without an error:

- `ste_run.py` swaps systemPrompt LINE 83 and asserts it is the v29 DEFERRALS
  block. On any later prompt the assert fires, which is the good outcome.
- It sends `thinking` ONLY when the served config says `disabled`. Any other
  value, including `adaptive`, is OMITTED, and on Sonnet 5 an omitted field
  means thinking ON with no effort set. A thinking A/B built on this file
  would silently run both arms the same way.
- It calls `api.anthropic.com` directly, so none of its calls appear in
  `usage_log`. Its token counts come from the API response and are exact; its
  latency is the model call alone, without the gateway's own time.
- It does not exercise the route's guards (stale `asking`, checkpoint refusal,
  envelope retry). The judge reads `reply`, which is why that was acceptable
  for STE; it has to be re-argued for any question about the object.
- `ste_analyze.py`'s pre-registered gate is "variant A reproduces v29's gold
  labels, 33 of 41". For any comparison where neither arm is v29 that gate is
  meaningless and has to be re-registered BEFORE a result is seen.
- Every path is `/tmp/...`, because it ran inside the prod container.

⚠ **The interview content is QA persona material, and that is load-bearing, not
incidental.** It is only true because `com.weirtech.n400helper` is still absent
from `CZ_APPLE_BUNDLE_ID`, so no real Apple identity can reach the lane. **The
day that bundle id is added, committing verbatim `known_facts` and
`conversation` payloads to a repo becomes a different decision.** Whoever runs
the next inputs extraction after that changes has to make it again rather than
copy this one.

## The two label revisions, and why both exist

Scott ruled on 2026-09-11: **both files, named apart, neither pretending to be
the other.**

    revision 2 (here)       the 09-06 convention, a conditional hedge counts as TOLD
    revision 3 (auditor)    the #966 convention, a conditional hedge does NOT

Revision 3 lives with the auditor as `qa/labelled-deferral-turns.json` and
differs from revision 2 by exactly one label: `conf-v25.json` t34, told ->
not_told. That single flip is the entire difference between the conventions,
worth 2.4 points on the historical told rate.

Two metrics, and **every number must name which file it was scored against** or
the ambiguity comes straight back:

    disclosure_rate_conv0906      scored against revision 2, flag OFF
    v30_conditional_compliance    scored against revision 3, flag ON

Why two: the disclosure rate has to keep the 09-06 convention or the pre-v28 38%
baseline stops being comparable to anything and the historical labels lose their
only job. v30 compliance has to treat the conditional as NOT disclosure, because
a hedge is precisely what #966 forbids, so a scorer that counts it reads 100%
obedience whether the lane obeys or not. That is a check that passes by
construction.

## Traps, from the files' own headers

- **Score PER FIELD, never per turn.** 42 turn labels cover 70 field instances,
  and a turn label is the AND across its fields.
- **These labels describe v6 to v27 output.** They are a PRE-v28 BASELINE, not
  ground truth for anything generated after v28. The STE gate failed on exactly
  this: it asked v29 to reproduce the defect v28 and v29 were written to fix, and
  v29 correctly refused.
- ⚠ `n400-deferral-ste-inputs.json` says `observed_reply is the v29-era reply`.
  That phrasing misled a GP session into treating the labels as v29 ground truth.
  It means "the baseline as it stood during the v29 era". Read it that way.
- **Do not hand a judge the prompt rule text.** A judge given the rule evaluates
  the lane against the same words the lane was given, which is reading the
  instruction back to itself.
- **Judge and lane are the same model family**, so they can share a blind spot.
  The three attachment turns (`conf-es-v27` t31, `conf-v21` t31, `conf-v22` t32)
  are the test for exactly that.
- An agreement figure from these files is a statement about the **instrument**,
  not about the lane. The lane is measured by regenerating replies from the
  inputs file under the variant being tested.

## Blindness: the pack and the key are adjacent ON PURPOSE, and both say so

**A second labeller must not read `ste-label-unblinding-key.json` before
labelling `ste-label-pack.json`.** Both files now carry a `_hazard` header
saying that, in the direction that matters for whichever one you opened.

My first reasoning here was wrong and fable-auditor caught it. I argued the
blindness was already spent, since the 30 items were labelled and scored on
2026-09-11, so the key beside the pack cost nothing. The first half is true and
the conclusion does not follow: **the pack has a legitimate future use that is
not a fresh blind pass.** A second independent read of the SAME 30 items is
inter-rater agreement, and that is the one thing that would settle the
conditional-hedge question empirically instead of by ruling. Two readers have
already split on it, the 09-06 labeller and fable-auditor-55. A third read is
the natural next experiment, and it only works if that reader never saw the arms.

They stay adjacent rather than separated because **a file that is merely far
away gets found anyway, and a file that states the cost of opening it does not
need to be hidden.** Separation would be the same mistake as a shouty filename,
pointed the other way: it relies on the name to carry a hazard that belongs in
the content.

⚠ A fresh blind pass on NEW turns needs a new pack either way; these items are
spent for anyone who has already labelled them.

## The second scorer is a GATE on `n400_multiturn_probe.py`, not a README line

Standing rule, Scott 2026-09-17: "make it standing, run it on every arm before
shipping." No prompt version ships on a probe verdict until every arm's
reply-TEXT checks have been rescored by a second, differently built scorer and
the two agree. A disagreement is a HOLD that a person rules, and the ruling is
recorded with the arm. It is not a second opinion to average with the first; it
is a check on the instrument.

The rule doc, the questions, the thresholds and the two failure modes of the
method live with the tool, at `N400 App/qa/JEV-SECOND-SCORER-STANDING-RULE.md`.
That file is the authority and nothing here keeps a copy of the questions,
because two copies of a rule drift and the drift is invisible from both sides.

`_second_scorer_gate()` in `n400_multiturn_probe.py` runs it before the per-arm
verdict lines print, and returns 3 with the lines stamped `[UNVERIFIED]` if it
holds. ⚠ Not being able to run it holds too: no `--out` file, no tool on disk,
no `TYPESAFE_API_KEY`. An absent check reads as a pass to anybody scrolling
past, which is the same shape as the false pass that produced this rule.
`--advisory` downgrades a hold to a print and stamps the output.

**Why a gate and not a habit:** the hand-written regex
`step2_asks_moved_out_day` required `asks_day(r2)` AND `/out|left|leave/`, and
the phrase "move-out date" in a STATEMENT supplied the "out" while `asks_day`
matched elsewhere in the reply. It credited a question that was never asked,
and it had already helped certify v35e at 3 of 3, which is what serves today. A
rule whose only carrier is "whoever runs the probe remembers" has no carrier.

Rescored 2026-09-17, all four committed run files, zero disagreements: v36 19
of 19, v37b 9 of 9, v37 4 of 4, v35e 9 of 9 (this last through
`typesafe_vs_regex.py`, the v34/v35 file shape). 41 checks, about $0.00054 all
in, so cost is never the reason to skip it.
