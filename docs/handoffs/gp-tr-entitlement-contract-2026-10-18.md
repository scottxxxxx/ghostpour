# GP to TR: anonymous accounts and the server-side TR entitlement (PROPOSAL)

2026-10-18, GP (cloudzap) to the Tech Rehearsal session. Scott ruled both yes
tonight (relayed by TR). This is the client-facing contract GP proposes to
build. Nothing here is built yet. TR builds against this file, not against a
guess, and any change goes back into this file before either side codes it.

Where each claim was proved: the ShoulderSurf behaviours quoted below were
read in GP's code tonight (`app/routers/auth.py`, `app/models/user.py`,
`app/services/allowance.py`, `app/routers/chat.py`). Everything marked NEW
is a proposal.

## 1. Anonymous TR accounts (Scott: yes, on SS's rule)

**Mint.** `POST /auth/anonymous`, header `X-App-ID: techrehearsal`, body
`{"install_id": "<uuid>"}` and nothing else. Same endpoint and body SS and
N-400 use. Keep the install id in the Keychain, device only, and keep it
through any "delete my data" (N-400's `GPInstallIdentity.swift` is the
pattern). GP namespaces the account `techrehearsal:<install_id>`, so a TR
anonymous account is never an SS one (`anonymous_sub`, auth.py).

Returns the standard `AuthResponse`: `access_token`, `refresh_token`,
`token_type`, `expires_in`, `user`. `user.tier` is `"free"`.

**Token claim.** NEW: `techrehearsal` joins `STRICT_APP_CLAIM_APPS`, so a TR
token carries `app: "techrehearsal"` and is refused on another app's
`X-App-ID`, and an SS token is refused on TR. This matches N-400 and I-765.

**$0 until bound (SS's rule).** An anonymous TR account with no verified TR
plan gets no AI. Every AI call answers the lean 200 envelope GP already
sends when a budget stops a call: `text: ""` plus `feature_state`:

```json
{"feature": "chat", "app": "techrehearsal", "budget_exhausted": true,
 "code": "techrehearsal_plan_required", "cta": {"text": {"en": "..."}}}
```

`code` is NEW and is the one field to branch on. The copy is served, not
hard-coded, so Scott can change it without a build. A signed-in free TR
user keeps the $5 free cap. A paid TR entitlement (section 2) lifts an
anonymous account to the $25 paid cap without signing in.

**Sign in with Apple after anonymous.** `POST /auth/apple` with the usual
body plus `anonymous_token` (the anonymous access token). The response is
the same `AuthResponse` plus three fields that exist today (check the echo):

- `merge`: `"converted"` (a new Apple ID took over the anonymous account in
  place, same user id), `"merged"` (it moved into an existing Apple account,
  which wins, and the anonymous account closed), or `"none"`.
- `merge_reason`: why a token merged nothing (`anonymous_token_expired`,
  `anonymous_token_invalid`, `not_anonymous`, `anonymous_account_closed`).
- `plan_conflict`: true when both accounts already held a paid TR plan and
  each kept its own.

NEW for TR: the TR entitlement row moves with a merge exactly as SS's plan
does: only when the Apple account has none of its own. A closed anonymous
account answers `410 anonymous_account_closed` on `/auth/anonymous`, and
the client offers Sign in with Apple instead of minting again.

## 2. The server-side TR entitlement (Scott: yes)

**Purchase.** `POST /v1/verify-receipt`, header `X-App-ID: techrehearsal`,
with the signed transaction (JWS) required. GP never grants a TR plan on the
client's word. That's the same rule as SS's one-time unlocks, whatever
`require_signed_transaction` says for SS. Apple's signed `productId` must
be `com.weirtech.techrehearsal.sub.monthly`.

NEW response (an echo the client compares, rule 4):

```json
{"app": "techrehearsal", "entitlement": "paid", "status": "active",
 "is_trial": true, "expires_at": "2026-10-25T...Z",
 "original_transaction_id": "..."}
```

`status` is `active`, `expired`, `revoked` or `refunded`. A JWS that
doesn't verify answers 400 `signed_transaction_required`. A TR product
posted without `X-App-ID: techrehearsal` answers 400 `wrong_app`.

**Where it lives.** NEW table, one row per (user, app,
original_transaction_id). It never touches `users.tier`, the SS allocation
or `users.original_transaction_id` (the one SS subscription slot). A person
can be SS Pro and TR free, or SS free and TR paid.

**Apple's notifications.** Once TR's notification URLs point at GP
(they're unset, and TR holds them until GP says so): SUBSCRIBED and
DID_RENEW set the row active with Apple's `expiresDate`. EXPIRED,
GRACE_PERIOD_EXPIRED, REVOKE and REFUND set it inactive. The row is found
by `originalTransactionId`, then by `appAccountToken` (TR sets it to the GP
user id, `SubscriptionManager.swift:95-100`). PR #1131 already stops these
from ever moving an SS tier.

**Budget gate.** `X-TR-Entitlement` is IGNORED. GP keys the cap on the
server-side row: active means paid ($25 a month), otherwise free ($5 a
month), and anonymous with no active row means $0. TR has no users yet, so
there's no transition period and no header fallback.

**Recall and features.** NEW `techrehearsal/entitlements` matrix with the
columns `free` and `paid`. For TR calls GP resolves the user's tier from
the TR entitlement, not SS's `effective_tier`. Scott sets the values. Until
he does, GP proposes `paid` = SS Plus's column and `free` = SS Free's.

**Read-back.** NEW `GET /v1/entitlement` with `X-App-ID: techrehearsal`
returns the same object as the verify-receipt response, so the client can
show plan state from GP on launch and on restore.

## Answers to TR's four notes (2026-10-18, read in GP's code)

1. **The JWS field is `signed_transaction`.** It already exists on
   `VerifyReceiptRequest` (chat.py) and is verified by
   `receipt_verification.resolve_identity`. Send the CURRENT transaction's
   `jwsRepresentation`, the renewal you just verified. Apple's payload
   carries `originalTransactionId` inside it, so GP gets both ids from the
   one signature. Keep sending `transaction_id` (originalID) and
   `current_transaction_id` as today. GP uses Apple's signed values over
   them.
2. **A streamed call that's refused is NOT an SSE event.** The budget gates
   in `chat()` run long before the stream decision (the gates near
   chat.py:2370 and :2540 against the stream branch near :5222), so a
   refused `stream: true` call answers a plain `200 application/json` with
   `text: ""` and `feature_state`, and no stream ever opens. Branch on the
   Content-Type: `application/json` means read `feature_state`;
   `text/event-stream` means the call is running. The same holds for
   `techrehearsal_plan_required`.
3. **Context Quilt for anonymous accounts: today GP does NOT gate on
   anonymous.** Nothing in `context_quilt.py` or the chat hook loop reads
   `is_anonymous`, so an anonymous account whose entitlement enables the
   CQ hook is captured and recalled. That's the mechanism behind what SS
   Dev found. PROPOSED for TR, and Scott's call: no CQ capture or recall
   for an anonymous account, paid or not, until it signs in. GP would
   enforce it server-side in the hook loop, so no client gate is needed.
   Whether the same rule should cover SS's anonymous accounts is also
   Scott's.
4. **Delete works for an anonymous account.** `POST /v1/account/delete`
   with the anonymous bearer and `{}` purges and answers
   `{"status": "deleted"}`. With no Apple code, revocation is skipped by
   design (`test_an_anonymous_account_can_delete_itself`, which tests
   SS's anonymous path). For TR the same route is scoped by
   `X-App-ID: techrehearsal`, and GP will add a TR-anonymous test with
   the build.

## What TR's client changes

1. Anonymous mint on first launch, with no Sign in with Apple wall before
   purchase (SS's 954238a0 shape).
2. Send the JWS on verify-receipt, and stop sending `X-TR-Entitlement`
   (GP ignores it).
3. Branch on `feature_state.code == "techrehearsal_plan_required"` to show
   the paywall.
4. Pass `anonymous_token` on `/auth/apple`, and read `merge`.
5. Read plan state from `GET /v1/entitlement`.

## Open, for Scott

- The `cta` copy for `techrehearsal_plan_required`.
- The `free` and `paid` columns of `techrehearsal/entitlements`.
- Whether a trial counts as paid for recall (proposed: yes, as SS's does).
- No Context Quilt for anonymous accounts until sign-in (proposed for TR;
  and whether it also applies to SS's anonymous accounts).
