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

## Blindness

The pack's blindness has already been spent: the 30 items were labelled and
scored on 2026-09-11, so the key sitting beside it costs nothing now. Re-using
these same items for another blind pass would be invalid regardless, because the
labeller has seen them. A future blind pass needs a fresh pack.
