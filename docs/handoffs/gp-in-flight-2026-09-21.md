# GP session close, 2026-09-21 (session `cloudzap-35`)

**Read this first when continuing.** It supersedes the ContextMeter handoff
of 2026-09-20 04:04 and the memory index lines it points at. Everything here
was true at 2026-09-21 ~21:45Z. prod = main = `7de46f56`. Zero PRs open except
#1016, which is parked on purpose. Disk on the Mac: 86 GB free of 1.8 TB.

## 1. What shipped this session (all merged, all deployed, all verified on prod)

| PR | What | Verified how |
|---|---|---|
| #1008 | raw SSE captures of the N-400 sentence stream for the client, `qa/fixtures/n400-stream/` | client built against them, live turns matched |
| #1009 | rename of a log label that shadowed the dedupe key name | no behaviour change |
| #1011 | a refused send (429, 402) no longer strands its top level `turn_id` as `turn_in_progress` for 240 s | tests, 3 sabotages exact; not yet observed on prod |
| #1010 | TypeSafe (Jev) as the offer reply judge with Haiku fallback, a breaker, `typesafe_calls` table, a dashboard panel | startup log shows the SM secret filled; `/admin/typesafe-health` answers |
| #1013 | one hour prompt cache on the interviewer lane's system prompt | usage_log row: turn 2 READ 24,085 tokens 12.5 min after turn 1 wrote them |
| #1015 | N-400 evidence support check: Jev marks a minted option her words do not establish (`facts_unsupported`), never drops | 23 tests, live in primary, 3 checks so far, none marked |
| #1017 | `companion` config document for ShoulderSurf's Mac companion download link | fetched from outside on both hosts; SS client half on their main `4f9f892`, snapshot `4d95d40` |
| #1018 | covering index for the per turn app budget spend query; `chat_preflight` now logs for streamed n400 turns | prod planner says `USING COVERING INDEX idx_usage_user_app_date_cost` |

Also done, not a PR: `CZ_TYPESAFE_MODE=primary` in `/opt/ghostpour/.env.prod`
(Scott's word), SM secret `typesafe-api-key` created by Scott, read only prod
query script at `~/ghostpour-ops/scripts/prod_ro_query.sh` (works for Claude
too, the prod read block is lifted for that script only).

## 2. The big finding: the slow N-400 first turn was OURS

Three rulings in a row were wrong, and the last one was mine:
1. previous session: "Anthropic queue tail, leave it alone"
2. me, 2026-09-20: "cold prompt cache, 5 minute TTL" (built #1013 and the
   warm up #1016 on it)
3. the row Scott ran: Anthropic's ttft was 1,215 ms cold and 1,240 ms warm.
   Stream open was 8.2 s. About SEVEN SECONDS were inside GhostPour before the
   request left.

Cause (read from code and prod's planner; "it is the whole 7 s" is inferred,
never timed cold): the flat app budget sums the user's month spend on every
turn; the only index was not covering; the N-400 QA account has 3,710 rows this
month at ~63 KB each (226 MB of a 343 MB db); cold disk, thousands of random
reads. A real applicant has tens of rows and never met it. Fixed by #1018.

**Still unproven:** one cold streamed harness turn (10+ idle minutes) opening
in ~1.5 to 2 s. The `chat_preflight` line now exists for streamed turns and
will say where any remaining time goes. Asked of the auditor.

Lesson recorded in memory: a mechanism that fits the observable is a
hypothesis; measure before shipping. My own probe (`scratchpad` is gone,
numbers are in memory) showed a fully cold 24k prefill streams its first token
in 1.58 s.

## 2b. IN FLIGHT AT EXIT (finish this first, next session)

**✅ DONE 2026-09-22 00:40Z (session cloudzap-81):** #1019 merged `787a4c0` and
deployed; the scoped sync ran inside the container against that sha (both
leaves `synced`); from outside, `/v1/config/companion` serves version 2 with
the trimmed comment, equal to the bundle, and `X-Config-Version: 2` answers
`{"changed": false, "version": 2}`; the ShoulderSurf session was told to
re-pull. Note for next time: a docs-only push to main does NOT trigger Build &
Deploy, so `/health` keeps the last CODE sha (`787a4c0`), not main's tip.
The original steps, for the record:

Scott ruled at ~21:50Z: **close #1016** (DONE, closed with reason) and **trim
the companion `_comment`** (PR #1019, in CI at exit, a background job merges
it on green; it may already be on main). Merging does NOT change what prod
serves. To finish:

1. Confirm #1019 merged and prod reports its sha (`/health`).
2. Sync the two leaves on prod, from the deployed bundle:
   `POST /webhooks/admin/config/companion/sync-from-bundle` with
   `{"keys": ["/_comment", "/version"]}` (X-Admin-Key, run inside the
   container; the same endpoint the n400 prompt sync uses, see
   `scripts/sync_n400_prompt.py` for the call shape).
3. Verify from OUTSIDE: `curl https://api.ghostpour.com/v1/config/companion`
   serves `version` 2 and the comment starts "Destination for the Set up on
   my Mac button". At exit prod still served version 1 with the old comment.
4. Tell the ShoulderSurf session to re-pull the snapshot (their
   `scripts/refresh-remote-configs.sh`) before their next release.

## 3. Open decisions, Scott's

1. ~~Close #1016~~ DONE 2026-09-21, closed by Scott's ruling. The N-400
   client (build 73+) fires it at launch and Talk open; prod answers 400, the
   client swallows it. Only 2 such 400s seen in 24 h, which is fewer than
   expected; the auditor should check their device log.
2. ~~Trim the public `_comment`~~ RULED yes, see 2b for the unfinished sync. The route needs no auth
   and SS bakes the document into the app bundle each release. The comment
   spells out App Review reasoning. Recommend trimming to one neutral line:
   dashboard edit, version 2, tell SS to re-pull. Must land before SS's next
   release if wanted.
3. **Comp codes expire 2026-10-01**, by hand in App Store Connect. Scott asked
   to be reminded at the start of every session until then.
4. **Permission rule for prod reads**: no longer needed, the script works.
   The self modification block stops Claude editing its own rules anyway.

## 4. Owed to other teams

**N-400 auditor** (`fable-auditor-d8`, and it changes name on resume):
SendMessage to them FAILED three times ("not reachable") while their messages
kept arriving. Everything is in `docs/handoffs/gp-to-auditor-2026-09-21.md`
(read sections 8 and 9 first). Scott must point them at it. They owe GP: one
cold harness turn (above); ruled cases of inferred option mints for
`qa/jev_evidence_support_eval.py`; status of the older N-400 rulings (self
employed no ZIP export dead end, compound given names). They should remove the
launch warm up from the client when convenient.

**ShoulderSurf** (`shoulder surf handoff continuation`, reachable): nothing
owed either way. Next event is approval day: dashboard edit to `companion`
(channel `appStore`, `appStoreURL` = `https://apps.apple.com/app/id<digits>`,
version bump). Their Mac companion pricing question (plan gate via
`/v1/usage/me`) was answered on 2026-09-20; nothing to build unless Scott
greenlights.

## 5. Not started, named this session

- Jev breaker alert: opening is only visible on the dashboard panel and as a
  `typesafe_breaker_opened` journal line.
- The Jev dashboard panel has never been opened in a real browser (render
  code was run in node only).
- The warm up race fix (a real turn waits behind an in flight warm up): moot
  if #1016 closes.
- Moving the file ask classifier to Jev: not worth it, ~25 calls a month.
- A-Number wording: the lane says "A-Number" though prompt and agenda say
  "USCIS number"; low priority, the app shows its own hint.
- Older items untouched: ja share card CJK font, SS translation `title` and
  sender language field.

## 6. Corrections I made to my own statements this session (so nobody re-learns them)

- "Buffered turn logs its reason nowhere": FALSE, `n400_stream_buffered` has
  logged since #1003.
- "Bifrost edge default deny needs Scott's hand": it was flipped on 09-18; I
  asked from a stale index line. Extending it to GP's host: Scott ruled NO.
- "A full config fetch returns `changed: true`": FALSE, it returns the
  document; docstring fixed.
- "The config route is authenticated": FALSE.
- "Jev offer reply judge is a latency win": true per call, but ~25 calls a
  month; check VOLUME before optimising latency.
- "Cold prompt cache causes the slow first turn": FALSE, see section 2.

## 7. Gotchas worth carrying

- `main` is strict: every PR must be updated to main before merge, ~15 min CI
  each. `scratchpad/land.sh` did update, wait, merge; it is gone with the
  scratchpad, three lines to rewrite.
- Stacked PRs get auto closed when their base branch is deleted on merge;
  rebase and reopen.
- Module level `tj._LIVE` tasks from one test's event loop break later tests
  on CI's Python 3.12 (local 3.14 hides it); both Jev test files clear it
  around every test.
- `_percentile()` in webhooks.py takes a FRACTION (0.5), not 50.
- A local `data/remote-config/` overlay shadows the bundle in local runs (the
  live warm up proof read prompt v2, 13,647 chars, not v38's 73,390).
- The auto mode classifier denied: prod usage_log reads (now solved by the
  script), `gcloud secrets create`, `gh pr merge` (until Scott said "do it
  all"), and editing `~/.claude/settings.json` (self modification).

## Addendum, session cloudzap-81, 2026-09-22 00:20Z to ~07:10Z

**Prod = `b32ea6c` (code), main tip is docs on top of it.** Read from
outside on `/health` after each deploy.

Shipped tonight, in order:
1. **Section 2b DONE**: #1019 merged `787a4c0`, companion `_comment` trimmed
   and `version` 2 synced on prod from inside the container, verified from
   OUTSIDE (served == bundle), ShoulderSurf re-pulled (their `de5fefc`).
   Note: a docs-only push to main does NOT trigger Build & Deploy.
2. **Auditor loop closed**: they reached this session at
   `uds:/tmp/cc-socks/80599.sock`; the file channel is
   `docs/handoffs/gp-to-auditor-<date>.md`, written FIRST, sent second.
   Sections 10 to 16 of the 09-21 auditor doc hold everything: both owed
   items closed (self-employed ZIP derived on the client; compound names,
   the lane asks), the cold-turn proof (client half 0.23 s; SERVER HALF
   STILL OWED, the journal read was blocked in this session), the labelled
   option mints, `choice_fields`, the marker measurement, the retracted
   06:14Z turn, the turn-id collision closed by their nonce.
3. **#1020** `c96db67`: the auditor's 22 real labelled option mints through
   the PRODUCTION evidence check. **The check catches 1 unsupported mint in
   4 on real turns**; the synthetic eval overstated it. Runner
   `qa/jev_labelled_option_mints_eval.py`.
4. **#1021** `b32ea6c`: both `choice_fields` consumers (evidence-check
   per-field scope; outside-options marker on the catalogue, measured
   first across 3,047 turns: 3 marks, all true outsiders). INERT until the
   client build carrying `metadata.choice_fields` (their `4b55b11`) reaches
   devices.
5. Scott's rulings tonight: "whatever the auditor is asking for, approve
   it" (memory `feedback_auditor_asks_preapproved_2026_09_21`).

Owed / not started:
- **Jev-question iteration** for the evidence check (the 1-in-4). Fixtures:
  `qa/labelled-option-mints.json`; the auditor's note: two of the three
  marker hits are the lane INVENTING an id when unsure of an option.
- **Server half of the auditor's cold-turn proof** (turn `26655b63-t_001`,
  2026-09-22T00:29:47Z): a prod journal read Scott has to allow or run.
- The eval runner should pass `metadata.choice_fields` from the wire once a
  run carries it.
- Comp codes die 2026-10-01 (Scott, by hand).

Lesson with a receipt: a CONFLICTING PR gets no `pull_request` run at all
and GitHub says nothing; #1021 sat four hours. `gh pr view --json mergeable`
first. Handoff sections now land on main directly.
