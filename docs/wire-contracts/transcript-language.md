# Transcript language: `metadata.transcript_language`

Added 2026-08-21 after a Spanish meeting, recorded through ShoulderSurf's
own language picker, came back with an English refusal where its summary
belonged. The served summary recipe carried no language rule and nothing
on the wire named the language, so the model inferred and refused.

## The field

`metadata.transcript_language`: a BCP-47 tag (`es`, `es-MX`, `pt-BR`), the
language the meeting was SPOKEN in, as the client knows it from its picker.
Send it on every call that carries a transcript: summary (full, delta,
consolidation), analysis and re-analysis, meeting chat and project chat
turns that include transcript content, and `POST /v1/reports` (top-level
`transcript_language` there, since reports have no metadata dict).

Not `metadata.language`. That key already exists on `capture-transcript`
and means the DEVICE language: GP forwards it to CQ, and CQ writes memory
in it (measured 2026-08-21: an en-US phone recording a Spanish meeting sent
`language: "en-US"`, and memory landed in English, which is the intended
translate-on-import behaviour). Two meanings on one key across a hop is how
a field gets silently misread, so the spoken language is its own key. If
CQ should also learn the spoken language, send `transcript_language` on
capture too; GP will forward it once CQ names it on their side.

Absent, null or malformed values are treated as not sent. Nothing 4xxs.

## What GP does with it

- Chat route: appends a server-side line to the system prompt naming the
  language and forbidding refusal or a request for another language. A user
  who writes in a different language is answered in theirs. Placement is
  GP's (prompt composition doctrine); the client never writes the line.
- Report route: the stated language wins over `Accept-Language` for the
  report's locale directive. The device locale says what the UI is in, not
  what the people in the room spoke.
- Served recipe (independent of the field): every transcript-bearing prompt
  in protected-prompts (summaryPrompts full/delta/consolidation, analysisPrompt,
  analyzeSessionPrompt, reanalyzeSummaryPrompt), in every shipped locale,
  now ends with a rule to write in the transcript's language, summarize a
  noisy transcript rather than refuse, and never ask for another language.

## Proof it is applied

Not a 200 on the config push. A real summary call carrying
`metadata.language: "es"` whose `usage_log.raw_request` system prompt ends
with the `TRANSCRIPT LANGUAGE: es` line, and a Spanish transcript that comes
back summarized in Spanish.
