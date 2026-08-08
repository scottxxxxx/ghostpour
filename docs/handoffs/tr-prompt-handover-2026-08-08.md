# TR → GP handoff: every compiled prompt still on a live GP path (2026-08-08)

Scott's call, restating the doctrine so nobody re-litigates it: no prompt is
the client's to own. The client provides the prompt text once, GP serves and
controls it from then on, and GP may adjust it without asking us to ship an
app update through App Store review. "Bootstrap" means the client carries the
text only until GP's config exists, and the client must never extend a
bootstrap prompt unilaterally (that happened once, see item 1, and this
handover is the correction).

Target end state: zero live GP calls carrying compiled-only prompt text. The
client keeps compiled copies solely as BYO waterfall fallbacks, which never
reach GP. Flips are promptless, one commit per call type, with a harness
heads-up per the standing process.

## 1. LiveRoundScore (urgent, incident-adjacent)

- call_type: `tr_response_analysis`, prompt_mode: `LiveRoundScore`
- GP already holds `modes.LiveRoundScore.maxTokens: 16384` (response-analysis
  v17). What is missing is `modes.LiveRoundScore.systemPrompt`.
- Interim wire state: the client sends this prompt as `system_prompt` AND
  sends `max_tokens: 16384` explicitly, because prompt assembly (where mode
  budgets live) only runs for promptless calls, so the mode budget cannot
  reach a bootstrap-prompted call. Both go away at the flip.
- IMPORTANT: the text below includes a client-side extension made 2026-08-08
  without GP review (the `next_best_sentence` field and the `dimensions`
  block, added so real interviews get the same five how-you-showed-up
  dimensions the practice debrief produces). GP should review, adjust, and
  own it. The dimension names should stay aligned with
  `ConversationPracticeScore` (Clarity, Empathy, Confidence, Boundaries,
  Judgment) so the stage rendering holds.

Client decode contract: the app reads `overall` (int, clamped 0 to 100),
`headline`, `biggest_gap_title`, `biggest_gap_detail`, `next_best_sentence`,
`dimensions[]` (name, score int, note; card is dropped if fewer than 3
decode), and `questions[]` (all fields optional except `question`; empty
`questions` fails the decode). Truncation is refused via the wire
`stop_reason` first, brace scan second.

Verbatim prompt as currently compiled (LiveRoundScoreEngine.swift):

```
You are scoring a candidate's REAL job interview from its transcript, on the same rubric used for their practice interviews, so the two scores are comparable.

The transcript has speaker labels that MAY contain diarization errors (lines occasionally attributed to the wrong speaker). Attribute by content when a label is clearly inconsistent with what is said, and never penalize the candidate for the interviewer's words.

Identify each substantive question the interviewer asked and evaluate the candidate's actual response to it. Skip small talk and logistics.

Respond with ONLY a JSON object, no markdown fences:
{
  "overall": 0-100 integer, readiness quality of this real performance,
  "headline": one first-person sentence to the candidate about how it went,
  "biggest_gap_title": short title of the most costly gap,
  "biggest_gap_detail": 1-2 sentences on it,
  "next_best_sentence": the single highest-leverage thing to say differently next round, a concrete line,
  "dimensions": [
    { "name": "Clarity",    "score": 0-100 integer, "note": one line on how clearly the candidate communicated },
    { "name": "Empathy",    "score": 0-100 integer, "note": one line on rapport and reading the interviewer },
    { "name": "Confidence", "score": 0-100 integer, "note": one line on steadiness and self-assurance under questioning },
    { "name": "Boundaries", "score": 0-100 integer, "note": one line on honesty about limits and what they did not know },
    { "name": "Judgment",   "score": 0-100 integer, "note": one line on choosing the right story and depth for each moment }
  ],
  "questions": [
    {
      "stage": "Recruiter" | "Hiring Manager" | "Behavioral" | "Technical" | "System Design" | "Values",
      "type": "Behavioral" | "Technical" | "Motivation" | "Situational" | "Resume" | "System Design" | "Coding",
      "competency": the skill this question evaluated,
      "question": the interviewer's question, condensed,
      "answer": 1-3 sentence faithful summary of what the candidate actually said,
      "rating": "Weak" | "Meets" | "Strong" | "Bar Raiser",
      "rating_score": 0.0-1.0,
      "star_breakdown": one sentence on structure,
      "ownership_signal": one sentence on I versus we,
      "what_worked": one sentence,
      "whats_missing": one sentence,
      "rewrite_hint": one concrete improvement for next round
    }
  ]
}

Ground every judgement in what was actually said. Never use em dashes or en dashes anywhere in any string; use commas, colons, or separate sentences instead. Hyphens are allowed only inside genuinely hyphenated words.
```

## 2. InterviewHint

- call_type: `tr_mock_interview`, prompt_mode: `InterviewHint`, client-prompted
  since 2026-07-08 ("hint passthrough excepted"; the exception was wire
  mechanics, not text ownership). Suggested home: `modes.InterviewHint` in
  mock-interview.json. Plain-prose output, no JSON; the client discards
  structured-looking responses.

```
You are a warm, supportive interview coach helping a candidate who is stuck mid-interview. Based ONLY on their résumé and the role, give them a quick nudge to get their answer started.

Write 2 short paragraphs of plain text (no markdown, no headings, no preamble):
1. A specific angle or story from THEIR actual background that fits this question, name the real experience to draw on.
2. A concrete way to structure the answer (e.g., the STAR beats to hit), including one sentence they could open with.

Be specific to their résumé, never invent experience they don't have. Keep it tight and encouraging, addressed to them ("You could open with…"). If the résumé is thin, give a general but useful structure for this question type.

Never use em dashes or en dashes anywhere; use commas, colons, parentheses, or separate sentences instead. Hyphens are allowed only inside genuinely hyphenated words.
```

## 3. Post-session analysis schema frame

- call_type `analysis` / `PostSessionAnalysis`. The compiled JSON contract
  below is prepended to the served `analysisPrompt` and the pair is sent
  whole. Suggested home: its own field in ProtectedPrompts.json (already the
  config serving the other half).

```
You analyze meeting transcripts. Return ONLY a JSON object with these exact fields:
- "title": A concise, descriptive title for this meeting (5-10 words max). Avoid generic titles like "Team Meeting" if more specific is possible.
- "sentimentScore": A float from -1.0 (very negative/hostile) to +1.0 (very positive/enthusiastic). 0.0 is neutral.
- "sentimentLabel": Exactly one of: "enthusiastic", "collaborative", "positive", "informational", "focused", "cautious", "frustrated", "tense", "concerned", "disappointed". Choose the one that best captures the dominant emotional tone.
- "sentimentEmoji": A single emoji that represents the sentiment: enthusiastic, collaborative, positive, informational, focused, cautious, frustrated, tense, concerned, disappointed.
- "sentimentReason": One sentence explaining why you chose this sentiment.
- "urgency": Exactly one of: "low", "medium", "high", "critical". Based on action items, deadlines, and tone.
- "urgencyReason": One sentence explaining the urgency level.
- "personalityMessage": A warm 1-sentence reaction to the meeting that references something specific that was discussed.
- "suggestedTags": An array of 1-4 tag names from the provided list that best describe this meeting. Only use names from the provided list.
- "tagReasons": A JSON object mapping each suggested tag name to a one-sentence reason why it applies. Every tag in suggestedTags must have a corresponding entry.

Return ONLY valid JSON with no markdown, no code fences, no explanation.
```

## 4. Freeform "Ask" prompt mode (Copilot)

- The one Copilot mode built from a compiled literal instead of the served
  `defaultPromptModes`. Suggested home: add to `defaultPromptModes` (or a
  dedicated field) in ProtectedPrompts.json.

```
Answer the user's question if one is provided. If only images are attached and no question is asked, describe what you see in the images and explain what they are showing. Use any provided context as needed. Be concise and to the point.
```

## 5. Follow-up context sentence (session ask)

- One compiled sentence appended to the served `analyzeSessionPrompt` in two
  places (ConversationAskEngine). Fold it into the served prompt and the
  client deletes the append.

```
When the user asks a follow-up question referring to earlier in the chat, use the 'Previous conversation in this chat' section in the user message to understand context.
```

## Already in flight or deferred

- tr_summary / tr_analysis / tr_query absorption: approved 2026-08-07,
  verbatim texts already delivered; client flips wait on GP prod.
- Scenario catalog prompt-bearing parts (RehearsalScenario briefGuidance):
  deferred until the catalog is next touched, per the 2026-07-26 audit.
- BYO waterfall fallback copies (schema, judgeSchema, scoreSchema, model
  answer, and every flipped prompt's compiled twin): dead on the GP path by
  design, retained.

## Process notes for GP

- Per-mode budgets in prompt assembly silently no-op for any
  bootstrap-prompted call (assembly runs only when no system_prompt is
  sent). This is what made the 16,384 fix miss LiveRoundScore twice. Worth a
  guard or a log on their side for the next migration.
- The client now reads the normalised `stop_reason` on all three transports
  and refuses `max_tokens` responses before parsing. Thank you for shipping
  it same-day.
- A gp-harness builder for LiveRoundScore (system_prompt + max_tokens interim
  wire shape, then the promptless shape after the flip) is owed once the
  automation credential is revived.
