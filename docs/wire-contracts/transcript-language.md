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

## The report says which language it was written in (2026-08-26)

Every report response carries two additive top-level fields:

- `report_language` (string): the BCP-47 primary subtag the LANGUAGE
  directive was built from, e.g. `"es"`. `"en"` when no directive was
  applied, because the model wrote English then and null would be a lie.
  On `GET /v1/meetings/{id}/report` for a report cached before this
  shipped the value is `null`: untagged, not English.
- `transcript_language` (string or null): the client's stated value,
  echoed raw as sent (`"es-US"`). Evidence, not something to merge on.

Present on `POST /v1/meetings/{id}/report` (real and canned), on the
cached `GET`, and on `POST /v1/reports/render`, where it is only an echo
of the optional request field `report_language` (rendering does not
generate, so it never discovers a language; send the value you stored).

SS stores `report_language` as the report's generated-language tag and
its CloudKit merge refuses a cross-language overwrite. Why: on 2026-08-24
a build-335 regeneration of a Spanish meeting followed the device locale
(that build predates this field), came back English, and sync replaced
the Spanish report with it. Same pair lands in `usage_log`
`metadata.report` on every report row (#805).

## Report writes have a build floor (2026-08-26)

`POST /v1/meetings/{id}/report` from a build below the served
`report_write_min_build` (app-versions.yml, `platforms.ios`; 1193 for
Shoulder Surf, the first build that states `transcript_language`) is
refused with **`412 Precondition Failed`** and the FastAPI `detail`
envelope, like every other coded report error:

```json
{"detail": {"code": "report_build_floor",
            "message": "This build cannot generate reports; update Shoulder Surf.",
            "min_build": 1193, "app_build": 335, "recovery_action": "update_app"}}
```

Reads (`GET`), render, chat and config are untouched: an old build keeps
working, it just cannot generate or overwrite a report. The build is read
from `X-App-Build`, else from the leading `<CFBundleName>/<build>` token
of a default URLSession User-Agent when it names the app
(`user_agent_app_name` beside the floor; builds before 648 sent no
`X-App-Build`). No readable build, no floor configured, or an app without
one: allowed, never guessed. Not 426: that is the app-wide hard gate.
