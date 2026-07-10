# `tr_compare_reality` — data contract (v1, user-reviewed, sent to GP 2026-07-09)

**Status: LIVE** — config shipped #374, TR flipped + harness-verified
2026-07-10 (14 checks, both capture modes). The original status text below
is preserved for history.

## Calibration refinement (2026-07-10, agreed by both teams — prompt v2/v3)

First-device-use field failure: a two-line recap produced red `missed`
sections for every planned topic the recap never mentioned — silence
converted into failure, which the calibration clause forbids. Both teams
independently diagnosed the same root cause (the delta enum has no
"unknown" state, so the model force-fit `missed`). Live behavior now:

- **Evidence rule is capture-mode explicit.** RECORDED TRANSCRIPT: absence
  IS evidence — a planned topic that never appears is genuinely `missed`.
  USER RECAP: absence is NOT evidence — `missed` only when the user's own
  account indicates the topic never came up or wasn't delivered.
- **Silence produces no section.** A topic the capture gives no evidence
  about (positive or negative) gets no section at all; its prep reminder
  folds into `coaching`.
- **Thin recaps teach.** When a recap is too thin to verify the plan, one
  coaching line encourages a fuller recap next time (what was asked, what
  they said, how it landed). A thin recap may honestly yield only 1-2
  sections; fewer grounded sections beat padded ones.
- Verdict behavior unchanged (it already stated coverage honestly). No
  wire change; TR's parse untouched. TR adds a thin-recap fixture to the
  standing replay.

**Lesson for future contracts:** a closed enum (like `delta`) needs an
explicit not-reported story per capture/trust mode, or the model will
force-fit the least-wrong value.

**Original status text (historical): PROPOSED — NO GP CONFIG EXISTS YET. Do not ship a client call until GP
confirms the config is live (the `tr_parse_resume` rule: a promptless call to an
unconfigured call_type 400s).** Standing process: this contract → GP authors the
managed config → TR flips the client promptless → harness verify with heads-up.

## Purpose

The "Compare What We Practiced to Reality" step of the Co-Pilot section
(design: `Tech Rehearsal Flow.dc.html`, claude.ai/design project "Tech
Rehearsal Layout Design"). One call diffs a REAL conversation — captured
either as a Copilot recording (diarized transcript) or as a user recap
("Describe It Yourself") — against what the user rehearsed: the prep plan
plus the latest mock's analysis. Output renders as the side-by-side Report
(rehearsal vs. reality, what landed / what drifted, coaching for the next
real conversation).

This is the comparison the debrief scorer can't do alone: evaluation anchored
to the user's own rehearsed plan instead of a free-floating rubric.

## Request envelope

- `call_type: "tr_compare_reality"` (via the standard `TRGateway.callType`
  prefix mapping), `provider/model: auto/auto`, `context_quilt: false`,
  `locale` as usual.
- `metadata.scenario` / `metadata.scenario_kind`: standard ScenarioKind tags.
- `metadata.capture`: `"transcript"` or `"recap"` — how the real conversation
  was captured, for usage analytics and so GP can weight trust accordingly.
- No `system_prompt` ever — GP assembles from its config, selected per
  `scenario_kind` like the other managed calls.

## Input (`user_content` plain-text blob, sections omitted when empty)

```
ROLE: <title> at <company>            (job scenarios; omitted for others)

REHEARSAL PLAN (what we prepared):
GOAL: <desired outcome>
BACKGROUND: <situation summary from the prep report / encounter brief>
CONSTRAINTS: <constraints / stakes>
KEY POINTS PLANNED: (from the prep report — story bank / must-cover items)
- <point>
...

PRACTICE ANALYSIS (latest mock for this rehearsal):
OVERALL: <score>/100 — <headline>
BIGGEST GAP: <title> — <detail>
COACHING GIVEN:
- <per-question whatsMissing / coaching highlights>
...

REAL CONVERSATION (RECORDED TRANSCRIPT):
<diarized transcript>
```

or, for the recap capture:

```
REAL CONVERSATION (USER RECAP — first-person, self-reported, unverified):
<the user's own account>
```

Notes for prompt authoring (GP side):
- A recap is the user's memory, not ground truth — compare charitably; never
  penalize brevity of the recap itself, only what it reveals.
- The PRACTICE ANALYSIS section may be absent (user never ran a mock). Then
  compare against the REHEARSAL PLAN only and say so in the verdict.
- Calibration: comparison is anchored to the plan. If reality matched the
  plan, SAY SO — `drifted` may be empty. Do not invent criticism to fill
  lists (the lesson from the debrief treadmill: an account that incorporates
  prior coaching must never score worse for it).

## Output (exact JSON TR parses)

Return ONLY valid JSON (no markdown, no fences), this exact shape:

```json
{
  "verdict": "one or two sentences: how reality compared to what was rehearsed",
  "sections": [
    {
      "topic": "2-4 word noun phrase",
      "planned": "what the rehearsal prepared for this topic",
      "reality": "what actually happened",
      "delta": "landed",
      "note": "one coaching-relevant sentence"
    }
  ],
  "landed": ["what worked as rehearsed"],
  "drifted": ["where reality diverged from the plan"],
  "coaching": ["specific prep for the NEXT real conversation"],
  "next_best_focus": "single highest-leverage thing to practice next"
}
```

- `sections[].delta` ∈ `landed | drifted | missed | unplanned`
  (`missed` = planned but never came up / never delivered; `unplanned` =
  happened but was never rehearsed).
- `sections[].topic` pinned to 2-4 word canonical noun phrases (same label
  discipline as the radar axes).
- `landed` and `drifted` may be empty; `verdict` and `next_best_focus` are
  required. TR's parse treats a reply with no `sections` AND no `verdict` as
  a miss.
- 3–7 sections typical; don't pad.

## Client work (TR side, gated on GP config)

- New `CompareRealityClient` mirroring `DebriefClient`'s transport shape.
- Blob builder pulls: `RehearsalReport` (plan), latest `MockReport` (practice
  analysis), and the real conversation from a `.live` `MeetingRecord`
  transcript or a `.debrief` record's account text.
- Entry point: the Job arc's real-interview / debrief rows ("Compare to my
  rehearsal"). Result persisted on the `MeetingRecord` (new `compareReportData`
  blob) so the Report reopens without re-running.
- Harness: matching builder + test across capture modes and scenario kinds,
  fired only after GP confirms config (heads-up first, as always).

## Open questions for GP

1. Routing lane: analysis-grade (this can be slow; transcripts are long).
   Suggest the same lane as `tr_response_analysis`.
2. Temperature: pin low — comparisons must be reproducible across re-runs.
3. Transcript length: cap or truncation strategy for very long recordings
   (we can pre-trim client-side to the interview-relevant span if needed).
4. Is `metadata.capture` the right vehicle, or do you want it only in the
   blob label?
