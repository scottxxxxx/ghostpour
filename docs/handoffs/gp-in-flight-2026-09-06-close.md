# GP session close, 2026-09-06 (second close; supersedes the earlier one)

**prod = main = `d681394`.** 23 PRs merged today. **One open: #924**, green,
a one-line loader fix. Served N-400 interviewer config is **v26**, verified
by reading the served copy back by string.

---

## ⚠ NEEDS SCOTT, in order of consequence

**1. Meeting memory is enabled for the N-400 app, pointing at ShoulderSurf's
identity.** `entitlement_state(..., "context_quilt", "n400")` resolves to
`enabled` at plus and pro. `users.tier` is shared across apps, so an SS Pro
user arrives at N-400 entitled. And `apps.yml` gives n400 **no `cq` entry**,
so `_cq_identity()` falls back to the DEFAULT ShoulderSurf identity. If that
hook ran on an N-400 turn, GP would authenticate to ContextQuilt as
ShoulderSurf and pull meeting memory into an immigration interview.
- **It has never happened**: 800 n400 rows, zero CQ recall.
- **It is not prevented, only unobserved.** The gate does not stop it and
  the identity fallback points at the wrong tenant. The likely reason
  nothing crosses is that the hook needs meeting/project context the N-400
  lane never sends, which is a boundary holding BY ACCIDENT.
- **Recommendation: disable `context_quilt` for app=n400 in the
  entitlements matrix.** N-400 has no meetings, so the capability has no
  meaning there, and one that exists only to be prevented eventually fires.
  Giving n400 its own CQ identity is belt, not the fix. NOT changed:
  another team's app, his ruling.

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
