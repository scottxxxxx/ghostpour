# GP in flight, session close 2026-09-30

Written 2026-09-30 about 22:10Z (5:10 pm CDT) by the GP session (cloudzap). It
supersedes the 09-24 close doc for current state; that one stays for history.
Everything below was read from prod, main or GitHub at writing time unless it
says otherwise.

## What is live

Prod runs `68129c9` (/health `68129c982b31`), which is main. The served N-400
interviewer prompt is v49, 98,235 chars, read back by string.

Shipped 09-28 to 09-30, in merge order:

| PR | What | Proved |
|---|---|---|
| #1049 | `qa/probe_runner.py`: estimate before sending, $5 cap, half-price Message Batch by default, spend ledger `qa/runs/spend-ledger.jsonl` | dry runs, a refusal, a real 2-request batch |
| #1051 | BYOK catalog v24: Sonnet 5.5 direct, 9 current OpenRouter models, price fixes (Sonnet 5 $2/$10) | every model and level called live; public endpoint read back in en/es/fr/ja; SS re-pulled |
| #1052 | N-400 v46: A-Number asked plainly | probe $0.36 |
| #1050 | automation tier (test harnesses) spends on the TEST Anthropic key | test key read in the running container |
| #1053 | N-400 v47: Part 9 one question at a time | probe $0.97 |
| #1055 | spend-cap outage fix: the cap's 400 alerts; OpenRouter fallback maps Claude 5 AND passes the router allowlist | code-level checks in prod; real-router test; NOT proved on the wire |
| #1059 | N-400 v48: oriented clause, opening is the question alone | probe $0.66 |
| #1060 | automation accounts latch OFF past 100 model calls in an hour | 4 tests through /v1/chat |
| #1061 | N-400 v49: form wording read as given (fee 400% FPG, interpreter, Reason for Filing) | structural only (no paid testing) |

Also live, outside a PR: the App Store `latest` is 1.18 build 1989 through the
admin overlay `/app/data/app-versions-overlay.yml` (read back: production and
header-less clients get 1.18/1989 with the App Store link, sandbox 1.17/1659).

## Held PRs, and what releases each

Deploys are HELD while Scott tests N-400 builds on his phone. A merge deploys
and restarts GP for about 30 s, which drops a live turn (it did at 20:02Z on
09-28). Ask fable-auditor-a2 (the N400 client session) before ANY deploy.

| PR | What | Released by |
|---|---|---|
| #1062 | N-400 v50: every Part 9 and printed yes/no or choice question read as given | auditor's go before build 114, AND Scott's answer on grouping (below) |
| #1063 | app-versions.yml to 1.18/1989 (matches the live overlay) | the next deploy; then CLEAR THE OVERLAY |
| #1056 | critical incidents push to Scott's iPhone through the SS app | Scott's direct go |
| #1057 | subscriptions row: payment failed on free, not Plus | Scott's direct go |
| #1058 | every model call goes through the OpenRouter fallback (8 direct callers) | Scott's direct go |
| #1054 | Shoulder Surf Companion telemetry (Mac + Windows), 180 days retention approved | the auditor's go (Scott: "ship it when the auditor says go") |

Plan was one batched deploy for #1056, #1057, #1058 (one restart). After
#1063 deploys: `DELETE /webhooks/admin/app-version/override/com.shouldersurf.ShoulderSurf`
with X-Admin-Key, then read /v1/app/version back from the YAML alone. After
#1056 deploys: add `automation_hourly_cap` to `config/remote/operator-alerts.json`
push_categories so the latch reaches his phone.

## Standing rules made this session

1. **No paid test calls through 2026-09-30** (Scott, after the outage). From
   Oct 1 the $5 per test rule applies (repo and global CLAUDE.md), plus CHECK
   THE ORG'S REMAINING HEADROOM first: the Testing workspace's $25 cap sits
   inside the org cap. Any paid verification is asked first (via the auditor
   or Scott).
2. **Ask the auditor or SS before any deploy** while Scott is device testing.
3. **The N-400 lane never makes a legal judgment**: no ruling on who counts as
   an interpreter, none on whether an act falls under a printed Part 9
   question. Asked, it says it can't give legal advice and has her answer the
   question as written.
4. **Test accounts latch at 100 calls/hour** and are cleared only on Scott's
   word: `UPDATE users SET is_active=1 WHERE id='<id>'`.

## The 2026-09-29 outage, in one paragraph

The org Anthropic spend cap ($250) was hit between 20:22:52Z and 20:37:52Z;
every call on both keys got 400 "You have reached your specified API usage
limits". GP's health check saw it and did not alert (it only alerted on
402/401/403); the OpenRouter fallback never fired because Sonnet 5 had no
mapping AND the router allowlist refused every Claude id on OpenRouter (the
fallback to Claude had never worked). Only Scott was affected: 6 failed
Catch Me Up queries 23:26 to 23:27Z (usage_log; no N-400 turns attempted,
per his device logs). Scott raised the limit about 23:29Z. $115 of
September's spend was 09-28 (probe runs plus the harness). Fixed by #1055
and #1060; #1056 and #1058 finish it.

## Keys and spend

- Production key ends **TwAA** (Default workspace; its limit is the org's).
- Test key ends **UgAA** (Testing workspace `wrkspc_011G45s18vpL2GsFQAv26Ezm`,
  $25/month), in Secret Manager `anthropic-test-api-key` (project `cloudzap`)
  and in local `.env` as BOTH `CZ_ANTHROPIC_API_KEY` and
  `CZ_ANTHROPIC_TEST_API_KEY`. The old YwAA line is commented out; delete YwAA
  in the console after checking its last-used date.
- OpenRouter key: about $10 limit remaining at 09-29.
- Test spend 09-29 (ledger, local times): $0.97 + $0.49 + $0.17. One sub-cent
  prod-key call at 23:34:43Z to confirm recovery, disclosed.

## Open, not done

- **#1055 live wire proof**: one sub-cent call where Anthropic refuses and
  OpenRouter answers. Needs Scott's OK from Oct 1. SS is waiting on it.
- **v50 grouping**: printed questions can no longer be merged, so the pace
  grouping narrows to plain boxes (Sex, parent citizen, disability, SSA card,
  ethnicity and race, spouse armed forces each get their own turn). The
  auditor is asking Scott; if he wants the old pairs, it is a small edit and a
  re-verify on #1062.
- **Scott's phone and the N-400 harness share ONE account** (408a4694,
  `harness:n400-reviewer`, active). GP cannot tell his testing from the
  harness; usage_log carries no case_id. The client should give the phone
  its own account or a marker.
- **SIWA revocation real proof**: a throwaway SS account on a second Apple ID
  deleted on a device, then read journald for "siwa_revoke: token revoked".
  Social has never claimed it. Scott owns the test.
- **On-device recipe for SS** (GP owns the recipe): SS asked for an Apple
  lane block in protected-prompts, query first, meeting chat second; SS runs a
  client constant (12,288 chars) until then. Field names to be sent to SS
  before building.
- **Companion telemetry read-back**: after #1054 deploys, post one test event
  of each shape plus one download, send SS the stored rows, delete them. SS
  senders are built but switched off until then.
- **file generation** stays on Sonnet 4.6 pending Scott's call; **Sonnet 5
  testing owed** on summary, chat and report (no paid testing this month).
- Local worktree `/Users/scottguida/cloudzap-dash` (branch
  fix/subs-row-current-tier, #1057) can be removed after #1057 merges.

## Gotchas hit this session

- zsh treats `"$sha:scripts/..."` as a history modifier; always `"${sha}:..."`.
  It bit twice (once cost v46 three minutes of v45 being served).
- Switching branches while a background test suite runs invalidates the run;
  use a worktree.
- `docker exec` without `-i` eats a heredoc; for scripts use
  `docker exec -i ghostpour python - < file`.
- A worktree has no `.venv`; run the main repo's `.venv/bin/python` from inside
  it (confirmed by sabotage that the worktree's code was the one tested).
- A popped env var falls through to a local `.env`; tests that need a setting
  absent must set it empty (fixed in #1054 for the test-key test).
- Streamed replies log 200 in the access log even when they end in an error;
  count failures from usage_log.
- The app-version override endpoint only takes safety fields; `latest` needs
  the overlay file plus a no-op reload, and merge_overlay replaces
  platform-level keys whole.
