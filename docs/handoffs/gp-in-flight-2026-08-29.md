# GhostPour: what is in flight (written 2026-08-29 ~03:30Z, session cloudzap-d8 [52aa80])

Prod and main are both at **9bf162d**, healthy, verified on `/health`. Working
tree clean (untracked: `Subscription Info/`, older handoffs, `tmp/`). Open PRs
are **#819** (TR, not mine) and **#736** (parked eval harness). Nothing of mine
is half-applied.

> Supersedes `gp-in-flight-2026-08-28.md`. That file's "Pro re-subscribe
> verified clean" line is RETRACTED; see below.

---

## Shipped and live tonight

**PR #818 — verify-receipt takes the purchase's identity from Apple, not the
client.** Merged `9bf162d`, deployed, SHA confirmed on `/health`.

The 08-28 handoff called `original_transaction_id = '0'` the defect. It was the
symptom. The cause: `/v1/verify-receipt` read `product_id` off the request body
and granted the mapped tier with **no server-side verification at all** (its own
docstring admitted it). Scott ran SS from Xcode against the local
`ShoulderSurf.storekit` config and GP granted Pro and booked **$14.99 of MRR for
a purchase Apple has no record of**. Proved from Apple's Notification History
with a positive control in the same query, not inferred.

What #818 does:
- Identity (`originalTransactionId`, `transactionId`, `environment`, `productId`)
  read from the Apple-signed JWS when present.
- **Two security holes closed, same shape.** `_verify_x5c_chain` takes a
  `bundle_id` it never reads, so nothing proved a JWS was signed FOR US. On
  verify-receipt a client could have presented any App Store app's transaction.
  On `/v1/apple-notifications` (unauthenticated by design, Apple posts to it) a
  third-party dev could set `appAccountToken` to one of our user ids and relay a
  genuine EXPIRED to strip that user's entitlement. **That second one was
  reachable by someone who is not Scott.**
- The tier comes from the product **Apple signed**, not `body.product_id`. This
  was a hole in my own first commit, caught only when I sat down to write the test.
- `COALESCE` everywhere so junk cannot overwrite a good id; cross-account dedup
  guarded (handed `'0'` it was nulling the id off every OTHER row holding `'0'`,
  which is how ddc3df33 ended up NULL).
- Four fields SS has sent for months (`current_transaction_id`, `payment_mode`,
  `purchase_date`, `expiration_date`) were modelled nowhere and dropped by
  pydantic. Rule 3.
- New alert categories `receipt_unverified` and `assn_foreign_bundle`.
- 31 tests, each guard sabotaged individually with the cache cleared, every one
  producing exactly the predicted failures.

**Verified in production, not just in tests:** Apple sent a live `TEST`
notification through the new bundleId guard and it returned 200. Baseline before
shipping: 47 real notifications across 6 types in both environments, all
carrying a bundleId of ours, zero refused.

---

## Scott's row: REPAIRED

Was `tier=pro`, `original_transaction_id='0'`, plus a fictional $14.99 event that
MRR was counting.

Now `tier=free`, `simulated_tier='pro'`, `original_transaction_id=NULL`, with an
appended `reconciled / void_unverified_purchase` event (the log is append-only;
nothing was rewritten). **MRR gross 39.97 -> 24.98, net 33.97 -> 21.23,
active_paid 3 -> 2.** He keeps Pro because `effective_tier` returns
`simulated_tier or tier`.

**⚠ THIS REPAIR IS NOT DURABLE.** `old_tier_name` reads the REAL tier, so his
next verify-receipt from the Xcode build sees free -> pro, treats it as a state
change, sets `tier='pro'`, **wipes `simulated_tier`, and writes a fresh $14.99
event.** Re-apply after any Xcode launch. Script saved next to this file: **`docs/handoffs/repair_scott_row.py`**
(`--dry-run` / `--apply --entitlement=simulated|admin|none`). Copy it into the
container and run with `PYTHONPATH=/app`. Apple-history probe is beside it as
`apple_notification_history_probe.py`.

---

## The wire check: HALF DONE

SS shipped their half (`71f0c7d`, in build **1306**). Verified from the artifact
rather than their word: the device `.app` Info.plist reads `CFBundleVersion 1306`
and the compiled dylib contains `signed_transaction`.

- **Experiment 1 PASSED, 02:45Z.** Launched from Xcode, GP logged
  `signature_rejected: x5c chain too short: 1 certs (need >= 3)`. That is the
  guard catching the exact defect on the wire, not in a test with a stubbed
  decoder. Xcode's local StoreKit config signs with one self-signed cert; Apple's
  chain is three.
- **Experiment 2 PENDING and it needs Scott.** Launch 1306 from the **home
  screen** (not Xcode) and make a **real sandbox purchase**. That is the only way
  either team sees a VERIFIED identity with `environment` read off Apple's
  signature. Nobody has ever watched that branch execute.

The StoreKit config is injected by Xcode **at launch** via the scheme's
LaunchAction, not baked into the build, so both experiments run on the same
binary and the launch method chooses which.

**DIAL `verify-receipt.require_signed_transaction` STAYS FALSE.** It is Scott's
to flip and only after experiment 2. Flipping it now would refuse receipts from
every real user on 1.14, since no shipped build sends the JWS.

---

## Steven Williams (Scott's friend): campaign LIVE

He is on the **App Store** build (`distribution: production`, 1.14/803), free
tier, never purchased. The `distribution` field on telemetry/onboarding is how
you tell App Store from TestFlight from Xcode; the ASC tester list cannot answer
it (40 of 43 testers are public-link "Anonymous" with no email).

**What went wrong:** I gave Scott code #1 of the production batch. That is the
exact code sitting in John Kirker's campaign CTA, redeemed in July. Steve was
typing a spent code. Apple's CSV is two columns, code and URL, with **no
redemption status**, so nothing warned me.

**Fix shipped:** campaign `ss_friend_steven_williams`, ACTIVE, cloned from
`ss_friend_john_kirker`. Targets his relay email, `tiers:["free"]`, launch
placement, `min_app_version 1.0`. CTA carries `YXJRXR4R638YKF83RM` (#250, never
handed out). Verified it will serve: email and tier match, and the only other
active campaign (`ss_gate_wiring_context_quilt`, priority 50) targets Scott on a
different placement. **He must relaunch the app to see the card.**

Copy is variant A, Scott's pick. `first_name_fallback` is **"Buddy"** (Scott's
standing rule: no name, call them buddy). Substitution happens ON DEVICE; the
name never reaches GP.

**Template for the next friend:** clone the campaign, change `id`, `name`,
targeting email, `cta_id`, and pick an UNUSED code.

---

## ▶ NEXT UP: redeem-failure messages (design agreed, waiting on two things)

Scott's ask: a user whose code does not work must see something. Today there is
**no failure path at all** — `PromoCTARouter.redeemOfferCode` is a bare
`UIApplication.shared.open` with no callback, and the sheet path's do/catch only
fires if the SHEET cannot present. Success arrives via `Transaction.updates`;
failure is silent.

**SS has BUILT the client half** (`5d53383` + `3e9a6f3`, 9 tests, gate green). It
presents **nothing** until GP serves copy: no English fallback on a subscription
surface. Armed on route, persisted to disk (the App Store hop is when iOS
reclaims the process), clock is foreground-milliseconds, fires once.

**Three outcomes, then SS split the middle one again:**

| payload | when |
|---|---|
| `promo_redeem_unconfirmed` | no transaction arrived. Honest "we cannot tell you why" |
| `promo_redeem_settling` | transaction arrived, `verify_failure` = `failed` or ABSENT. Nothing for them to do |
| `promo_redeem_settling_signed_out` | transaction arrived, `verify_failure` = `deferred_no_token`. **Sign in and it applies** |

Absent `verify_failure` must pick plain `settling`: "we never learned a reason"
is not "we know you are signed out".

**Wire shape agreed:**
```
redeem: {
  promo_redeem_unconfirmed:         { title, body, dismiss_label, secondary_label?, secondary_action? },
  promo_redeem_settling:            { title, body, dismiss_label },
  promo_redeem_settling_signed_out: { title, body, dismiss_label },
  redeem_timeout_ms:                8000
}
```
`redeem_timeout_ms` = milliseconds of **accumulated foreground time after
return**, NOT wall clock from the tap. `secondary_action` allowlist is GP's:
`retry_redeem` or absent. No `contact_support` (no destination) and no `sign_in`
in v1 (needs client work; the text carries it).

**BLOCKED ON:** (1) Scott signing off the copy, (2) me shipping it as a PR with
es/fr/ja, because the locale-parity test rejects English-only.

**GAP FOUND AND CLOSED (SS `67f6a87`, verified in their source at 03:4xZ).**
`arm()` had exactly ONE call site, `PromoCTARouter.swift:98`, inside the
`storekit_offer` case. The Settings **"Redeem Code"** row
(`SettingsView.swift:593`) called `presentOfferCodeRedeemSheet()` directly and
never armed, so the one path explicitly available signed out had no watch. It
also broke the agreed staging recipe: signed out there is no card to tap, so the
only route is Settings, which did not arm, so the test could only fail.

SS took the recommendation to arm inside the functions rather than at the call
site, and found a THIRD cold path doing it: their own retry button in the alert
also calls `presentOfferCodeRedeemSheet`. `arm()` now lives at
`PromoCTARouter.swift:157` (inside `redeemOfferCode`) and `:185` (inside
`presentOfferCodeRedeemSheet`), so all four entry points arm. Confirmed by
reading their source, not by report.

**Lesson worth keeping:** a rule that depends on every future caller remembering
is not a rule. Two entry points existed, one was already cold, and a third was
about to be added cold.

**Staging recipe for `settling` (once armed):** sign out, redeem, stay signed
out. No JWT so the receipt queues (`deferred_no_token`), the transaction still
arrives, the tier cannot flip. Agreed NOT to build a per-user verify-receipt
fault switch: the client branch is identical for both flavours and one telemetry
string is not worth a fault path on the endpoint that grants paid tiers.

---

## ▶ MY NEXT TASK: populate `offer_code_pool`

The table exists with `code`, `offer_id`, `product_id`, `environment`,
`batch_id`, `status`, `reserved_by_user`, `reserved_by_device`, `reserved_at` and
is **completely empty** — nothing has ever written to it. That is the root cause
of tonight's failure: nothing stopped me handing out a code already committed to
another campaign.

Reserve a code per campaign at mint time so "have we handed this one out" becomes
answerable. Keep it strictly separate from "Apple accepted it", which neither
team can ever see.

---

## Waiting on Scott

1. **Sign off the three redeem messages** (drafted, in the session transcript).
2. **Experiment 2**: home-screen launch of 1306 + a real sandbox purchase.
3. **Then** flip `verify-receipt.require_signed_transaction`. Not before.
4. Older, unchanged: test-account revocation (delete ddc3df33 refresh_tokens),
   `enforce_admins` on branch protection, the 300-char research-notes cap, the
   1.16 upload.

---

## Lessons filed tonight

- **[[feedback-verified-source-must-reach-every-decision]]** — I added a trusted
  `product_id` and left the tier reading the client's. A half-wired verification
  proves the wrong claim and looks closed.
- **[[feedback-grep-skips-gitignored-files]]** — recursive `grep` here is ugrep
  with `--ignore-files -I`; it silently skips gitignored AND binary files and
  returns `rc=1`, indistinguishable from real absence. Use `/usr/bin/grep`,
  SCOPED (unfiltered over `~/ShoulderSurf` times out at 5 min; `build/` is 4.1 GB
  of 13 GB). Cost me a false negative that decided a diagnosis.
- **[[reference-apple-notification-history-probe]]** — how to ask Apple whether a
  purchase actually happened. Needs `PYTHONPATH=/app`. **Always include a
  positive-control window**: a quiet history and a broken query look identical.
- **Two lessons that outlive grep**, from five false conclusions between two
  teams: an empty output is not an empty result until you have seen the command
  run (a pipe gives you `head`'s status, not the command's); and a repro attempt
  has to state its conditions, because "cannot reproduce" from the wrong
  directory is a confident wrong answer that looks like diligence.

## Standing gotchas

- **This worktree is shared.** Another session switched it to `main` mid-flight
  tonight. Check `git branch --show-current` before believing a diff.
- Deploy is automatic within ~3 min of merge; check `/health` `git_sha`.
- CI is the authority, not a local run: the local overlay shadows config changes
  and CI has none. The full suite is 2924 tests, ~7 min on CI and 45+ locally.
  Do NOT `--ignore=tests/integration`; that is what let two failures through.
- Apple's production offer-code batches have a **500 minimum**; sandbox is 10.
  `productionCodeCount` / `sandboxCodeCount` on the offer tell you which batch is
  which. Batch `549815` = PRODUCTION, `550023` = SANDBOX.
- Both friend offers cover exactly **one territory, USA**, while the Pro
  subscription sells in 175. A non-US friend cannot redeem either one.
