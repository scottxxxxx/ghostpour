# `tr_match_analysis` — résumé/JD fit analysis (wire contract)

The response GP returns for the Tech Rehearsal `tr_match_analysis` call:
a recruiter-style fit score for one résumé against one job, plus the
strengths, gaps, and fit-radar data the TR app renders. This doc is the
diff target for that JSON shape — especially the `gaps[]` objects, which
back the "Strengthen" flow.

Last updated: 2026-06-30. Served prompt version: 6 (`config/remote/tr-match-analysis.json`).

## Who assembles the prompt (read this first)

`tr_match_analysis` can run with either prompt source, and **this
determines which fields you get back**:

- **GP-assembled (recommended).** The client sends `call_type:
  "tr_match_analysis"` and **no** `system_prompt`. GP assembles the
  current server-side prompt (`tr-match-analysis.json`, v6) and the
  response includes every field below, including `closeable` and
  `share_prompt`. Future prompt improvements land with no client
  release.
- **Client-embedded (legacy).** The client sends its own
  `system_prompt`. GP relays it verbatim and does **not** assemble.
  The response shape is then whatever that embedded prompt asks for —
  if it predates `closeable`/`share_prompt`, those fields come back
  **`null`/absent** no matter what GP's served prompt says, because
  GP's prompt isn't used.

> As of 2026-06-30 TR sends an embedded `system_prompt` that does not
> request `closeable`/`share_prompt`, so those two fields are arriving
> `null` in production even though the UI is wired to render them. To
> get them populated, either drop the embedded `system_prompt` (let GP
> assemble v6) or copy the two fields + their guidance into the
> embedded prompt. See "Getting `closeable`/`share_prompt` populated".

## Response shape

```jsonc
{
  "pct": 0,          // int 0-100, overall match
  "skills": 0,       // int 0-100
  "experience": 0,   // int 0-100
  "keywords": 0,     // int 0-100
  "level_fit": 0,    // int 0-100, seniority/scope fit

  "strengths": [
    { "text": "specific thing the candidate brings, grounded in the résumé",
      "level": "strong" }          // "strong" | "ok" | "weak"
  ],

  "gaps": [
    {
      "keyword": "SQL / Postgres / MySQL proficiency", // short label; a REAL JD requirement
      "severity": "medium",                            // "high" | "medium" | "low"
      "closeable": true,                               // bool — see below
      "share_prompt": "Share a specific project where you wrote raw SQL against a Postgres or MySQL database — schema design, query optimization, or migration.",
      "fix": "Name Postgres/MySQL explicitly in a skills entry or bullet rather than only 'SQL data modeling with Prisma'."
    }
  ],

  "fit_by_dimension": [
    { "label": "matches a ROLE EMPHASIS AXIS verbatim",
      "role_level": 1.0,        // 0.0-1.0, the role's bar on this axis
      "candidate_level": 0.5 }  // 0.0-1.0, the candidate on the SAME scale
  ]
}
```

## `gaps[]` field semantics (the Strengthen flow)

Each gap is one missing/weak area that is a real requirement of *this*
JD. The five fields:

| field | type | meaning |
|---|---|---|
| `keyword` | string | Short label for the gap. Shown as the "GAP TO CLOSE" title. |
| `severity` | `"high" \| "medium" \| "low"` | How important to this JD. Order is most-important-first. |
| `closeable` | bool | **Can the candidate close/narrow this by sharing real experience they may not have surfaced** (an adjacent project, a tool they've used, an outcome they drove)? `false` when nothing they say could satisfy it — a proprietary platform they couldn't have used, a credential they don't hold, a raw years/scale bar. **Gate the Strengthen affordance on this:** `true` → offer the evidence input + Strengthen button; `false` → show `fix` as interview-prep guidance, no button. |
| `share_prompt` | string | **When `closeable`: one concrete example of what to share**, specific enough that the candidate can self-check whether they have it. Use it as the hint/placeholder for the evidence text field. **When not `closeable`: empty string `""`.** May also be `null`/absent if the model omits it — treat `null`/`""` as "no example, fall back to showing `fix`". |
| `fix` | string | One sentence on how to address it. For `closeable:false` gaps it's framed as interview-prep, not a résumé edit. |

Split behavior: when a requirement has both a not-closeable core (a
proprietary tool) and a closeable adjacent skill (the general method
behind it), v6 emits them as **two separate gaps** — the proprietary
part `closeable:false`, the learnable part `closeable:true` with a
`share_prompt`.

### Robustness rules for the client

- Treat `closeable` absent/`null` as **`true`** (fail toward offering the
  flow; matches how old saved reports decode).
- Treat `share_prompt` absent/`null`/`""` as **no example** — render
  `fix` instead; never block on it.
- Unknown future fields: ignore. Additive changes won't bump the shape.

## Getting `closeable`/`share_prompt` populated

These fields only appear when GP's v6 prompt actually runs. Two ways:

1. **Drop the embedded `system_prompt`** for `tr_match_analysis` (send
   `call_type` only). GP assembles v6; you get the fields plus all
   future prompt work for free, no client release. Cleaner long-term —
   it's the managed-prompt cutover already on the roadmap.
2. **Keep your embedded prompt, add the fields to it.** Copy the
   `closeable`/`share_prompt` keys into your gap schema and the v6
   guidance paragraph (closeable=true → concrete `share_prompt`
   example; closeable=false → `share_prompt:""` and interview-prep
   `fix`; split a proprietary core from its adjacent skill) into the
   prompt body. Faster, but you own keeping it in sync with ours.

The Strengthen submit (`tr_resume_enhance`) is unaffected either way:
it grounds strictly in the user's typed evidence and returns the
résumé unchanged if that evidence doesn't actually address the gap.
