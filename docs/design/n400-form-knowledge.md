# N-400 form knowledge: GP as the subject-matter expert

**Status:** design, 2026-09-06. Scott's mandate: GP becomes the N-400
expert so the lane can redirect and disambiguate when an applicant's
answer does not map cleanly onto what the form asks. Everything N-400
must stay independent of ShoulderSurf and Tech Rehearsal.

## The problem, from real defects rather than from theory

Three failures in the conf-v24 and conf-v25 runs were all one thing: the
lane did not know what the form actually asks.

1. **Turn 40, the widow.** She volunteered "he passed in August 2019".
   The lane thanked her and recorded nothing, which was CORRECT, and then
   said nothing about why. Establishing that it was correct took a human
   opening the USCIS PDF and reading that every spouse field on Part 5
   says *current* spouse. That knowledge was nowhere in the system.
2. **Turn 104, Selective Service.** The lane told her she had answered
   "no" about "military or Selective Service issues". No such answer
   existed. It invented a question, an answer, and a confirmation.
3. **Turn 73, "a what?"** She asked what a title of nobility was and got
   no answer, so the "no" she then gave could not be informed. v25 fixes
   this with four glosses written by hand into the prompt, which does not
   scale and has no citation behind it.

The shape is constant: **the lane improvises where it lacks knowledge,
and improvisation on a federal form is a false statement.** Guards can
refuse a claim the record contradicts (#909), but no guard can supply a
fact the system does not have.

## What this is NOT

**Not embedding retrieval.** The N-400 is a bounded, authoritative,
versioned corpus: one form, eighteen parts, a few hundred fields, plus
official instructions and the USCIS Policy Manual. Similarity search is
the wrong instrument for it, and the reason is the failure mode. An
embedding miss returns a *plausible neighbour* — the Part 12 interpreter
question when asked about Part 13 preparer — and a plausible neighbour is
exactly what produced turn 104. A curated index either has the row or it
does not, and "does not" is detectable.

We also cannot cite an embedding. Every claim this system makes about the
form has to carry a source, because the capability that authorises it
(below) is authorised *only* as the restatement of a published source.

**Not legal advice, and this is already ruled.**
`config/remote/n400/policy-matrix.json` draws the line and it is tested:

| capability | basis | may GP do it |
|---|---|---|
| `explain_question_literal` | PUBLISHED | **yes** — "reading back what USCIS itself publishes about a question, in plainer words, states a public source" |
| `populate_field` | PUBLISHED | yes, scrivener assistance |
| `normalize` | PUBLISHED | yes, form not substance |
| `recommend_answer` | NEEDS_LEGAL | no |
| `interpret_legally` | PUBLISHED | no — 8 CFR 292.1 |
| `determine_eligibility` | PUBLISHED | no — USCIS's determination |

So the knowledge pack may contain **what the form asks, what its terms
mean per published sources, and what the form does not ask.** It may not
contain what an applicant should answer. That boundary is not a
preference; it is the thing that keeps this on the right side of 8 CFR
292.1, and it is already ratified in this repo.

## Tenancy: use what exists, do not invent

Verified today rather than assumed:

- **Config is already isolated.** `config/remote/n400/` resolves through
  the per-app dir mechanism, and `tests/test_n400_policy.py` pins both
  that the slug uses the registered n400 dir AND that it is never read
  from the flat name. A knowledge pack in this namespace inherits proven
  isolation; no new mechanism is needed.
- **Money is already isolated.** The old leak where an N-400 call drew
  down the user's ShoulderSurf allowance was closed: `record_cost` takes
  `app_id`, and an app carrying its own enforced cap is skipped from the
  shared account meter. N-400 has a flat cap in `budget.json`, so it
  meters against its own `usage_log` rows.
- **Usage and telemetry are stamped** with `app_id`; account deletion is
  per-app.

⚠ **The one live gap that matters for new N-400 endpoints:** `X-App-ID`
is **self-asserted**. It is read from the header on every request and
never compared against the `app_id` stamped on the session at sign-in.
Any new N-400-only route inherits that: a caller can claim to be n400.
For a knowledge lookup the exposure is small, because the corpus is
public information, but it should be stated rather than discovered, and
it is an argument for keeping the knowledge server-side where possible.

## The design

### 1. A knowledge pack, per-app namespaced

`config/remote/n400/form-knowledge.json`, `server_only`, versioned like
every other served config, carrying:

- **`fields`**: keyed by the field ids the client already sends in
  `known_facts` and on the agenda (`p5.marital_status`,
  `p9.willing_bear_arms`). Each row: the part, the official question
  text, what kind of answer it takes, and its citation.
- **`not_asked`**: the negative space, which is the half nothing else
  has. "Part 5 asks nothing about a deceased spouse." "There is no
  Selective Service *issues* question." This is what would have made
  turns 40 and 104 impossible.
- **`terms`**: the form's own vocabulary with plain-language glosses,
  each carrying a source. Supersedes the four glosses hand-written into
  v25's prompt.
- **`ambiguities`**: an utterance shape → what the form actually asks →
  the correct clarifying move. Sourced, not invented.

Every row carries `basis` (`PUBLISHED` with a URL, or `NEEDS_LEGAL`) and
`source`, exactly as the policy matrix does. A row without a citation is
a defect, and a test should say so.

### 2. Fail closed, like the policy matrix

`test_capability_nobody_wrote_a_row_for_blocks` is the pattern. An
unknown field or term returns **nothing**, and the lane must say it does
not have that rather than improvising. This is the direct fix for turn
104: the lane could not have asserted a Selective Service answer if
asserting anything required a row that did not exist.

### 3. Retrieval is a lookup, not a search

Given a field id, return that row. Given an utterance and the standing
node, return candidate ambiguity rows by explicit alias match. Both are
deterministic, auditable, and citable, and both can report "no match",
which similarity search structurally cannot.

### 4. Where it plugs in

Server-side, in the interviewer turn, the same place the agenda and
`known_facts` already arrive. GP owns the conversation
([[feedback_gp_brains_apps_are_views]]), so the knowledge belongs on the
server side of that boundary and not in a client bundle. A read-only
lookup endpoint for the client is possible later if they need one; it is
not needed to fix the defects above.

## What is deliberately open

- The corpus content itself, pending the research pass.
- Whether `ambiguities` needs Scott's legal reviewer before it ships. A
  clarifying question is arguably `explain_question_literal`, but "what
  the form actually wants here" edges toward `recommend_answer` and the
  matrix already flags that row as the one most worth a reviewer's time.
  **Held at NEEDS_LEGAL until ruled.**
