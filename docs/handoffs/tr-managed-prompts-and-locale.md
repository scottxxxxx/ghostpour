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

**Order matters, one call at a time (agreed 2026-06-24).** An empty template is passthrough, so if
TR drops its `system_prompt` *before* GP has authored that call's config, the model runs with no
system prompt and the output falls apart. So per call: **GP authors the config first → TR drops its
`system_prompt` → we test together → move to the next call.** It is never a blanket drop across calls.

- **Pilot: `tr_parse_jd`** (simplest, lowest risk), then **`tr_match_analysis`**.
- GP signals when a call's config is in; TR drops the client prompt on that call and we test.

## Per-call data contract

Each managed call sends `call_type` + `user_content` (the data blob). GP substitutes it into the
config's template (empty template = passthrough) and owns the system prompt. The blob must carry the
section labels the prompt expects:

### Interview-prep flow

- `tr_parse_jd` — the job posting text.
- `tr_mock_interview` — role + company/interviewer background + question, with the section labels
  (COMPANY BACKGROUND / INTERVIEWER BACKGROUND / ROLE / QUESTION N).
- `tr_response_analysis` — role + the Q&A transcript. **Serves TWO prompts selected by `prompt_mode`**
  (#358, 2026-07-08): `InterviewFollowUp` → the mid-interview judge, contract
  `{should_follow_up, follow_up, stalled}`; `InterviewScorecard` (and any other/missing mode) → the
  end-of-session scorecard, contract `{overall, headline, biggest_gap_title, biggest_gap_detail,
  per_question[]}`. Mechanism: prompt configs support an optional `modes` map of per-prompt_mode field
  overrides (absent fields inherit the top level; unknown mode = top-level prompt), so send `prompt_mode`
  on every response_analysis call. The judge prompt is the pre-cutover client prompt verbatim.
- `tr_match_analysis` — **raw resume text + JD** in one blob (TR does not parse the resume separately;
  there is no `tr_parse_resume` step — see note below). The blob must also include the **ROLE EMPHASIS
  AXES** = the JD dimension labels from `tr_parse_jd`, so the radar labels line up. Structured output,
  exact-key parsed → English-keys guard applies. **Gap shape is v7 (stable):
  `{keyword, severity, closeable, share_prompt, example_excerpt, fix}`** — `closeable` gates the
  Strengthen affordance, `share_prompt` is the hint line, `example_excerpt` seeds the editable box
  (empty `""` when `closeable=false`). Full schema + client null-handling in
  `docs/wire-contracts/tr-match-analysis.md`. `fit_by_dimension` labels mirror the parse_jd axes verbatim.
- `tr_research_interviewer` — a short text plus the LinkedIn **screenshot in `images`** (vision call).
- `tr_research_company` — company name / context (routes to a search-grounded model).

> **No `tr_parse_resume`.** Confirmed by TR 2026-06-24: the résumé is never parsed as its own step;
> the raw résumé text is fed straight into `tr_match_analysis` alongside the JD, and it works well.
> So this is not a real call type — intentionally left unconfigured, no contract owed. TR will send a
> request/response shape later only if they ever build structured résumé features.

### Conversation-practice loop (negotiation / hard conversations / pitch)

A separate surface from the interview flow. These four are **live today and still send their own
`system_prompt`** (client-assembled), so they migrate under the same one-call-at-a-time cutover above.
The locale injection already reaches them (it applies to client-assembled calls too — see Locale), so
they answer in the user's language the moment we ship it, **no cutover needed first**. Contracts below
are the starting shape to firm up with TR before each one's prompt cutover.

- `tr_intake` — the user's description of the scenario to rehearse (situation + who they're talking to
  + their goal). Sets up the session. Prose output.
- `tr_brief_analysis` — produces the structured brief from the intake. **Structured output, exact-key
  parsed by the client → English-keys guard applies.**
- `tr_debrief` — post-session debrief / scorecard. **Structured output, exact-key parsed → English-keys
  guard applies.**
- `tr_rewrite` — rewrites a user line / response into a stronger version. Prose output.

## Locale (model output language)

You already send a `locale` field on every managed call (bare ISO code, e.g. `es`, `en`).
**Confirmed by TR 2026-06-24:** the signal is on the wire on **every** managed call today, including
the ones that don't build their own body (`tr_research_interviewer`, the mock calls) — those route
through TR's provider layer, which adds the `locale` field. So nothing is owed from TR here; GP is
clear to ship the injection.

- **GP injects a language directive** into the managed prompt based on `locale`, so the model answers
  in the user's language. `locale` of `en` or missing = English (no injection).
- This is applied to **all** managed calls, including any that still send their own `system_prompt`
  during the migration (GP appends the directive), so you don't bolt it on per call and it can't drift.
  This is also why the conversation-practice calls get localization before their prompt cutover.
- **Structured calls keep English keys.** For `tr_match_analysis`, `tr_brief_analysis`, and
  `tr_debrief` (all exact-key parsed by the client), the directive instructs the model to **translate
  only the human-readable string values and keep every JSON key and the structure in English.** Your
  exact-key parsing stays intact. GP tests these in Spanish and guards that the keys come back English
  before relying on it.
- **Format:** bare language code is what we want; we map it to the language. Switch to a full locale
  (e.g. `es-MX`) only if you later want region-specific dialect, then we'll use the full value.

## Open items

- **GP:** build + ship the locale injection (greenlit — TR's signal is confirmed on the wire
  everywhere); confirm a Spanish end-to-end run with TR.
- **GP → TR:** author `tr_parse_jd`'s config and signal TR to start the pilot cutover.
- **TR:** drop the client `system_prompt` per managed call as GP authors each (pilot order:
  `tr_parse_jd`, then `tr_match_analysis`).
- **Both:** firm up the data contracts for the conversation-practice calls (`tr_intake`,
  `tr_brief_analysis`, `tr_debrief`, `tr_rewrite`) ahead of migrating them.
- ~~**TR:** `tr_parse_resume` contract~~ — closed; no such call type (résumé feeds raw into
  `tr_match_analysis`).

## Change log

- 2026-06-30 — Cutover live for `tr_parse_jd`, `tr_match_analysis`, `tr_research_company` (TR dropped
  their `system_prompt`; GP assembles). Gap shape firmed to **v7**: added `closeable` (Strengthen gate),
  `share_prompt` (hint), and `example_excerpt` (editable box seed). Ready-but-not-yet-cutover (live
  server configs, same pattern): `tr_mock_interview`, `tr_response_analysis`, `tr_research_interviewer`.
  Still client-authored (no server config; contracts to firm up first): `tr_intake`, `tr_brief_analysis`,
  `tr_debrief`, `tr_rewrite`. Locale directive hardened to keep enum tokens + verbatim axis labels English.
- 2026-06-24 — TR reviewed and is on board with the model. Updates from their reply: locale signal
  confirmed present on every managed call (incl. provider-added ones) → injection greenlit, nothing
  owed by TR. `tr_parse_resume` dropped (not a real call type; raw résumé goes into
  `tr_match_analysis`). Added the conversation-practice loop calls (`tr_intake`, `tr_brief_analysis`,
  `tr_debrief`, `tr_rewrite`) to the per-call contract. Cutover order agreed: GP authors config first,
  then TR drops its prompt, one call at a time, pilot `tr_parse_jd` → `tr_match_analysis`.
- 2026-06-24 — Initial: server-side assembly for managed TR calls + central locale injection;
  client-assembled path reserved for a future on-device/BYOK case. Drafted for TR review.
