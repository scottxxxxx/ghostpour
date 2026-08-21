# Transcript language: `metadata.language`

Added 2026-08-21 after a Spanish meeting, recorded through ShoulderSurf's
own language picker, came back with an English refusal where its summary
belonged. The served summary recipe carried no language rule and nothing
on the wire named the language, so the model inferred and refused.

## The field

`metadata.language`: a BCP-47 tag (`es`, `es-MX`, `pt-BR`), the language the
meeting was held in, as the client knows it from its picker. Send it on
every call that carries a transcript: summary (full, delta, consolidation),
analysis and re-analysis, meeting chat and project chat turns that include
transcript content, `POST /v1/reports` (top-level `language` there, since
reports have no metadata dict), and `POST /v1/capture-transcript` (where it
already existed and is forwarded to CQ).

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
