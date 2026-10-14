# GP in flight, session close 2026-10-14

Prod serves `520d42c` (#1105). Main is clean, and no GP PRs are open from this session.

## Standing orders from Scott (still in force)

- "merge N" means GP runs it: update-branch if BEHIND, wait for CI, merge one at a time, watch the deploy, sync configs, read back. Auto-merge is off on the repo.
- Internal teams get Pro when they ask, with no word from Scott needed. Set the tier, read it back, and revert when they're done.
- N-400 team asks are pre-approved, including live creative uploads and deploys of their work, unless they cost tokens. Paid probes still need Scott.
- Verbose logging stays ON while Scott tests. He decides when it goes off. Never tell anyone it is already off.
- Test accounts stay out of dashboard stats. Scott's account (fa4d903c) and Lauren's (f74ba226) stay COUNTED.
- Paid model tests: $2 cap per test, ask above that (memory rule, tighter than CLAUDE.md's $5).
- Prod writes on non-N-400 work need Scott's go typed in this session. A relay from another session is not his go.

## Shipped this session (all live, synced, read back)

| PR | Sha | What |
|---|---|---|
| #1091 to #1097 | 57a5b93 | Accented names in headers (latin-1 decode, percent-encoded CQ entity header), report localization, dash rules in protected prompts, PCC model entry |
| #1098 | 82f4c85 | N-400 share pages and per-language share links |
| #1099 | 00edeb9 | N-400 v57: age comes from the app's `p1.age_over_18`, never date math on the masked DOB. interview-turn v4 |
| #1100 | 0d1bd8a | `users.exclude_from_stats`, plus `POST /webhooks/admin/stats-exclusion` |
| #1101 | 85e43e4 | Free tier sees the last 10 days (tiers v63) |
| #1102 | 2547cd2 | A resumed meeting counts once in meeting stats |
| #1103 | 1b232ca | IAP one-time unlocks (Apple Cloud pack, own key pack, both, two upgrades) and the free Project Chat rules |
| #1104 | d8923b6 | N-400 v58: the app owns the window math, five coverage rules retired |
| #1106 | f89a2b1 | New-user push skips test builds (below) |
| #1105 | 520d42c | Free Apple Cloud takes 1 photo, the pack 5. Sentence-case pack names, Anthropic note rewrite |

#1105 syncs: llm-providers x4 v26 to v27 (keys `providers`, `/version`); tiers en v64 to v65 and es/fr/ja v63 to v64 (key `unlocks`, no `/version`). Read back by string: PCC `maxImagesPerRequest` 1, the Anthropic note in all four languages, the pack names, and the "Up to 5 photos per question on Apple Cloud" bullet on both Apple Cloud packs.

## #1106, the new-user push rule

Scott: "I only want to get those alerts when we have a new human user."

On a NEW account from `/auth/apple` or `/auth/anonymous`, GP reads `X-App-Distribution`, the same header `/v1/app/version` reads. SS resolves it from Apple-signed AppTransaction.environment.

- `production` or absent: push and count. Absent has to mean a real user, because App Store 1.18 and 1.19 never send the header on auth.
- `sandbox` (TestFlight), `xcode` (debug builds and every simulator), or any other present value: no push, and the account is born with `exclude_from_stats = 1`.

Sabotage, committed first, cache cleared, count predicted out loud: `is_test_build` returning False turned exactly the predicted 4 tests red; the anonymous path ignoring the flag turned exactly the predicted 3 red.

SS sends the header on every auth post from their NEXT app version, not 1.19, so test accounts from current builds still push until then. **Owed:** when SS sends that build number, read back a test sign-up's row (exclude_from_stats=1, no push).

## Waiting on Scott

1. **Flag 6a07e3a9-ea72-4005-afdd-33a1f80e0a6e out of stats.** This is SS's simulator anonymous account from the unlock check: free tier, exclude_from_stats=0. GP's remote write was blocked by the auto-mode classifier, so it needs Scott's permission. 29c3fc76 was already excluded (read back).
2. **"run the unlock matrix"** (SS's ask, server half on prod):
   - `/v1/usage/me` shows `unlock_apple_cloud` and `unlock_byok` as disabled.
   - `POST /v1/features/project-chat/check` for each provider (apple_cloud, byok, on_device, ssai), each with a fresh session_id.
   - `/v1/chat` Project Chat returns 402 `unlock_required`, then 402 `quota_exhausted` after the taste is used.
   - The old-build shape (no `selected_provider`) keeps today's behaviour.
   - `/v1/tiers` carries `unlocks`.

   It needs sessions minted for test accounts and an anonymous account created server-side (curl gets `anonymous_not_offered`). Spend is one ssai taste call, about a cent. Mark every result as proved on GP: on own key the client calls the provider directly, so GP gates only the preflight and `/v1/chat`.
3. **Haiku 5.5 (1M context)** is in Anthropic's Models API but not in our served model list. It touches pricing and the picker, so it's his call.
4. **cta_strings copy** for `unlock_required` and `quota_exhausted`. The text is empty today.
5. **A signed-out user who picks Apple Cloud gets `login_required`** and never sees the pack offer. SS is raising it with Scott as a product question. Change nothing until he rules.

## Owed to partners

- **SS, sandbox purchase:** a Sandbox JWS should grant on prod. This was read in code, not proved on the wire: `receipt_verification.py:185-190` keeps environment "Sandbox", and the unlock branch in `chat.py:550-565` has no environment filter. When SS sends the purchase time, read back the `purchases` row (environment, status, grants) and `/v1/usage/me`.
- **SS client proof already in:** on the sim with anonymous account 6a07e3a9 and apple_cloud, the preflight returned `unlock_required` with unlock `unlock_apple_cloud`, and the sheet showed. No purchase was made.
- **N-400:** the v58 real-run proof is owed, and the partial-start derive lands in client build 175.
- **N-400 I-765 card:** swap the real I-765 App Store link into both cards once it's approved.

## Gotchas from this session

- zsh needs `${=T}` to word-split a variable of test paths.
- The local overlay `data/remote-config` shadows the bundle in tests. Move it aside to match CI, and put it back after.
- Run the FULL suite before pushing a new table: #1103 failed CI on `test_every_user_keyed_table_is_classified`.
- `$(curl ...)` strips the trailing newline, so pipe curl straight to `shasum` for sha checks.
- The admin key inside the container is `CZ_ADMIN_KEY` in the env. Never print it.
