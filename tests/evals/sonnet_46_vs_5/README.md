# Sonnet 4.6 vs Sonnet 5 (with a Haiku 4.5 floor arm)

Commissioned 2026-08-21 by Scott via CQ; results due 2026-08-28, before
Sonnet 5's intro pricing ends 08-31. Decides, per lane, whether Sonnet 5
stays in ShoulderSurf's Pro routing.

Three scripts, run in order. Everything they read and write lives under
`tmp/eval_s46v5/` (gitignored: the inputs are real meeting content).

1. `generate.py`: replays real requests exported from `usage_log.raw_request`
   against each arm, byte-identical except the model string. Provider
   default against provider default: no thinking block, no output_config,
   temperature removed on every arm. Records billed usage (input incl.
   cache writes and reads, output, thinking tokens), cost at today's list
   and at post-08-31 list, latency, stop_reason, full content. Resumable.
   Report only: an extra arm `B16` (Sonnet 5, max_tokens 16000) because at
   the lane's live 4096 ten of twelve Sonnet 5 reports hit the ceiling.
2. `build_pack.py`: seeded random judged subset per lane (code picks, not a
   human reading outputs), randomized left/right, model ids stripped,
   forced choice with "no difference", Haiku floor pairs interleaved so the
   judge cannot tell them from decision pairs. Key kept in `key.json`, not
   in the HTML. Judge writes `choices.txt`, one line per pair: `P07 R`.
3. `tally.py`: code counts. Order: Haiku separation rate first (stop
   condition under 75%), then 4.6 vs 5 per lane with n and a NOISE verdict
   when the lead is within sqrt(n), then the alongside table over ALL
   generated items with thinking tokens as min/median/max, never a mean.

Stated deviations: the no-temperature 4.6 arm on report is NOT live 4.6
(live pins 0.2; Sonnet 5 rejects the key and the adapter drops it), so on
that lane a 4.6 win validates a configuration nobody runs and a 5 win
validates one that would ship. One meeting_chat item (code_execution
sandbox loop) and one project_chat item (attached document's base64 is
redacted in the log) are excluded from everything.

Cleanup: GP deletes `tmp/eval_s46v5/` on 2026-09-05 unless Scott rules keep.
