Subject: TR InterviewScorecard: grade against the candidate's own prep when it's present

Hi GP team,

TR now lets a candidate import their own question list for a rehearsal, with a prepared answer and scoring notes per question (TR 817e3e0). When a question came from that list, the tr_response_analysis / InterviewScorecard user message carries two extra lines after the candidate's answer and follow-ups:

Candidate's prepared answer: <their text, verbatim>
Candidate's notes on what to hit: <their text, verbatim>

Both lines are optional and per question; questions without prep look exactly as they do today. The client adds no instructions, so the served rubric decides what to do with them. The gp-harness scorecard builders mirror the lines (optional "prepared" and "notes" keys on a fixture question).

What we're asking for: one rule in InterviewScorecard (jobInterview guidance or the system prompt, your call), along these lines:

When a question includes the candidate's prepared answer or notes, treat them as the candidate's own target. In whats_missing and rewrite_hint, call out facts, numbers, names or lessons from the prepared answer that the spoken answer dropped or got wrong, quoting the prepared value. Treat anything the notes say to avoid as a hard miss and name it. Never grade on word-for-word similarity to the prepared answer, and keep the rating anchored to the usual rubric: the prep sharpens the feedback, it does not set the score.

Why: we simulated the current served prompt (local copy of response-analysis.json, August 19 revision) with and without the lines on two answers seeded with mistakes. With the lines, the coaching picked up the candidate's exact facts and lessons (the six-week swap, "bring a pilot and a metric"). In both runs, though, it missed every deliberate violation: it named a client the notes say never to name, said 30% where the prep says 40%, and contradicted the notes on what he disagreed with. Score and ratings were the same both ways (70 overall). So the data arrives and gets used loosely, but nothing tells the grader to check it.

A harness case once the rule is live: question 1 with a prepared answer containing a number, a spoken answer with a different number plus a name the notes forbid; pass when whats_missing names both.

Thanks!
