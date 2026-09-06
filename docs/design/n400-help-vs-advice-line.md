# N-400: the help vs legal advice line, from primary sources

**Status:** research findings, 2026-09-06. Sources verified against the
primary documents rather than secondary summaries. This exists because
the knowledge pack in `n400-form-knowledge.md` needs a boundary, and the
boundary turned out to be sharper and better documented than expected.

## The good news first: the existing design is already on the right side

`config/remote/n400/policy-matrix.json` allows `explain_question_literal`
and blocks `recommend_answer`, `interpret_legally` and
`determine_eligibility`. **That split matches the federal line almost
exactly**, which is worth stating because it was drawn before this
research existed and it holds up.

## The sharpest formulation that exists, and it is DOJ's

DOJ EOIR, *"Are You a Victim of Fraud?"* — non-attorneys:

> **"CANNOT tell you which immigration forms to use or what answers to
> put on the forms"**

USCIS's own UPL training deck (March 2023) lists as unauthorized legal
advice:

> "**How to answer questions on forms** · Advising individuals on
> available immigration options · **Advising what documentation to
> include with forms** · **Advising when to file**"

and, from the same deck:

> "Courts have held that even a nonattorney's **selection of which legal
> forms to complete** can constitute the unauthorized practice of law."

**"What answers to put on the forms" sits close to the product's core
feature.** The distinction the product lives or dies on is between
*transcribing what she said* (allowed, scrivener) and *telling her what
to say* (not allowed). The lane's existing evidence floor, which refuses
to record anything not in her own words, is not merely a data-quality
guard: it is the mechanism that keeps the product on the allowed side.
That is a stronger reason to keep it than the one it was built for.

## The four-condition safe harbor (8 CFR 1.2, DHS)

All cumulative. Fail one and the activity is "practice":

1. **Solely** filling in blank spaces on printed forms;
2. with information **the applicant provided**;
3. **no exercise of professional judgment** to give legal advice;
4. remuneration **nominal or none**, *and* **no holding out** as
   qualified in legal matters or immigration procedure.

USCIS Policy Manual 1 USCIS-PM D.2 n.3 restates it: preparation is not
practice if the preparer "does not hold themselves out as qualified...
and merely assists with the completion of blank spaces on DHS forms
**for a nominal fee, if any**."

## ⚠ The one that is a business decision, not an engineering one

**Condition 4's nominal-fee requirement has no found reconciliation with
commercial pricing.** Neither 8 CFR 1.2 nor 1001.1(k) nor the Policy
Manual gloss offers a commercial carve-out, and no authority reconciling
them was located. This is not a drafting nit and no code change addresses
it. **It needs counsel before pricing is set, not after.** Flagged once
here; it is Scott's call and the build continues regardless.

## Three exposure paths, and only one is about the code

- **State UPL** — the code and the copy.
- **FTC deception** — *FTC v. Forms Direct / American Immigration Center*
  (N.D. Cal. 2018), $2.2M, an online USCIS-form preparation service. The
  theory was **government-imposter deception, not UPL**.
- **FTC substantiation** — the DoNotPay order.

**In both cases that reached a holding, the marketing is what lost it**,
not the software. That is a useful and slightly counterintuitive
allocation of risk: the most dangerous surface may be the landing page
rather than the interviewer lane.

Related: supplying model narratives or sample answers is the charging
theory in *Gospel Immigration* (USAO N.D. Cal.). Part 13 requires the
form to reflect **only** information provided by the applicant, so
auto-populating substantive content the applicant did not supply is the
specific thing prosecuted.

## Genuinely unsettled, recorded rather than smoothed

1. **Who or what goes in Part 13 when software did the assisting.**
   1 USCIS-PM B.5 says "the preparer **and any other person who
   assisted**" must sign. There is no software gloss anywhere.
2. **The nominal-fee condition versus commercial pricing** (above).
3. Whether a courtesy title or earned honour counts as a title of
   nobility. USCIS publishes no definition.

## What the form does NOT do for us

Grepped from the 01/20/25 instructions:

- **"G-28" appears zero times.**
- **"legal advice" appears zero times.**
- "attorney" appears twice, both incidental.

**The paperwork warns the helper about nothing.** Every guardrail is the
product's to build. Nobody gets a nudge from the form.

## A correction carried from the research

ILRC's step-by-step guide cites **8 CFR 1003.102(t)** for the
preparation/G-28 rule. That is a mis-citation: (t) is the
entry-of-appearance discipline ground. The relevant subsection is
**(m)**, assisting unauthorized practice. Anything in our notes carrying
ILRC's sentence should carry the corrected cite. Note also that all of
1003.102 is a *practitioner* discipline rule, so it reaches a product only
indirectly, through (m), which would make a supervising attorney liable
for assisting the tool's UPL. That matters if the plan ever involves an
attorney signing off.


## What this reclassifies, which is the consequential half

The research does not mainly change what to build next. **It changes what
several defects we already fixed actually were.** We were closer to the
line than we knew, and had stepped back from it for the wrong reason.

If a non-attorney may not tell her "what answers to put on the forms",
then the evidence floor is not a data-quality mechanism that happens to
be prudent. **It is the compliance boundary**, because it is the thing
that guarantees no answer reaches the form that she did not say. Three
defects graded as quality problems are really boundary defects:

1. **conf-v10 turn 67, "Most people say no"** on the fee-reduction
   question. Graded as a nudge. It is suggesting the answer, on the one
   question in the form with a money consequence. The auditor has since
   built a detector and run it over the whole corpus: **exactly one hit**,
   that turn, across English, Spanish and Portuguese patterns.
2. **Part 9 batteries minting ten fields from a "no" that answered one
   clause.** Graded as over-minting. It is putting answers on her form
   that she was never asked, which is the same act with her participation
   removed.
3. **The narrated read-back**, the worst of the three. A summary that
   asserts an answer she did not give and asks her to confirm it does not
   only record something unsaid, **it obtains her agreement to it**.
   conf-v25 turn 79 told her the oath was recorded across the board when
   one field of six existed, and she said yes twice.

### The consequence for priority

The two failure directions of the same floor now carry **different
severities**, and they did not before:

- A floor that drops too much costs a **re-ask**. Quality.
- A floor that lets an unsaid answer through is a **boundary breach**.

That asymmetry is why the non-answer rule was correctly changed from a
dropper to a marker (all 13 would have been drops of facts she really
said, so dropping bought nothing and cost re-asks), and it is why
**field-level read-back derivation stops being a polish item.** A
read-back narrated from what the model believes rather than derived from
what is on file is the mechanism by which an unsaid answer gets her
confirmation. That is the boundary, so it is a requirement.

## ⚠ Genuinely uncertain, and a Scott question rather than ours

The line between explaining what a question MEANS and suggesting what to
ANSWER is clean in the two cases we have and **is not clean in general**.

- "Bearing arms means carrying weapons" is explanation, and it is USCIS's
  own gloss. Clearly fine.
- "Most people say no" is suggesting the answer. Clearly not.
- **"There is no modification available for that clause" is true,
  published, and one step from "so you will need to answer yes."**

The third is the shape we cannot resolve by reasoning, and both teams
arriving at the same intuition about it is not evidence, it is the same
blind spot twice. It needs the legal reviewer that `policy-matrix.json`
already names Scott as the owner of, and it is a better use of that
reviewer's time than the `recommend_answer` row the matrix currently
flags as most worth it.
