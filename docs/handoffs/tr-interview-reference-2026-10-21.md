# TR to GP: the interview reference (new call type) and Live Copilot lessons

2026-10-21, from the Tech Rehearsal session. PROPOSAL. Nothing is built on
either side. Under the standing process: this contract, then GP config, then
the promptless flip, then harness verification with a heads-up.

## Why

Scott's real interview on 2026-10-20 (EMIDS, Senior Conversational AI
Architect, PolyAI voice program) went badly. The interviewer spent most of
the call on hands-on design questions the prep never anticipated. The
Copilot also suggested LUIS, which is retired, and got corrected live. SS Dev
replayed the interview's 10 questions through several models ($3.10 total).
The full evidence is in `/Users/scottguida/Documents/ss-interview-replay-2026-10-20/`:

- `RESULTS.md`: rounds, scores, latency, findings.
- `EMIDS-interview-reference-v3.md`: the tested reference, 36K chars, scored 9/10.
- `answer-key.md`: what each answer should contain.
- `live-prompt-final.txt`: the winning live prompt (v4).
- `harness/`: replay.py and all-results.jsonl.

Scott's direction: Tech Rehearsal takes a job description and a resume,
researches ahead of time to anticipate the questions, and produces a
reference document that a live Copilot carries into the interview. TR owns
producing it. ShoulderSurf only consumes it: a single .md, imported through
References > Add as Text and pinned to the interview Quick Prompt. The whole
text is sent with every live query, so the fast-answer layer has to come first.

## 1. New call type: `tr_interview_reference`

Generates the reference as markdown. It needs its own call_type, because it
needs its own routing: a model with web search at generation time, and a
much larger output ceiling than the brief.

**Model and settings (proposal, GP owns the final call):** Sonnet 5 or 5.5
with web search ON. The replay found that web search at prep time is what
caught the current product names: Azure AI Foundry is now Microsoft Foundry,
LUIS retired March 31 2026, Voice Live GA October 2025, ACS bidirectional
streaming GA May 2025. The model can't look those up mid-answer.
Thinking can stay on here; this isn't a live path. Output is about 9K to 10K
tokens for a 30K to 36K character document, so set the ceiling to 16384.
**Stream it** (SSE, like `tr_brief_analysis`). A one to two minute wait
needs progress.

**Request (client sends data, GP owns every word of the prompt):**

```json
{
  "call_type": "tr_interview_reference",
  "prompt_mode": "InterviewReference",
  "metadata": {"scenario": "interview", "action_id": "<prep action id>"},
  "inputs": {
    "candidate_name": "Scott Guida",
    "role_title": "...", "company": "...", "end_client": "... or null",
    "interview_date": "2026-10-20 or null", "stage": "phone_screen|technical|onsite|... or null",
    "interviewer": {"name": "...", "title": "...", "research": "<tr_research_interviewer output or null>"},
    "job_description": "<full text>",
    "resume": "<full text>",
    "company_research": "<tr_research_company output or null>",
    "match_analysis": "<tr_match_analysis output or null>",
    "prep_brief": "<tr_brief_analysis output or null>",
    "candidate_notes": {
      "why_looking": "<candidate's own words, or null>",
      "past_stack_confirmed": "<what they used hands-on at recent jobs, their words, or null>",
      "anything_else": "<free text from intake, or null>"
    }
  }
}
```

The two `candidate_notes` fields are NEW inputs TR's intake will collect.
The replay showed both move the score. Without `why_looking`, every model
invented a reason. Without a confirmed past stack, models drifted to the new
company's stack when asked "what did you use at Kore". When either is null,
the document must say so in plain words, not fill the gap.

**Response:** the markdown text, plus the usual usage and stop_reason. The
client refuses `stop_reason == max_tokens` here like everywhere else.

**Document shape the prompt must produce** (from the tested v3, in this order):

- PART 1, FAST ANSWERS:
  - Header: role, company, interviewer name and title, date, end client.
  - What they're hiring for: the JD distilled, never pasted.
  - Who the company is.
  - The candidate in 30 seconds.
  - Why I'm looking, in the candidate's own words, or "not provided".
  - 6 to 10 stories as situation, action, result, with numbers.
  - Exact facts the role will probe.
  - Likely questions with answer outlines.
  - 6 to 8 questions to ask the interviewer.
  - A numbers cheat sheet.
  - A short glossary.
- PART T, TECHNICAL PLAYBOOKS, derived from the JD's requirements, the
  company's stack and news, and the interviewer's background:
  - A reference architecture per cloud the JD names, call in to answer out,
    with exact current service names.
  - AWS and Google equivalents.
  - The core flow step by step.
  - Scenario playbooks ("metric X is at 50%, get it to 75%"): funnel, usual
    root causes, fixes, how to measure.
  - Non-technical failure causes.
  - Fixing conversational issues.
  - First 90 days, tied to what the role is for.
  - "What did you use at <last job>", confirmed facts only, with an explicit
    NOT CONFIRMED line for anything else.
- PART 5, DOMAIN DEPTH: the employer or product the role centers on (the
  PolyAI primer in v3).

Rules:
- Facts about the candidate come only from the resume or candidate_notes.
  Anything unconfirmed is marked.
- No full resume or JD text.
- 20K to 35K characters.
- Plain markdown.
- No em or en dashes. The model copies what it sees, so the prompt itself
  must contain none.

Non-interview scenarios (negotiation, pitch, personal) are out of scope for
v1. Return a 400 for scenario != interview rather than producing something
generic.

**Gating:** this belongs under the TR plan gating (TR repo
`docs/tr-plan-gating-2026-10-19.md`). Proposal: paid only, counted inside
the prep's action_id (no separate count), with refusal code
`techrehearsal_plan_required` for free and anonymous. Scott has not ruled
yet; I'll confirm.

**Cost:** about 15K input tokens and 9K to 10K output on Sonnet 5 ($2/$10),
plus search fees, so roughly $0.12 to $0.20 per reference. That's well
inside the $25 paid backstop.

## 2. Live Copilot lane (`tr_query`, Copilot surface) lessons

TR's Live Copilot runs on `tr_query`, which is GP-owned. The replay's
findings apply directly:

- **Latency:** default thinking was the killer: 6 to 10 s to the first word,
  14.7 s live. The winner was Sonnet 5.5 with reasoning effort LOW (it
  can't be fully disabled), with a first word at about 1 s. Haiku 5.5 is as
  fast but runs long and misses subtle rules. Please route the Copilot
  surface of `tr_query` (metadata.surface == "copilot" once the client
  sends it) to Sonnet 5.5 low reasoning, and keep job_chat / session_chat
  where they are. That's per-surface routing, which matches the "per-mode
  routing" small feature we discussed in August.
- **Prompt rules that held up** (v4, in `live-prompt-final.txt`):
  - Find the question by its wording, not speaker labels.
  - Answer only the latest question.
  - A "Say:" line under 30 words, then at most 3 short specifics.
  - Facts about the candidate only from the reference.
  - For past-job questions, answer about that job first, confirmed facts only.
  - Never volunteer "I haven't"; use a "Bridge:" line only when asked directly.
  - Under 75 to 90 words.
  - No dashes.
  - Keep unrelated memory out.
- **Unfixable by prompt:** a tap before the question finishes answers the
  previous topic (3 of 10 early replays). That's a client UX problem, and
  TR's to solve.

## Asks

1. Confirm or reshape section 1's contract (call type, request fields,
   streaming, ceiling, model with search).
2. Size the GP work.
3. The `tr_query` Copilot-surface routing and prompt change in section 2.
4. Holding: don't build until Scott confirms paid-only. I'll relay.

The TR session is `techrehearsal-26`.
