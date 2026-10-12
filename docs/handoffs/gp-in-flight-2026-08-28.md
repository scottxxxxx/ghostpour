# GhostPour handoff — 2026-08-28 (session "Resume work" [0e5333])

**origin/main and prod are both at `da66249`**, healthy. Open PRs: **#818**
(cloudzap-d8's, below) and **#736** (parked eval harness). Nothing of mine is
half-applied.

> ⚠ **The shared working tree is NOT on main.** It is checked out on
> `fix/verify-receipt-server-side-verification`, **7 commits ahead of
> origin/main**, because cloudzap-d8 is working in it live (HEAD was `de7e58f`
> at the time of writing and is moving). Clean, but do not mistake that for
> main having advanced, and do not commit onto it. If you need main:
> `git worktree add <path> origin/main` rather than switching this checkout
> underneath them.

**Rewritten end-to-end at session close.** Earlier versions carried claims I
later retracted; this is the corrected state, not a patched one.

---

## 1. OWNERSHIP FIRST — do not pick these up

**cloudzap-d8 owns the subscription/receipt work.** PR #818: five commits,
30 tests, five sabotages, CI running; they will not merge without green.
Two distinct defects, both found by pulling the thread on a transaction id
I had wrongly cleared.

### 1a. SECURITY, externally reachable — `/v1/apple-notifications` (fixed `d8c8b6f`)

The webhook verified that **Apple signed** a notification but never that
Apple signed it **for us**: `decode_notification` passes a `bundle_id` to
`_verify_x5c_chain`, and that function never reads it. The endpoint is
unauthenticated by design, because Apple posts to it.

**Reachable harm:** a third-party developer puts one of our user ids in
their own app's `appAccountToken`, then relays the genuine Apple-signed
EXPIRED they receive to our endpoint. `_match_user` finds our user and
applies it. The notification is entirely real, just not about our app.
**A paying user's entitlement is stripped by an outsider.**

The guard checks `data.bundleId` against `apple_bundle_id`; d8 confirmed
against live Production payloads (SUBSCRIBED, DID_CHANGE_RENEWAL_STATUS,
EXPIRED) that it refuses nothing legitimate. `if False` sabotage turned
exactly one test red.

**Naming trap in that diff — three near-identical strings:**
- real bundle id: `com.shouldersurf.ShoulderSurf`
- `apple_bundle_id`: comma-joined `com.shouldersurf.ShoulderSurf,com.weirtech.techrehearsal`
- `com.weirtech.shouldersurf.*`: **PRODUCT** ids, a different namespace

### 1b. REVENUE INTEGRITY — verify-receipt trusted the client

`/v1/verify-receipt` read `product_id` off the request body and granted the
matching tier with **no server-side verification**. Its own docstring said so
("For MVP: trusts the product_id from the authenticated client") — known and
parked, not hidden.

Scott ran the app **from Xcode against the local `ShoulderSurf.storekit`
configuration**. GP granted Pro and **booked $14.99 of MRR for a purchase
Apple never saw** — d8 confirmed via Apple's Notification History: no purchase
for him on 08-28, only the EXPIRED for his old comped trial (offer
`ops-test-scott`, expired VOLUNTARY 02:23 UTC; GP handled that correctly).

**`environment` has never once been measured.** `_resolve_storekit_environment`
falls back to inferring from an earlier event, and the comment above that
fallback claims the client sends `signed_transaction` — it never has, anywhere
in the SS codebase, so that branch has not executed a single time. Every
`environment` on every verify_receipt row we have ever written is **inferred**.
Scott's read "Production" because his trial's EXPIRED hours earlier was. And
`user_environments` exists specifically to keep TestFlight out of the revenue
number, so the field doing that job has been guessing.

**The `original_transaction_id = '0'` symptom:** SS sends
`String(transaction.originalID)` and StoreKit reported 0 under the local config.
The cross-account dedup then **nulls the id off every other row holding the
incoming value**, so handed `'0'` it widened: ddc3df33 shows `'0'` in its 08-23
event but NULL on its users row today, because Scott's 08-28 verify cleared it.
**Two rows affected** — one holding a bad value, one that lost its id entirely.
(The 08-23 row is the Sandbox test account, not a second customer purchase.)

**The fix:** identity from the Apple-signed JWS when present, plus a bundleId
check that did not exist (`_verify_x5c_chain` took a `bundle_id` it never read,
so a client-supplied JWS from *any* App Store app would have verified).
Implausible ids can no longer overwrite good ones, dedup is guarded, and
enforcement is a served dial `verify-receipt.require_signed_transaction`
**defaulting FALSE** so live subscribers on old builds keep their tier. Adds a
400 with `detail.code = "receipt_unverified"` on that path only, when the dial
is on; does not touch the 429 envelope SS is rewriting.

**d8's own correction, worth its own line:** their first commit captured
`identity.product_id` off the signed payload but left the tier lookup reading
`body.product_id`, so a genuine Plus receipt sent with a Pro product_id would
still have bought Pro. Fixed in `2db099d`. They caught it only by sitting down
to write the test and finding nothing to assert against — the test was the
instrument, not the review.

### 1c. My retraction, recorded because the mechanism is the lesson

I told Scott his re-subscribe was "verified clean" and that MRR counted it
correctly. **Wrong, retracted.** I had checked `users` and `subscription_events`
— tier, is_trial, simulated_tier, source, price, environment. **Every one of
those is downstream of what the client asserted.** The failure was not that the
fields were wrong; it was that **Apple was never asked**. `verify canonical, not
derivative` is already in memory and I did not apply it.

---

## 2. Shipped and live today

| PR | What | Live at |
|----|------|---------|
| #814 | Recall timeout 500 -> **1500 ms** as a served dial (`cq-recall.timeout_ms`, server-only, bounds 50..10000) + durable `recall_observations` table | daef1de |
| #815 | Share card: subject back to caption-only (undid the 08-26 flip) | b62cb3d |
| #816 | Card translates its quoted content at render time; a day label must be a real date or weekday | 0337b0f |
| #817 | Card is the **ledger sandwich**: black bars top and bottom, light panel, stat row with bolt + sentiment wave | da66249 |

`CARD_VERSION = 8`, verified live on Scott's real Spanish share: chrome, action
text and sentiment all Spanish, no subject in the image, no "Para el 7 to 14 days".

---

## 3. Recall investigation — answered, CQ's hypothesis disproved

Timeout was 500 ms, uniform across routes and tiers. Every degrade in the 3 days
since #790 (when degrades started being recorded at all) was a **timeout on a
full-scope recall**: 6 events, 0 5xx. On 08-27 full scope ran 5 ok / 4 degraded
(**44%**) against 16 ok / 0 for People-scoped. The user sees an answer with
**no memory block and no error** — invisible.

Raised to 1500 on Scott's word. **After 17 real recalls (08-28): 0 degraded, and
2 (12%) ran past 500 ms** (746, 1094) so they would have failed on the old
budget. Max 1094 — 1500 is headroom, not a band-aid.

**CQ's entity-count scaling is NOT supported** and they have been told not to
optimise it: 43 entities -> 294 ms and 45 -> 299 ms, while 37 -> 746 ms and
28 -> 139 ms. Not monotonic. Also ruled out: cold-start (the longest gap of the
day gave the 2nd fastest row) and ingest proximity. An unexplained 400-800 ms
tail remains, where CQ's own unattributed ~243 ms was pointing. Rows handed to
CQ: 15:01:23Z (746/37ent), 15:25:53Z (1094/54) vs fast neighbour 15:21:23Z (299/45).

Next check, one query; every row carries the budget it ran under:
```sql
SELECT scope, timeout_ms, outcome, COUNT(*), ROUND(AVG(duration_ms))
FROM recall_observations GROUP BY scope, timeout_ms, outcome;
```

---

## 4. Partner threads — both resolved

**The demo-night "You're out of AI credits" was never an allocation problem.**
nginx refused the request at the edge (general zone, 10 r/s, burst 20) during a
translation burst, and the client maps any 429 to `allocation_exhausted`.

- **Bifrost: DONE.** Dedicated zone live on both `cz.shouldersurf.com` and
  `api.ghostpour.com` (`translations rate=30r/s burst=120 nodelay`, their
  135a359), verified under load. They measured the real peak at **25 req/s in a
  single second**, larger than my 18/s (mine was a per-minute count divided
  down, which understates a burst).
- **Bifrost: ban risk RESOLVED** (their 19c9896, Scott ruled). Their fail2ban
  jail counted any 403/404 with no `ignoreip`, so a 20x403 burst could ban a
  paying customer off **every service on the VM**. Fixed surgically by
  distinguishing nginx-direct 403s from app 403s via `$upstream_status` (32
  lines dropped vs 74,472 scanner blocks kept, ~0.04%), plus an `ignoreip`.
  A/B verified live.
  - GP's contribution: expired/invalid/revoked tokens return **401**, not 403,
    so that path was never exposed. `unknown_tier` (403, on `/v1/translations`)
    is **unreachable in current config** — every tier value in prod is in
    tiers.yml. Only `Account disabled` (403 after the JWT decodes) remained.
- **Shoulder Surf: pinged, awaiting.** Asked for (1) a translation concurrency
  cap (4 in flight) and (2) stop surfacing the allocation message unless
  `detail.code == "allocation_exhausted"` is present; a bodyless 429 should back
  off and retry.
- **Held deliberately:** Bifrost offered a JSON 429 with a machine-readable code
  at the edge. Waiting until SS lands their parser change, so the body shape
  does not move underneath them.

---

## 5. Waiting on Scott

1. **His own subscription row.** MRR replays `to_tier` from the event log, so he
   is counted at **$14.99/mo on a purchase Apple never saw**. The accurate fix
   is an **appended corrective event**, not an edit. If he wants Pro for
   testing, the honest lever is `simulated_tier` or admin, not a subscription
   row. **Nobody has touched his tier.**
2. **Install go for Shoulder Surf** — two device checks queued behind it: the
   `report_language` wire check (their 8e3e767, unpushed) and the project-chat
   entry UI.
3. Test-account revocation (delete ddc3df33 refresh_tokens; one-liner in
   `project_RESUME_2026_08_25`), `enforce_admins` on branch protection, the
   300-char research-notes cap, the 1.16 (1212) upload.
4. **Local junk:** `data/remote-config/remote-config.aside/remote-config.aside/...`
   is a recursive directory producing "File name too long" at test boot.
   Harmless to the suites; a sync script recursed into its own backup.

## 6. GP follow-ups, unclaimed

- A guard so tier-config drift cannot turn `unknown_tier` into a 403 storm on
  the burst endpoint. Unreachable today, reachable the moment a tier is renamed
  while users still carry the old string.
- Five stale worktrees under session `4ecad7ea`'s scratchpad (`wt-card2`,
  `wt-globallang`, `wt-h1`, `wt-md`, `wt-report`), all for merged branches.
  Left alone in case a sibling session is in them.

---

## 7. Lessons filed today

- **Verify canonical, not derivative.** Every field I checked to clear Scott's
  subscription was the client's own claim replayed back. Apple was the authority
  and I never asked it.
- **Verify a config change from the PROCESS, not the artifact you edited.** A
  file read, a `reload` that returns OK, and a green diff can all agree with
  each other and all be wrong about what is serving. (Bifrost's `jail.local` was
  a single-file bind mount and git replaced its inode, silently severing it. GP
  is not exposed — one named-volume directory mount — but `.env.prod` is read by
  compose at container *create* time, which is the same shape.)
- **A render change needs a PIXEL test.** My first zero-state duplication test
  asserted only `im.size == (1200, 630)`, true whether or not the duplicate was
  drawn; it stayed green under sabotage.
- **Judge card copy on a REAL iMessage thread, not a PNG.** The subject-twice
  bug was invisible in a render and in the design canvas, because neither
  includes iMessage's caption.
- **Sabotage-revert trap, third hit.** `git revert --no-commit` STAGES its
  changes, so a later `git checkout <file>` restored the index and silently
  wiped two uncommitted edits, which I then committed. The tell: a test that
  fails identically after the "restore". Commit or stash before any sabotage.
- Never name a shell variable `path` in zsh; it is tied to `PATH`, and a loop
  over it wiped the shell's search path and read as an outage.

## 8. Standing gotchas

- **Bump `CARD_VERSION` on every plan or render change**, same PR, or cached
  `<archive>.card.vN.png` sidecars keep serving the old image. Clear stale ones
  as root:
  `sudo find /var/lib/docker/volumes/ghostpour_ghostpour-data/_data/meeting_shares -name "*.card.v*.png" -delete`
- **Deploy is automatic** — "Build & Deploy" on main ships within ~3 min of
  merge. Check `/health` `git_sha` before assuming a manual pull is needed.
- **A scratchpad worktree needs BOTH `.venv` and `.env` linked**, or a class of
  tests fails with "jwt_secret Field required" and reads as a pre-existing env
  problem.
- `CZ_CQ_RECALL_TIMEOUT_MS` **shadows** the code default; both must move
  together (the code comment says so).
- The repo has **no auto-merge**; a branch that goes BEHIND needs
  `gh pr update-branch` and a fresh CI run.
