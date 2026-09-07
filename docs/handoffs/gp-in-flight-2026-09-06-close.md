# GP session close, 2026-09-06 (second close; supersedes the earlier one)

**prod = main = `d681394`.** 23 PRs merged today. **Three open and green at close: #924** (loader fix), **#925** (this
handoff), **#926** (the N-400 recall closure). They were in a merge chain
when the session ended; if any is still open, it is green and needs only a
rebase and merge. **Verify by reading main, not the merge report:** two PRs
were silently auto-closed by a branch deletion earlier today and only a
file read caught it. Served N-400 interviewer config is **v26**, verified
by reading the served copy back by string.

---

## ⚠ NEEDS SCOTT, in order of consequence

**1. ✅ RULED AND BUILT: no ContextQuilt recall reaches N-400 (#926).**
Scott ruled "disable context_quilt for n400" on 2026-09-06.

⚠ **Doing literally that would NOT have closed it, and this is the part to
carry forward.** `chat.py` reads `run_hook = state != "disabled"`, then,
WHEN context_quilt IS disabled, falls through to
`run_hook = entitlement_state(..., "people", app) == "enabled"`, which runs
the PEOPLE-scoped recall lane instead. That lane still calls
`hook.before_llm` and still resolves `_cq_identity(app_id)`, which, because
`apps.yml` gives n400 no `cq` entry, falls back to the DEFAULT ShoulderSurf
identity. So one feature off would have changed WHICH lane ran, not WHETHER
GP authenticates to CQ as ShoulderSurf inside an immigration interview.
**Both `context_quilt` and `people` are off for n400.**

`config/remote/n400/entitlements.json` is the first per-app matrix.
⚠ `entitlement_matrix()` REPLACES rather than merges, so it carries all
nine features; a test pins that the feature set matches the flat matrix, or
a feature added flat would go dark for N-400 silently. `web_search` copied
verbatim and still live. Tests assert the closed PROPERTY (the hook cannot
fire for n400 at any tier), not the two values, and one test pins the trap:
re-enable `people` alone and the hook fires again.

⚠ **OPS STEP OUTSTANDING: `sync-from-bundle` on `n400/entitlements`**,
scoped to that slug. Merged is not served. Until it is synced the matrix on
the box is still the flat one and recall is still reachable. Verify by
reading the served copy back, not by trusting the sync's own report.

⚠ The underlying facts are unchanged and still true: `users.tier` is shared
across apps because Apple issues the SIWA subject per developer team, and
n400 still has no `cq` identity of its own. The entitlement is now the only
thing standing between the two, which is why the test asserts the property.

**2. The attorney question, now concrete rather than abstract.** Two
decisions, same underlying question: how much may the product shape what she
attests to?
- She said "sí a todo, pero no entiendo eso de portar armas". Mint the five
  she plainly affirmed and hold the one she queried, or treat "yes to all"
  as uninformed and re-ask all six after glossing? The lane attempted the
  second and minted NOTHING, which is how the run became unrecoverable.
- Where does stating a true published fact tip into suggesting an answer?
  "Bearing arms means carry weapons" is USCIS's own gloss, clearly fine.
  "Most people say no" is clearly not. **"There is no modification available
  for that clause" is true, published, and one step from "so you will need
  to answer yes."** Neither team should settle that by reasoning.
- `policy-matrix.json` already names Scott as owner of the legal review.

**3. The nominal-fee condition.** 8 CFR 1.2's safe harbour for form
preparation is conditioned on the fee being **nominal or none**. No
authority reconciling that with commercial pricing was found. **No code
change addresses it. It wants counsel before pricing is set.** See
`docs/design/n400-help-vs-advice-line.md`.

**4. The N-400 flat cap is still $20**, raised 09-05 for harness runs,
bundle floor 5.0. Lower on his word.

**5. Per-send web search toggle** resets after every send, so his next
question (the one about the internet) went out with search off. Real, SS's,
by design, his call whether the design is right.

---

## What shipped today

**The N-400 lane. Seven guards live**, all verified on main by reading the
file rather than a merge report:
- `defer_dates_with_unspoken_day`: a YYYY-MM-DD fact whose day is not in her
  utterance becomes the deferral the prompt always prescribed. EN+ES day
  words including apocopated and multi-word compounds (generated across
  21..31, because "treinta y uno" is three tokens and was missed first pass).
- `mark_facts_minted_on_a_non_answer`: MARKS, does not drop. Designed as a
  dropper; the auditor measured it against nine runs first and **13 of 13
  would have been wrong drops**.
- `normalize_reply_shape`: a bare-string `reply` becomes `{"en": ...}` at the
  gateway. It crashed a GP guard and was a NON-RETRYABLE terminal error on
  the client.
- `checkpoint_is_refused` (agenda-open + nothing-on-file), refuse and retry.
- `clear_interview_over_while_agenda_open`.
- `offers_impossible_oath_modification`: the auditor's polarity rule,
  implemented rather than reinvented.
- `closes_in_words_while_agenda_open`.
- `mark_values_outside_declared_options`: reads the option set off the
  agenda, so coverage extended to 70 fields with no code change when the
  client declared them.

**v26 config**: USCIS's own printed glosses replace four I wrote (two of
which NARROWED the question); the form's wording for title of nobility with
the gap stated; the no-modification rule.

**`config/remote/n400/form-knowledge.json`**: what the form asks, what its
terms mean in USCIS's words, and the NEGATIVE SPACE. Every row carries a
source, because `explain_question_literal` is authorised on the basis that
it restates a published source. Tests assert nothing is uncited and nothing
suggests an answer.

**Deploy 502 window SOLVED.** Was 17 to 31 seconds, variable. Cause was the
cold migration sweep, 15.09s of a 17.31s boot. #907 fingerprint-gates it:
`path=skipped ran=0`, 0.01s. Lifespan 17.31 -> 9.27s. ⚠ `retention_purge`
INHERITED the bill, 0.02 -> 6.69s: a measured cost belongs to the ORDER, not
the component.

**APNs push WORKS END TO END**, 16:09:52Z, key `QS9294ZU9P`, force-quit
mid-build and the notification arrived. Every field predicted before the
send.

**Also:** the streaming transport bypass closed; the generated-files purge
invariant; drift signal split into expected/unexpected with the sidecar
written on the box; the dash scrub confirmed at byte level on a real Spanish
docx (0 em dashes, 0 en dashes).

---

## Open threads with the teams

- **fable-auditor-55** (N-400): has the check for "she answered and nothing
  was recorded" (mine fired at 48% and was reverted; the node she just
  answered is the PREVIOUS turn's `asking`, not the client cursor). Waiting
  on the entitlement ruling above. Their client is green at 604 tests.
- **shouldersurf-e6**: Urubamba->Aruba is their meeting transcriber, a real
  open question about Spanish proper nouns through `SpeechAnalyzer`, with a
  lead (no contextual biasing at four construction sites) that is Scott's
  call to fund.
- **The Spanish search branch is DEAD.** Scott typed it sloppily. Three
  teams produced three plausible mechanisms and none was the cause.

## Still not built

- **Field-level read-back derivation.** THE priority. Every guard governs a
  field she cannot see, and she has been harmed three times by a sentence
  she can. The client already renders the checkpoint card FROM THE RECORD,
  so the work is "make the sentence agree with the card", and if they
  disagree the card wins.
- The Spanish envelope preamble rate: 28 of 99 turns against 2 to 6 in
  English.
- N-400 is a voice product graded entirely on typed script.
