# Project Chat — prompt assembly guidance for SS

How ShoulderSurf iOS should structure the `system_prompt` it sends to
`POST /v1/chat` when `prompt_mode = ProjectChat`. GP treats `system_prompt`
as opaque (passed through to the provider verbatim, after CQ recall is
prepended), so this is purely an SS-side contract — no server-side
enforcement.

Last updated: 2026-05-06.

## Why this exists

Project Chat answers were mis-attributing meeting dates and reading
chronological references backwards. Root cause: SS was inlining selected
meetings into `system_prompt` without explicit date headers, and the order
of meetings in the prompt didn't match what the user expected. CQ recall
patches don't carry meeting-level metadata (no dates, no meeting
boundaries — see `recall_formatter.py::format_flat_ranked`), so anything
the LLM knows about *when* something happened or *which meeting* it came
from has to be in the SS-assembled portion of the prompt.

This doc is the unified spec for that assembly.

## Goal

The LLM should be able to answer "what was decided?", "when did X
happen?", and "in which meeting?" with equal confidence, regardless of
which mix of summaries / transcripts / prior Q&A the user selected per
meeting.

## 1. Open with a manifest

Before any meeting content, emit a compact header so the model knows the
shape of what's coming:

```
You have context from N meeting(s), spanning {earliest_date} to {latest_date}, ordered oldest → newest. For each meeting, the user has selected one or more of: summary, transcript excerpt, prior Q&A.
```

- **N=1:** swap to `You have context from one meeting, dated {date}.`
- **Order chronologically (oldest first).** If SS keeps reverse-chron for
  UI/parity reasons, change the line to `…ordered newest → oldest` so the
  model isn't guessing.
- **Zero meetings selected** (Project Chat with project-level context
  only): skip the manifest entirely. CQ recall + project metadata carry
  the load.

## 2. One block per meeting, in order

```
## Meeting {i} of {N} — {YYYY-MM-DD} · "{title}" ({relative_time})
```

- `i` is the position in the assembled prompt, not a database ID.
- `relative_time` is "today" / "yesterday" / "3 days ago" / "2 weeks ago".
  Gives the model a temporal anchor without forcing date arithmetic.
- **No UUIDs in the prompt.** If SS needs to round-trip an ID for
  follow-up actions, keep a client-side `i → meeting_id` map and resolve
  at render time. Raw `meeting_id=550e8400-…` in the prompt is tokens
  with no semantic value and invites hallucinated references.

## 3. Sub-sections — only the ones the user selected

Under each meeting header, emit only the sub-sections that have content.
Always in this order: **summary → transcript → prior Q&A**.

```
### Summary
{summary text}

### Transcript excerpt
{transcript — see truncation note below}

### Prior Q&A from this meeting (asked {date})
Q: {user question}
A: {assistant answer}
```

**Per-chunk rules:**

- **Summary.** As-is. No decoration.
- **Transcript.** If SS already chunks/truncates, prefix the chunk with
  `[truncated — first/last/middle N lines of M]` so the model knows what
  it's *not* seeing. Preserve speaker labels (`Speaker 1:` / `Scott:` —
  whatever SS already renders).
- **Prior Q&A.** Repeat the date inline (`asked 2026-04-22`). The user's
  old question often contains implicit temporal references ("this
  meeting," "today," "earlier"); without the date the model misreads
  them.

## 4. Edge cases

- **Queries-only for a meeting** (no summary, no transcript). Still emit
  the meeting header. The Q&A is the entire content, but the date+title
  anchor matters most in this case — without it, the model has a
  question with no idea what it was about.
- **Same meeting selected with all three.** No special handling — the
  per-meeting block just has all three subsections in the order above.
- **One huge transcript dominating the budget.** Out of scope for this
  format spec; SS's existing budget gate (`docs/wire-contracts/budget-gate.md`)
  handles it. The format here doesn't change.

## 5. Don'ts

- Don't intermix by type (all summaries, then all transcripts). Group by
  meeting. The meeting is the outer container; chunk type is the inner
  section.
- Don't include `meeting_id` UUIDs in the prompt.
- Don't omit dates "to save tokens." A meeting header costs ~20 tokens;
  the fuel gauge already accounts for it.
- Don't rely on prompt position alone for ordering. The model treats
  prompt order as narrative order *only when you tell it to* — make the
  ordering explicit in the manifest.

## 6. Worked example

Three meetings selected, mixed inclusions:

```
You have context from 3 meeting(s), spanning 2026-04-08 to 2026-04-29, ordered oldest → newest. For each meeting, the user has selected one or more of: summary, transcript excerpt, prior Q&A.

## Meeting 1 of 3 — 2026-04-08 · "Kickoff with Florida Blue" (4 weeks ago)

### Summary
Initial scope discussion. FB to provide claims sample by 4/15.

## Meeting 2 of 3 — 2026-04-22 · "Handoff prompts review" (2 weeks ago)

### Summary
Reviewed 4 candidate prompts. Decided on Mixtral 8x22B for runner.

### Transcript excerpt
[truncated — first 40 lines of 312]
Scott: Okay, so the question is whether we frame the handoff as…
…

### Prior Q&A from this meeting (asked 2026-04-22)
Q: Which prompt variant scored highest on coherence?
A: Variant C, by a small margin over A.

## Meeting 3 of 3 — 2026-04-29 · "Cost-reduction sync" (1 week ago)

### Summary
Slice 2 (search caps) shipped. Slice 3 reassessment pending.
```

## What does NOT change on the GP side

- `/v1/chat` request shape — `system_prompt` stays opaque.
- CQ recall — still prepended as `[CONTEXT FROM PREVIOUS MEETINGS]\n…`
  above SS's assembled prompt. Patches remain undated by design.
- Budget gate — `(len(system_prompt) + len(user_content)) / 4` still
  drives the 413 / context-cap path. Per-meeting headers cost ~20 tokens
  each and are absorbed by the existing `tier.max_input_tokens` budget.
- `meeting_id` request metadata — still sent on the request body
  (`metadata.meeting_id`) for usage logging and CQ origin scoping. That's
  separate from the rendered prompt content; nothing changes there.
