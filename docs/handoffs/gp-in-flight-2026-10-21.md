# GP in flight, 2026-10-21 (session close before /clear)

Read this first. It supersedes the 10-19 close for anything it covers.

## Live on prod (read back)

- #1131 TR readiness, ed00af5. TR SIWA key DJTH4VLF6U in Secret Manager
  `tr-siwa-private-key-b64`, `CZ_TR_SIWA_KEY_ID` in `/opt/ghostpour/.env.prod`
  (backup `.env.prod.bak-20261020`). `is_configured` tr True, ss True; JWT kid/sub read back.
- #1132 N-400 per-IP $2/30d block, 813099d. Hydrate added per_ip 2/30 itself, no sync.
- #1133 i765 interviewer-turn v4 + filing v5, 81c2efd. #1134 i765 filing v6 / interviewer-turn v5
  (no fees, payees, addresses, online_filing, timing), 3dd51f1. Hydrate served "v6" with the old keys
  for 67 s; fixed with a whole-doc PUT + `/version` sync (memory
  `feedback_hydrate_serves_bundle_version_with_stale_keys`).
- #1135 rejected-signature purchase is `Unverified`, not revenue, no paid alert. 15b207b, health
  git_sha read back, container carries UNVERIFIED. Cause of Scott's "paid" alert: an Xcode local
  StoreKit test purchase (1-cert chain, signature rejected) that inherited "Production" from his
  account's 10-04 reconciliation event.

## Waiting on Scott

1. **Merge #1137** (`! gh pr merge 1137 --squash`) once CI is green on 0f5627c.
   - What it does: `thinking` on model-routing rows; v42 sets `"disabled"` on SS `query` +
     `query_follow_up` and TR `tr_query` (Haiku 4.5 today, so a no-op there). Scott: "do what the SS
     team asks related to model changes" and "1 yes" for tr_query.
   - 0f5627c also fixes a time-bomb test that turned MAIN red on 10-21:
     `tests/test_resumed_meeting_stats.py` had `DAY = "2026-10-13"` against a 7-day window. Now
     yesterday. Sabotage: fixed date back in gave exactly 1 failure, the predicted one. Main stays
     red for every PR until #1137 lands.
   - After deploy: confirm hydrate ADDED `/apps/shouldersurf/call_types/query/thinking`,
     `.../query_follow_up/thinking`, `/apps/techrehearsal/call_types/tr_query/thinking`; if not,
     pointer-sync each, watch the version; read back served. Then send SS Dev AFTER stats against
     BEFORE (SS live asks, all users, 14d: n=150, ttft p50 0.91 s, p90 4.6 s, output p50 242, p90 777).
2. **#1136 TR handoff doc** (docs only). Two rulings:
   - Is `tr_interview_reference` paid only (counted inside the prep's action_id,
     `techrehearsal_plan_required` for free/anon)? About $0.12 to $0.20 per reference.
   - May TR's Copilot surface of `tr_query` move to Sonnet 5.5 low reasoning (first word ~1 s vs
     6 to 10 s)? Scott's SS model authorization does not cover TR; asked, unanswered.
   - All GP reshapes already accepted by TR. Sizing ~2 d + 0.5 d + 0.5 to 1 d. Merge the doc AFTER
     #1137 (any main push puts #1137 behind, ~18 min CI).
3. Revert Scott's account from Pro to free (the Xcode purchase moved it)? Asked, unanswered.
4. TR plan gating build: waiting on TR's "go" with the final table. Build floor on `X-TR-Build`
   must be dropped before TR 1.0.
5. N-400 sandbox test notification via App Store Server API (outward call): asked earlier.

## SS chat thinking test (done, $0)

Scott asked for subagents instead of paid calls. 17 real SS chat asks, A = gather evidence then
answer, B = direct; blind, two judge panels (Sonnet, Opus). On the 15 clean items both panels:
A 8, B 1 (item 18, accuracy), tie 3, split 2, one A/tie. Only item 02 was a clear A win; verified
errors about even (10 vs 12, 15 vs 18). Read: more completeness, not more correctness. Proxy only,
not the production thinking switch. Raw: `qa/runs/ss-chat-thinking-subagents-2026-10-20.json`.
SS Dev agrees and is taking it to Scott; **hold any Meeting Chat / Project Chat prompt change until
Scott decides**. GP owns SS AI prompts and models; SS never edits them (Scott, 10-20).
Gotcha: asking subagents to write out their reasoning trips a `reasoning_extraction` safeguard;
"list the evidence first" works.

## Owed

- Throwaway TR account delete showing `siwa_revoke: token revoked client_id=com.weirtech.techrehearsal`.
- Read the log after SS Social presses "Request a Test Notification" for TR.
- `ip_hash` read-back on the first real N-400 turn.
- Clean up worktrees after merges: `../cloudzap-unverified` (merged), `../cloudzap-qthink`,
  `../cloudzap-docs`, `../cloudzap-tr`, plus the prunable scratchpad ones.

## Gotchas this window

- Boot hydrate never removes keys but does move the version; a removal bundle needs a whole-doc PUT
  then a `/version` sync right after deploy.
- An explicit sync-from-bundle per key adopts the bundle version on the FIRST write; extra writes
  overshoot it.
- Don't push docs to main while a PR waits; this file is committed locally only until #1137 merges.

## Update, 07:30Z same day

- #1137 merged 550bf32 and deployed; health git_sha read back. Overlay model-routing is v43 (bundle 42)
  and carries all three `thinking: "disabled"` keys through hydrate, so no sync was needed. AFTER stats
  are still owed to SS Dev once about a day of live asks has accrued.
- #1136 merged 8d74fb6.
- Scott's rulings: `tr_interview_reference` follows TR's proposal (free and anon get
  `techrehearsal_plan_required`, and paid counts it inside the prep's action_id with no separate meter).
  TR's Copilot surface of `tr_query` moves to Sonnet 5.5 low reasoning. Scott stays on Pro for now.
- TR client e916be4 sends `metadata.surface` (`copilot` on tr_query and tr_follow_up, plus `job_chat` and
  `session_chat`), with X-TR-Build unchanged. Route on the field being present, and fall back to today's
  routing when it's absent. NOT BUILT yet: the reference call type and the Copilot routing are next.
- N-400 sandbox TEST notification proved: Apple sendAttempts SUCCESS at 06:54:04Z, and GP logged
  `transaction not decoded (type=TEST)` plus a 200 at 06:54:08Z. This proves nothing about the
  production ASSN URL.
