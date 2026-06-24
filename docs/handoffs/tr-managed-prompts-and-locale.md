# GP <-> Tech Rehearsal: managed prompts + locale (living contract)

Status: evergreen. The reference both teams follow for how managed Tech Rehearsal calls are
prompted, assembled, localized, and parsed. Update the change log at the bottom as it evolves.

## The model in one line

For managed TR calls, **GP owns and assembles the prompt**. TR sends a thin payload (the user's
data + `call_type` + `locale`, with `model=auto`), GP builds the full prompt from its config, injects
any cross-cutting directives (today: language), routes to the model, and returns the result.

## Who does what

| | GP (us) | Tech Rehearsal (you) |
|---|---|---|
| Prompt content | Owns it. Authored in GP config, editable from our dashboard, no app release. | — |
| Assembly | Builds system + user message from the config + your data. | — |
| Data | — | Sends the user's data blob (with the expected section labels), per call type. |
| Routing / model | Decides the model when you send `model=auto`. | Sends `model=auto`, no provider/model. |
| Cross-cutting directives (language, formatting) | Injects centrally so it can't drift. | — |
| Render / UI | — | Renders the result, owns the app experience. |

## Why we assemble instead of you (rationale, so it's not a black box)

- **Control.** We own the exact bytes that reach the model, so cross-cutting rules (respond in the
  user's language, strip stray code fences, future formatting/safety) are injected once, centrally,
  and can't drift as prompts migrate into our configs.
- **Wire.** The heavy system prompts never travel the wire. You send a thin data payload; the prompt
  lives on GP. That's less per-call bandwidth than shipping an assembled prompt up every time.
- **Compute.** Assembly is string substitution (microseconds, no model call). The model inference is
  the cost and it runs through us either way, so there's no GP load argument against assembling.
- **Escape hatch preserved.** The client-assembled path (we serve you the prompt config, you fill it
  in) still exists and is the right model **if you ever run a prompt against an on-device or
  bring-your-own-key model with us out of the loop**. It is not used on the managed path. We keep it;
  we just don't run managed calls through it.

## The cutover (what activates this)

The GP assemble path is built and waiting. It engages for a call type the moment **TR stops sending
its own `system_prompt`** on that managed call. Until then nothing changes (your prompt is used as
is). To switch a call to GP-assembled: send `call_type`, the data blob as `user_content`,
`model=auto`, `locale`, and **no `system_prompt`**.

## Per-call data contract

Each managed call sends `call_type` + `user_content` (the data blob). GP substitutes it into the
config's template (empty template = passthrough) and owns the system prompt. The blob must carry the
section labels the prompt expects:

- `tr_parse_jd` — the job posting text.
- `tr_parse_resume` — **contract still owed by TR.** We deliberately left this one unconfigured
  rather than guess your parser's expected shape. Send us the request/response shape and we'll author it.
- `tr_mock_interview` — role + company/interviewer background + question, with the section labels
  (COMPANY BACKGROUND / INTERVIEWER BACKGROUND / ROLE / QUESTION N).
- `tr_response_analysis` — role + the Q&A transcript.
- `tr_match_analysis` — resume + JD, and the blob must include the **ROLE EMPHASIS AXES** = the JD
  dimension labels from `tr_parse_jd`, so the radar labels line up.
- `tr_research_interviewer` — a short text plus the LinkedIn **screenshot in `images`** (vision call).
- `tr_research_company` — company name / context (routes to a search-grounded model).

## Locale (model output language)

You already send a `locale` field on every managed call (bare ISO code, e.g. `es`, `en`).

- **GP injects a language directive** into the managed prompt based on `locale`, so the model answers
  in the user's language. `locale` of `en` or missing = English (no injection).
- This is applied to **all** managed calls, including any that still send their own `system_prompt`
  during the migration (GP appends the directive), so you don't bolt it on per call and it can't drift.
- **Structured calls keep English keys.** For the brief, the match analysis, and the debrief
  scorecard, the directive instructs the model to **translate only the human-readable string values
  and keep every JSON key and the structure in English.** Your exact-key parsing stays intact. GP
  tests these in Spanish and guards that the keys come back English before relying on it.
- **Format:** bare language code is what we want; we map it to the language. Switch to a full locale
  (e.g. `es-MX`) only if you later want region-specific dialect, then we'll use the full value.

## Open items

- **TR:** `tr_parse_resume` request/response contract (so we can author the prompt).
- **TR:** complete the `system_prompt` cutover per managed call so the GP-assembled prompts engage.
- **GP:** build + ship the locale injection; confirm a Spanish end-to-end run with TR.

## Change log

- 2026-06-24 — Initial: server-side assembly for managed TR calls + central locale injection;
  client-assembled path reserved for a future on-device/BYOK case. Drafted for TR review.
