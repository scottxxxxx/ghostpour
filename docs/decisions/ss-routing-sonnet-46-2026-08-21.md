# ShoulderSurf routing: Sonnet 4.6 on every chat lane, Sonnet 5 retired (2026-08-21)

**Ruling (Scott, 2026-08-21, relayed by CQ):** Sonnet 4.6 on every
ShoulderSurf chat lane, every tier including Free, for now. Sonnet 5 is
retired from ShoulderSurf routing. Free rides its monthly cost cap on 4.6;
lowering Free to Haiku later is the named escape hatch if cost needs it.
TechRehearsal routing is untouched (their app, their call).

## Basis: measured cost, latency and a ceiling defect. NOT a quality result.

The eval that preceded this (tests/evals/sonnet_46_vs_5/, PR #736) could
not separate any arm from any other. In eight blind Haiku-vs-Sonnet floor
pairs the judge split 4 and 4, and across all 36 pairs the right-hand
column won 26 times whatever model sat in it. **Quality is unmeasured, not
equal.** Do not let this ruling be cited later as "tested equal, cheaper
wins"; that sentence is how a phantom eval gets born, and this document
exists partly to stop it.

What WAS measured, replaying 49 of Scott's real requests byte-identical
except the model string, provider default against provider default:

- **Tokenizer:** Sonnet 5 bills about 37% more input tokens than 4.6 or
  Haiku on identical bytes, on every lane. After Sonnet 5's intro pricing
  ends on 2026-08-31 the two list at the same per-token price, so this is a
  permanent per-request premium, not a temporary one.
- **Default thinking:** with no thinking block sent, Sonnet 5 spent thinking
  tokens on every lane (per lane min/median/max: meeting_chat 0/359/602,
  project_chat 0/542/1713, analysis 0/355/589, report 2015/3044/9192). 4.6
  and Haiku spent none. Net post-08-31 cost per request: 1.5x on
  meeting_chat, 2.0x on project_chat, analysis and report.
- **Latency:** Sonnet 5 20 to 45% slower at the median; report tail of 80
  and 122 seconds against 4.6's 41-second median.
- **Ceiling defect:** at the report lane's live max_tokens of 4096, Sonnet 5
  hit the ceiling on 10 of 12 reports, three with no visible text at all.
  Shipping Sonnet 5 on report would have required raising the ceiling
  first. 4.6 keeps the lane's 0.2 temperature pin (it accepts the key) and
  the 4096 ceiling is not a constraint for it (0 of 12 hits).
- **One observation, n=1:** Sonnet 5 returned "Output blocked by content
  filtering policy" on a benign thank-you-email ask, succeeding on retry.
  The other arms produced no such failure across the corpus.
- **Haiku:** about one third of 4.6's cost and one half its latency on every
  lane, and not distinguishable from a Sonnet by the judge in this pass at
  n=8. That is the escape hatch's evidence, such as it is.

## What the file change is (routing v35 to v36)

The six chat lanes (meeting_chat, meeting_chat_follow_up, project_chat,
project_chat_follow_up, query, query_follow_up) move to
anthropic/claude-sonnet-4-6 on free, plus, pro and automation. analysis
and report lose Sonnet 5 on pro and automation (retired) and keep Haiku on
free and plus. summary and artifact_generation are unchanged.

Interpretation choices made here and flagged for confirmation, because
the ruling named "chat lanes": analysis and report on free/plus stay on
Haiku; summary stays on Haiku; artifact_generation free/plus stay on
Haiku. Widening any of those is a separate dial save.

## Cost consequence accepted knowingly

Free on 4.6 gets roughly a third of the chat turns per dollar that Haiku
would have given. Scott accepted that. The cap, not the model, is Free's
gate.

## Record-keeping

The preceding commit mirrors served v35 into the repo, which had drifted
eleven dial saves from v24 with no record of who set which cell when; one
earlier ruling (meeting_chat Pro to Opus 4.7, v27, 2026-08-14) was silently
reverted inside that gap. Four of those saves also broke the
automation-equals-pro invariant that CI guards; the mirror commit is red on
that test on purpose, and this one is green. Dial saves should leave a
record; until the dashboard writes one, the repo mirror is it.
