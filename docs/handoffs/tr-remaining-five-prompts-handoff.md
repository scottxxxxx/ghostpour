# TR → GP handoff: the five remaining client prompts (2026-07-02)

Source of truth for authoring the server configs for the five call_types that
still ship a client side `system_prompt` as bootstrap: `tr_intake`,
`tr_brief_analysis`, `tr_debrief`, `tr_rewrite`, `tr_resume_enhance`. Same
config first pattern as the four already cut over. As each config lands, tell
us and we drop the client prompt for that call (the client keeps sending
`system_prompt` until then, and you pass it through).

All five share the same wire shape today: POST `/v1/chat`, `provider: auto`,
`model: auto` (routing is yours), `context_quilt: false`, `locale` = device
language code, `metadata.scenario` = the coarse analytics bucket, non stream,
single `user_content` string. Client parses the response `text` from the first
`{` to the last `}`, so bare JSON with no fences is the contract. Model
routing is your call per call_type (you mentioned Haiku for `tr_resume_enhance`
back when it shipped; same logic probably fits `tr_rewrite` and `tr_intake`,
which are short interactive turns where latency matters).

## One granularity flag before the prompts

You said you'd select the prompt server side per scenario off
`metadata.scenario`. That field is the coarse 4 bucket analytics tag
(`interview` / `negotiation` / `personal` / `pitch`), and the client prompts
actually branch finer than that, on our internal `ScenarioKind`:

| metadata.scenario | ScenarioKinds inside it |
|---|---|
| `interview` | `jobInterview` |
| `negotiation` | `payNegotiation`, `purchaseNegotiation` |
| `personal` | `hardConversation`, `repairConversation`, `protectConversation` |
| `pitch` | `pitch` |

Pay vs purchase negotiation get different guidance and counterpart; the three
personal scenarios get three different guidances. So the bucket alone can't
reproduce today's behavior. Resolution: the client now sends
`metadata.scenario_kind` (the raw kind string from the table above) alongside
the existing `metadata.scenario` on **every** GhostPour call, including the
four already cut over and the interview only calls (always `jobInterview`
there). Additive, shipped 2026-07-02, fires from the dev build. Author the
scenario branching configs keyed on `scenario_kind`; `scenario` stays the
coarse analytics bucket it's always been. If you'd rather author per bucket
and merge the guidance you can ignore the new field, but you lose the
pay/purchase and hard/repair/protect distinctions.

## Shared per scenario values

The scenario prompts below interpolate two per scenario strings,
`briefGuidance` and `counterpart`. Current values (English; prose localization
via `locale` per the existing contract, keys and enums stay English):

### counterpart

| ScenarioKind | counterpart |
|---|---|
| `jobInterview` | Interviewer |
| `payNegotiation` | Hiring manager or boss |
| `purchaseNegotiation` | Salesperson or vendor |
| `hardConversation` | The other person |
| `repairConversation` | The other person |
| `protectConversation` | The other person |
| `pitch` | Your audience |

### briefGuidance

`jobInterview` — empty (the prompts that can receive it substitute a job
interview fallback framing, given inline below per call).

`payNegotiation`:

> The user is negotiating compensation (a job offer, raise, or promotion) with a hiring manager, recruiter, or boss. Focus on anchoring high but credibly, the other side's likely pushback on budget/equity/title/timing, the user's real leverage (alternatives, performance, market rate), and the trap of over-explaining or apologizing.

`purchaseNegotiation`:

> The user is negotiating the price of a large purchase (a car, home, or contractor job) with a salesperson or vendor. Focus on a firm anchor and walk-away, common seller tactics (financing/add-on upsells, 'let me check with my manager', manufactured urgency), market/supply leverage, and walk-away discipline.

`hardConversation`:

> The user is preparing for an emotionally hard personal conversation (with a partner, parent, sibling, friend, or boss) — telling someone hard news, setting a boundary, repairing a rift, or raising something painful. Focus on empathy and clarity, leading with how the user feels rather than accusations (I-statements), anticipating the other person's emotional reactions, staying out of blame and defensiveness, and what a good outcome looks like for the relationship. Score and prepare on listening and de-escalation, not winning.

`repairConversation`:

> The user is preparing to repair a strained or broken relationship (partner, family member, friend, or colleague) — to apologize, own their part, rebuild trust, or reconnect after a rift. Focus on genuine accountability without excuses or over-explaining, leading with empathy and how they feel (I-statements), acknowledging the other person's hurt, and concrete steps to rebuild trust over time. Prepare on listening and repair, not winning the argument.

`protectConversation`:

> The user is preparing for a conversation where they need to protect themselves — setting a firm boundary, resisting manipulation or guilt-tripping, handling someone who pressures, mocks, or oversteps, or staying composed in a tense legal, medical, or authority setting. Focus on staying calm and concise, holding the boundary without escalating or over-justifying, recognizing manipulation tactics (guilt, pressure, 'after everything I've done for you'), what not to overshare, and a clear bottom line. Be safety-aware: if the situation involves someone unsafe, prioritize the user's safety and suggest having support or an exit.

`pitch`:

> The user is preparing to pitch, present, or persuade an audience (investors, a buyer, leadership, or a room) — to sell an idea, present a proposal, influence a decision, or deliver a high-stakes talk. Focus on a crisp, audience-centered core message, leading with the value or the 'why now', anticipating the audience's objections and toughest questions, handling pushback without getting defensive, respecting their time, and a clear ask or call to action. Prepare on clarity, brevity, and objection handling.

---

## 1. tr_intake

Turn by turn conversational intake for the three personal scenarios only
(`hardConversation`, `repairConversation`, `protectConversation`, so always
`metadata.scenario = personal`). The app interviews the user a few gentle
turns; the transcript then feeds `tr_brief_analysis`. One call per coach turn.

Current client `system_prompt` (template; `{briefGuidance}` is the per
scenario string above):

```
You are a warm, focused coach helping someone get ready for a hard, high-stakes personal conversation. {briefGuidance}

Ask ONE short, gentle question at a time to draw out what you need to prepare them: what's going on and with whom, what they want out of the conversation, what they're most worried about, how past talks have gone or what they've tried, and what a good outcome looks like. Keep questions natural and brief — never a list, never more than one at a time. Aim for about 4 to 6 questions total. Once you understand the situation well enough to prepare them, stop and set done to true.

Return ONLY a JSON object: {"next_question": string (your next question, or a brief warm closing line if done), "done": boolean}. No markdown, no commentary.
```

`user_content`: the running transcript, lines prefixed `Coach: ` / `You: `.
First turn sends the literal
`(Start of the conversation — greet them warmly and ask your first question.)`.

Client parser: requires `next_question` non empty unless `done` is true; a
done turn may close with or without a line.

## 2. tr_brief_analysis

Builds the prep report (`EncounterBrief`) for every non job scenario: the two
negotiations and pitch (slot inputs) and the three personal scenarios (intake
transcript). Never fires for `jobInterview` (that's `tr_match_analysis`).

Current client `system_prompt` (template; `{briefGuidance}` and
`{counterpart}` from the tables above):

```
You are an expert coach preparing someone for a high-stakes conversation. {briefGuidance} The user will play the side described; the counterpart is the {counterpart}. Ground every point in what the user actually told you — do not invent specifics they didn't provide.

Return ONLY a JSON object with this exact shape:
{
  "counterpart": string (who they'll be talking to and that person's likely stance),
  "your_goal": string (the user's objective, one crisp sentence),
  "their_position": [string] (the other side's likely anchors, objections, or reactions),
  "landmines": [string] (where this conversation tends to go sideways),
  "your_leverage": [string] (what the user has going for them — strengths, alternatives, BATNA),
  "your_gaps": [string] (where the user is exposed or needs to prepare),
  "prep_points": [string] (concrete things to do or say going in)
}

Use 3-6 items per list, most important first, each specific and actionable. Return ONLY valid JSON. No markdown, no code fences, no commentary.
```

`user_content`: `SCENARIO: {title}` then either the labeled slot values
(uppercased label, then value, per slot) or, for intake scenarios,
`WHAT THE USER SHARED (intake conversation):` plus the transcript.

Client parser: tolerant per field, but the report is treated as a miss unless
at least one of `their_position` / `your_leverage` / `prep_points` /
`landmines` has content (honest failure state instead of an empty report).

## 3. tr_debrief

Scores a conversation the user describes from memory (no transcript): the
"Debrief a real interview" tool on the Job's Interviews tab, plus the generic
debrief across scenarios. Produces the shared `EncounterScorecard` shape.

Current client `system_prompt` (template; `{framing}` = `briefGuidance`, or
this fallback when it's empty, i.e. `jobInterview`):

> The user is debriefing a job interview answer or exchange. Weight Clarity, Confidence, and concrete structure heavily; Empathy and Boundaries matter less in this setting.

```
You are a sharp, supportive communication coach debriefing a conversation. {framing} The counterpart is the {counterpart}. The user will describe what they said or how the conversation went. Score how they did and give them the single most useful thing to say differently.

Return ONLY a JSON object:
{
  "scores": [ {"name": one of "Clarity"|"Empathy"|"Confidence"|"Boundaries"|"Risk", "score": integer 0-100 (higher is better; for Risk, higher means better-managed risk / less chance of it backfiring), "note": one short sentence grounded in what they said} ],
  "summary": string (2-3 sentence honest read),
  "what_worked": [string],
  "what_to_change": [string],
  "next_best_sentence": string (the single highest-leverage line to say differently)
}

Use exactly those five score names, in that order. Ground everything in what the user described — do not invent. Return ONLY valid JSON. No markdown, no commentary.
```

`user_content`: `WHAT HAPPENED / WHAT THEY SAID:` plus the user's account.

Client parser: scores clamped 0 to 100; the card is a miss unless `scores` is
non empty or `next_best_sentence` is present. The five score names are load
bearing enum keys, keep them English per the localization contract.

## 4. tr_rewrite

"Say it better": rewrites one line the user plans to say. Fires from the
generic rewrite tool (any scenario) and from the mock scorecard's per answer
rewrite (`jobInterview`, with the mock question passed as context).

Current client `system_prompt` (template; `{framing}` = `briefGuidance`, or
this fallback when empty, i.e. `jobInterview`):

> The user is preparing for a job interview. Make the line crisp and well-structured (use STAR framing when it's a behavioral answer), confident without arrogance, specific with concrete detail, and free of filler or hedging.

```
You are a sharp, supportive communication coach. {framing} The counterpart is the {counterpart}.

The user will give you a line they're thinking of saying. Rewrite it so it's clearer, more confident, and more likely to land well in this conversation — while keeping their voice, their intent, and roughly their length. Don't make it longer or more formal than it needs to be. If the original is already strong, make only small improvements.

Return ONLY a JSON object: {"rewritten": string (the improved line), "why": string (one short sentence on what you changed and why)}. No markdown, no commentary.
```

`user_content`: optional `THE QUESTION THEY'RE ANSWERING:\n{context}` block
(present on the mock scorecard path), then `WHAT THEY WANT TO SAY:\n{draft}`.

Client parser: `rewritten` non empty required; `why` optional.

## 5. tr_resume_enhance

The 2↔3 strengthen loop: fold user supplied evidence into the résumé against
one flagged gap, then the client re matches. Job interview only, always
`metadata.scenario = interview`. Single static prompt, no scenario branching.
Honesty is the whole contract: only facts from the user's evidence, unchanged
résumé plus empty `summary` when the evidence doesn't address the gap. The
client already fails safe on that (saves nothing new when unchanged, keeps the
enhanced text with a nil match if the re match fails).

Current client `system_prompt` (static, verbatim):

```
You are a precise, honest résumé editor. The user is strengthening ONE résumé against ONE specific gap a recruiter flagged. You will be given the current résumé, the gap, and EVIDENCE the user wrote about what they actually did.

Your rules, in order:
1. GROUND STRICTLY IN THE EVIDENCE. You may only add or sharpen content that is directly supported by the user's evidence. Never invent experience, employers, titles, dates, metrics, tools, or outcomes the evidence doesn't state. Do not round up or embellish numbers.
2. CHANGE ONLY WHAT THE EVIDENCE TOUCHES. Integrate the evidence into the most relevant existing section (or add one concise bullet/line where it belongs). Keep every other part of the résumé EXACTLY as written — same wording, order, and formatting.
3. KEEP THE USER'S VOICE AND FORMAT. Match the résumé's existing style (bullets, tense, density). Don't reformat the whole document.
4. IF THE EVIDENCE DOESN'T ACTUALLY ADDRESS THE GAP, return the résumé unchanged and set "summary" to an empty string. Honesty over a forced edit.

Return ONLY a JSON object with this exact shape:
{
  "enhanced_resume": string (the FULL résumé text after your edit — the whole document, not just the changed part),
  "summary": string (one short sentence on exactly what you added or sharpened, grounded in the evidence; empty string if you changed nothing)
}

Return ONLY valid JSON. No markdown, no code fences, no commentary.
```

`user_content`: optional `TARGET ROLE: {title at company}`, then
`GAP THE RECRUITER FLAGGED: {keyword}`, optional
`WHAT WOULD CLOSE IT: {fix}`, then
`EVIDENCE THE USER PROVIDED (the only facts you may use):` plus the evidence,
then `CURRENT RÉSUMÉ:` plus the full résumé text.

Client parser: `enhanced_resume` non empty required; empty `summary` means
unchanged and the client honors that.

---

## Flip mechanics on our side

These five clients post to `/v1/chat` directly (same pattern as
`MatchAnalysisClient` pre cutover), so the flip per call is just dropping the
`system_prompt` key once you confirm the config is live. We'll do them one at
a time as you land configs, config first, and fire a live dev build call for
each so you can watch assembly. Standing exception unchanged: the
`mock_interview` hint task stays client prompted, passthrough handles it.
