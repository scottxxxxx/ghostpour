# Extraction yield: the question, the wrong answer, and the answer

> **RETRACTED AND REPLACED 2026-08-31.** The first version of this file
> carried a 34-meeting "silent" cohort and asked GP to join transcript
> lengths against it. **That cohort was invalid and the join was never run.**
> Do not use it; it is preserved nowhere on purpose. What follows is the
> corrected finding, which answers the question outright and needs no join.

**The question:** Scott's tire-store meeting produced almost nothing, while
his vet visit produced ten patches including a real commitment. Was that
because the tire store had **no project** (which would make it a scoping
problem, and would justify the header work in
`2026-08-30-recall-scoping-posture.md`), or because it simply had **less
content** (which would make it nothing to do with scoping at all)?

---

## The answer: it was length. The content hypothesis carries it.

Measured by CQ from `extraction_metrics`, which records `transcript_chars`,
`patches_before_filters`, `patches_after_filters`, `entities_extracted` and
`origin_id` per extraction call.

    calls, last 14 days                             254
      parsed zero patches                           119   47%
      sanitizers stripped everything                  1
      dedup absorbed everything                       1
      stored something                              133

    TRANSCRIPT LENGTH
      calls yielding zero patches      median    786 chars
      calls yielding patches           median 13,774 chars

    zero-yield rate by transcript length
      <2000 chars     81/90    90% produced nothing
      <5000           20/39    51%
      <10000           7/32    22%
      <20000           2/35     6%
      <40000           8/37    22%
      >=40000          1/21     5%

A **17x** difference in median length, holding monotonically out to 20k. This
is not a subtle effect.

**Scott's tire store is explained outright: 2,303 characters, owner marker
present, zero patches parsed.** A couple of minutes of audio. Not
personal-versus-work, not the missing project, not scoping.

### What that does to the scoping proposal

It **removes** the strongest argument for option C. That option was largely
justified by serving project-less meetings well, and the premise was that
those meetings were being failed *because* they had no project. They were
not. They were short. C still has a case on confidentiality grounds (the
entity header is where the customer names actually came through) but it can
no longer be sold on the casual-use experience.

---

## The residual, which is small and is where a real defect may live

**Eleven transcripts of 10,000+ chars parsed to zero patches.** These are not
explained by length and are the cohort worth having.

    2E206B94   41,178 chars
    3F7686A6   35,844
    23DDC47E   31,070
    02430F01   27,494
    9E153EB5   26,641
    22FB3590   24,132
    EBD40AB3   22,901   <- see below
    AB54F986   22,535
    796D53C3   20,962
    AFADE3CC   15,682
    8557035C   12,289

**`EBD40AB3` is the one to look at first.** 22,901 chars in, **4,163 output
tokens**, zero entities, zero patches, owner marker present. The model wrote
4,163 tokens and nothing was kept, with no entities either. That is the
signature CLAUDE.md warns about: `AnthropicLLMClient.extract()` does not
enforce `json_schema` on the wire, so a prose answer parses to nothing and
reports as an empty result rather than as a failure.

The other ten look different: mostly 600-900 output tokens with
`reasoning_chars` around 1,300-1,700, i.e. the model reasoned and then
emitted a genuinely empty patches array. **Those are two different failures
and they have not been separated.**

---

## Three ways this was got wrong first, all worth keeping

Every one fired cleanly and answered a slightly different question than the
one asked, which is the failure mode that survives review.

1. **The circular project signal.** A first cut asked whether any patch on
   the meeting carried a project and printed a perfect correlation: 100% of
   project-less meetings silent, 0% of project-having ones. Spurious. Only 79
   of 1486 behavior patches are scoped, so "every patch is behavior" nearly
   *entails* "no patch is scoped". Two variables that were one variable.
2. **The windowed backfill.** 38 "silent" meetings were really 34. Seventeen
   were ingested in one three-minute span on 08-17, a backfill re-running the
   behaviour call over historical meetings; their non-behavior patches
   predated the 14-day window rather than not existing, one by two months.
3. **The proxy that excluded the answer** (the one that invalidated this
   file). The cohort was built on `origin_id`, but six patch types are
   **origin-null by design** (`project_origin_id_design`, a ruling predating
   all of this): person 140/140, insight 108/108, project 83/83, preference
   47/47, trait 30/30, org 17/17 in the last 14 days. That is 425 patches
   invisible to any origin-keyed query. A meeting that produced a pile of
   person and project patches looked silent while having produced plenty.

   The example this file previously headlined, `EA3C5976`, annotated "the one
   that breaks 'too short'", was **the opposite of what it was sold as**:
   47,949 chars, 34 patches parsed, 15 stored, 9 entities. One of the most
   productive meetings in the window.

**The third is a different animal from the first two.** The first two are
instrument effects. The third was reaching for a derived signal when the
authoritative per-call table (`extraction_metrics`) existed, with the exact
column needed, in the same schema. Not a subtle effect: a proxy adopted
without checking what it excludes, where the exclusion was a deliberate
design ruling in the same repo.

### And the constraint that was asserted twice and was wrong twice

"Transcript length against yield can only be measured at ingest going
forward, never backfilled" was stated as a hard constraint, twice. It was
wrong first because GP retains transcripts 30 days, and wrong again, more
importantly, because `extraction_metrics` had `transcript_chars` per call on
CQ's own side the entire time. **No GP join was ever needed.** The scoping
proposal's note that GP could supply the data is true and was never required.

---

## Status

Measured by CQ, from `extraction_metrics`. **GP ran nothing here**, and the
30-day transcript join described in the scoping proposal was never needed and
was not executed. The eleven above are the open item.
