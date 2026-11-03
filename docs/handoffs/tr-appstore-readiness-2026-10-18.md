# TR to GP: App Store readiness asks

2026-10-18, from the Tech Rehearsal session. ShoulderSurf went to review
tonight and Scott has moved the team onto getting Tech Rehearsal submitted.
These are the GP-side items between us and a submission. Each one names what
we already checked in your repo so you don't redo it.

TR v1 ships English only and without the Watch app (cut tonight, TR
`eff5f91`). Bundle `com.weirtech.techrehearsal`, product
`com.weirtech.techrehearsal.sub.monthly` ($9.99, P1M, one-week free trial).

## 1. Sign in with Apple revocation on a TR delete (rejection risk)

Guideline 5.1.1(v). Our client half is built: `POST /v1/account/delete` with
`X-App-ID: techrehearsal` and `apple_authorization_code` from a fresh Apple
re-auth. In August you said revocation wasn't firing end to end yet.
`app/config.py:385` says the SIWA key was unmapped until 2026-09-24, so we
read that as now live.

**Ask:** confirm a TR-scoped delete actually calls Apple's `/auth/revoke`
with TR's bundle id as the client id, not ShoulderSurf's. Also confirm the
existing rule still holds: revocation fires only when TR is the last app on
the account.

## 2. Extend the delete dry run to TR

`app/routers/account.py:98` limits `account_delete_dryrun_until` to
`shouldersurf`/`unknown`/`None`. A TR delete inside the window would purge
for real. We need the window for two things: our first ever run of the delete
flow on a device, and the App Review screen recording.

**Ask:** allow `techrehearsal` through the dry-run branch, and open a window
when Scott schedules the recording. We'll tell you before any real delete.

## 3. The TR subscription product has no mapping in GP

`SubscriptionManager.swift:293` posts every TR purchase to
`/v1/verify-receipt` with product `com.weirtech.techrehearsal.sub.monthly`.
`git grep 'techrehearsal.sub'` on main finds nothing, so we don't know what
verify-receipt does with it: drop it, alert on it as unmapped, or grant an
SS tier. Separately, TR's budget gate keys on the client-sent
`X-TR-Entitlement` (`free`/`paid`), so the $25 paid cap is currently
selected by the client's own claim, not by a verified purchase.

**Ask:** what happens today on a real TR purchase, end to end? And what
should v1 do: map the product to a TR entitlement server side, or keep the
header and accept the exposure? It's capped at $25/user/month either way, so
this is a call for Scott, not an emergency. We'll mirror whatever contract
you pick.

## 4. Receipt enforcement before TR launches

Your 09-23 note: `require_signed_transaction` is false on prod, so an
unverifiable receipt alerts and then grants anyway, with a build floor as
the intended fix. **Ask:** is that planned before TR's release, and does it
cover TR's product once item 3 is settled?

## 5. Prod config confirmation (checked in repo, want the live read)

In the repo `config/apps.yml` has TR's budget enabled (free $5, paid $25,
`own_account_meter: true`) and `tier_overrides.max_images_per_request: 1`.
There's no `techrehearsal/entitlements` file, so TR resolves the flat SS
matrix. **Ask:** confirm prod serves the same, and tell us if anything in
the flat entitlements matrix gates a TR call type. If nothing does, we're
fine without a per-app file for v1.

## Not asks, just closing the loop

- **Localization shadowing (your 09-23 note).** Not blocking. TR v1 is
  English only. Fix it before we restore es/ja; we'll tell you then.
- **`analysisSchema` (09-04).** Still item 3 of our 08-10 prompt handover,
  so please keep it.
- **#819 is still open on our side.** Does the overall-score rollup ship on
  LiveRoundScore only, or on both? We're fine with LiveRoundScore only, since
  the mock path measured stable on the user lane. When you name the
  `scoring_version` key, we ship the client change.
- **Routing.** We read #982 as closed by Scott's re-ruling: TR stays on
  Sonnet 5 at v41. Nothing for us to do.

The TR session is `techrehearsal-26` if you want to reply directly.
