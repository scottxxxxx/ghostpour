# GhostPour, in flight at exit, 2026-09-23

Session `cloudzap-81`. The 2026-09-22 work (PRs #1019 to #1023) is in the
addendum at the bottom of `gp-in-flight-2026-09-21.md`; this doc covers
2026-09-23, which produced no code changes on our side. It was findings,
prod ops, and two cross-team diagnoses.

## 1. State at exit

- **prod = `8c63bc2`**, read from outside on `/health`. Main tip is
  `a61b6bf`, docs only on top, so prod is level with the last code merge.
- **Zero PRs open.** Nothing uncommitted. All merged branches deleted.
- Everything shipped 09-22 (#1019 companion comment, #1020 labelled option
  mints, #1021 choice_fields consumers, #1022 whale check off the request
  path, #1023 Jev panel marks and multiples) is live and was verified from
  outside, each one individually.

## 2. Findings, none of them fixed, all with receipts

### 2a. A per-app config silently shadows a translation

Scott asked why some rows on the config dashboard show only `en`. Answer:
fine for the flat tree, a real hole for the per-app trees.

`candidate_slugs()` puts `{app_dir}/{name}` before the flat `{name}`, and the
resolver tries `{cand}.{locale}` then `{cand}` and **breaks on the base of the
first candidate that exists**. So a per-app file with no locale siblings wins
and the flat translation one candidate later is never reached.

Proved with the real resolver, not by reading:
- **`shouldersurf` (App Store, live): correct in es, fr and ja for all nine
  localized slugs.** It has no per-app dir, so nothing can outrank a
  translation, and no `tester/` or `build-lte-*` variants exist either.
- `techrehearsal` (not shipped): serves ENGLISH for `idle-tips` and
  `protected-prompts` (user-visible prose) plus `llm-providers` and
  `model-capabilities` (structural, harmless).
- `n400`: unaffected.

It reached real requests, all dev traffic: 5 `tr-idle-tips` and 3
`tr-protected-prompts` at `locale=es`, on 06-24, 07-13 and 08-19.

⭐ Why nothing caught it: `test_served_config_shape.py` and
`test_locale_parity.py` both walk `config/remote` with a NON-recursive
`glob("*.json")`, so the per-app dirs are outside every locale check, and
`test_per_app_config.py` has 24 tests that never mention locale. Two correct
filters, the intersection invisible to both (CLAUDE.md rule 8).

**To close:** point the coverage test at `rglob`, and decide whether a
per-app file should outrank a localized flat one at all. Not urgent while TR
is unshipped. Detail in memory `reference_per_app_config_shadows_translations`.

### 2b. ⚠ Receipt enforcement is off on a live App Store app

Said once, plainly, and left alone. `require_signed_transaction` reads
**false** on prod, so `/v1/verify-receipt` alerts on an unverifiable receipt
and then grants the mapped tier anyway. Scott's own iPad did it four times in
one minute today by accident: the Xcode local StoreKit config signs with a
test cert, GP correctly refused the signature (`x5c chain too short: 1 certs`),
opened a `receipt_unverified` incident, emailed alerts@weirtech.com, and
upgraded him to pro regardless.

That is the same shape as the 2026-08-28 incident. The detection half works;
the enforcement half is the dial. The note from August says enforcement needs
a BUILD FLOOR rather than a boolean, and the shipped gate
(`receipt_verification.require_signed_transaction`) is a plain boolean, which
is presumably why nobody has turned it on: flipping it today would also refuse
older builds that never send a signed transaction. **Scott's call whether that
becomes work. Not raised twice.**

## 3. Prod ops performed

Scott's `scott@weirtech.com` account (`fa4d903c-24c0-45d5-9fdb-b5496e32501b`),
set to free for a UI test and then back to pro at his word, through
`POST /webhooks/admin/set-tier`, never by hand-edited SQL. **Net state: pro,
limit unlimited, usage counters zeroed, allocation window to 2026-10-23.**
Lauren's account untouched.

⚠ Worth knowing for any future tier test: **neither tool holds.** The device
re-grants pro on its next launch through verify-receipt (see 2b), and that
path clears `simulated_tier` as well as setting `tier`, so the simulate route
is no escape either. The only way to test free on that device is to stop it
presenting a receipt (delete the Xcode StoreKit transaction, or cancel the
sandbox sub).

## 4. Cross-team, both closed today

### 4a. ShoulderSurf: a long meeting produced neither report nor tags

Scott's 12 minute meeting (`EB305735-91C3-4426-A897-447CB2F4F8C5`, Netomi,
iPad16,8, app 1.17 build 1900) gave an auto summary and nothing else. He asked
if the length caused it; it was the opposite.

GP's whole diagnosis came from our telemetry and request log without opening
their code: `meeting_stop duration_seconds=733`, transcript captured (10,335
chars), AutoSummary and MeetingTitle ran, **no PostSessionAnalysis** (correct
at >=300s), **no POST report ever arrived**, and two
`GET /v1/meetings/{id}/report` answered 404 `report_not_found`. ⭐ A 404 on a
read endpoint whose message says "Generate one with POST first" is the tell
that the client never asked.

SS read their own code and confirmed: tagging from the saving screen wrote the
tags and RETURNED before the report code in the same `SessionViewV2` handler,
**under a comment claiming the report below would still generate** (rule 7
exactly). Fixed in their `c6c8f46`. ⚠ **Builds green, NOT proved on a device.**
The proof needs a real meeting over 5 minutes tagged from the saving screen;
Scott is the one who can produce it. Ask for the meeting id and confirm a POST
report arrives in our log.

⚠ **Still unadjudicated:** Scott saw no tags, SS says tags were written. GP
stores no meeting tags at all (`tag_taxonomy` in `reports.py` is only an INPUT
naming allowed tags; AI tags come back inside the report JSON), so we cannot
settle it. If his manual tags are also absent, the tag write failed too and
c6c8f46 does not cover it.

### 4b. The iPad query that "did not respond"

Reached us and succeeded: `request_id 5e3beb554f99`, call_type query,
prompt_mode Ask, 10,647 ms, 200 streaming, 799 chars over 109 chunks. The
device logged the same 799 characters arriving and storing, so the loss is
between stored and shown in build 1926. Theirs, and they accepted it.

⭐ **The iPad's clock is about 57 seconds fast**, which made the two sides'
timestamps disagree. SS rightly challenged my first claim that the iPad was
the wrong one, since disagreement alone does not say which. Measured after:
the VM is `timedatectl` synchronized, chrony stratum 3, 1.5 microseconds fast
of NTP, agreeing with Google's Date header and a laptop within a second. So
the iPad is the outlier, and that is now a measurement rather than a guess.
**Correlate cross-device by `request_id`, not by time.** We return it as the
`x-request-id` header and persist it at `usage_log.metadata.request_id`.

## 5. Owed, and by whom

- **GP:** the Jev-question iteration for the N-400 evidence check. The check
  catches 1 unsupported mint in 4 on the auditor's real labelled turns while
  the synthetic eval said it was strong. Fixtures are in
  `qa/labelled-option-mints.json`, runner is
  `qa/jev_labelled_option_mints_eval.py`. NOT STARTED.
- **GP, small:** the Jev panel's Haiku-latency query plans as `SCAN usage_log`
  (27 ms warm, unmeasured cold). Predates today, not a fault in what shipped.
- **SS:** device proof of `c6c8f46`, and the tags question in 4a.
- **Scott:** comp codes expire **2026-10-01**, by hand in App Store Connect.
  And the 2b ruling if he wants it.

## 6. Cross-team addresses

The N-400 auditor reaches this machine as `fable-auditor-*` but its socket
changes between turns. **Write to `docs/handoffs/gp-to-auditor-<date>.md`
FIRST and send second**; a bounced send is fine, they read the file. Sections
10 to 18 of the 09-21 auditor doc hold everything from this session,
including the cold-turn proof closed at section 18.

## 7. Arrived at exit: SS's `capturesImage` prompt flag (answered, nothing built)

SS added an optional `capturesImage: true` on `defaultPromptModes[]` in
ProtectedPrompts (their main `8624b4a`, not in any App Store build). When set,
the quick prompt takes a picture at run time and sends it with the question.
They asked GP three things; answered by reading, no GP change made:

1. **Do we strip an unmodelled key? No, and there is nothing to model.**
   `config.py` loads configs with `json.loads` into plain dicts and returns
   `JSONResponse(content=data)` untouched; the only serve-time transform is
   slug-scoped to `tiers`. The dashboard write is `UpdateConfigRequest` with
   `data: dict`. The only GP code reading `defaultPromptModes` is the
   `requiresContext` 403 gate in `chat.py`, which reads two fields and
   rewrites nothing. **Declined their request to model it explicitly**: adding
   a schema where none exists creates the strip risk it is meant to prevent.
2. ⚠ **The real blocker, and it is ours to know (rule 3).** We serve the
   runtime OVERLAY, not the bundled file. `hydrate_overlay_additions()` would
   normally pick up an additive key at startup, but its own docstring says
   **"Lists are atomic"**, and `defaultPromptModes` is a list. So the key will
   land in the repo, pass CI, and change nothing on prod, silently. Same shape
   as the 2026-06-10 stale-`defaultPromptModes` incident. Serving it needs a
   scoped `sync-from-bundle` naming the pointer, or a dashboard PUT.
3. Which prompts get it is **Scott's product call**. Nothing is enabled.

**Owed by GP when Scott picks the prompts:** run the scoped sync, then read
the public `GET /v1/config/protected-prompts` back from OUTSIDE and give SS
the confirmation, so they check the wire rather than our word. They re-pull
with `refresh-remote-configs.sh` after that, never hand-editing.

## 8. ⚠⚠ BLOCKER FOR SCOTT: iOS 1.18 rejected, needs a GP identity decision

Arrived from `shouldersurf-19` after the close. **iOS 1.18 (1921) was rejected
under App Store guideline 5.1.1(v)**: the app requires registration before an
IAP that is not account based. Apple says registration must be OPTIONAL, and
that forcing it to enable cross-device access is not acceptable. SS proposes an
anonymous GP identity (`POST /v1/auth/device` with an install UUID, anonymous
JWT, purchase bound via appAccountToken, merge on later Sign in with Apple).

**GP's read, done, nothing built:**
- **Nothing exists.** `auth.py` has exactly two routes, `/apple` and
  `/refresh`. Every "anonymous" hit in our tree is telemetry (`telemetry.py`
  device_id) or APNs registration (`devices.py`). No anonymous identity, no
  JWT path, no App Attest.
- `verify-receipt` requires `get_current_user`, so signed out it cannot be
  called at all. SS's queue-and-replay is the only current path. Confirmed.
- ⭐ **Their step 3 does not work as assumed.** verify-receipt already handles
  the cross-account case (its comment names "anon-purchase then later sign-in
  to a different account") but it only does
  `UPDATE users SET original_transaction_id = NULL ... AND id != ?`. It NULLS
  the id off the other row; **it does not move the plan**. So a restore onto a
  fresh anonymous identity grants the new row the tier and leaves the old row
  marked pro with no Apple linkage. Every reinstall orphans a stale paid row,
  which compounds the abuse question rather than being separate from it.
- ✅ Cheap part: `apple_webhooks.py` ALREADY falls back to matching on
  `appAccountToken` ("Future: also try appAccountToken once SS sets it"), so
  that half is built and dormant, waiting on SS to set the token.
- **CQ is a third team here.** Memory is keyed by `subject_for(app_id, user_id)`
  in `cq_subject.py`, a deterministic `app:user_id` string with no indirection.
  Nothing to re-key on GP's side; a merge means moving data INSIDE CQ.
- **No user-merge machinery exists** in GP (`merge_people` is CQ people,
  `merge_overlay` is config). Survivor rule is new design, and CLAUDE.md rule 1
  is literally about a merge relocating identity.

**Why GP stopped there:** an anonymous free allowance and the merge survivor
rule are money and product-promise calls, and minting anonymous user rows is
the "ask before minting a session" category. Those are **Scott's word in his
own session**; a relay through a peer does not stand for them. Nothing was
designed or built, and SS was asked not to build on the relay either.

**Rough shape, explicitly a guess, not a commitment:** "anonymous with NO free
allowance, purchase binding only" is days. "Anonymous first-class users with
full merge" is a multi-week three-team change with a data migration.

**The fallback SS floated** (reply to App Review arguing the subscription IS
account based) is not dishonest: our allowance really is a metered server-side
budget per user. But Apple has stated a position. ⭐ The quoted rejection also
says Apple permits telling users that registering enables cross-device access
**provided registering is optional**, which hints at a middle path: let a
signed-out user buy and use the app on that device, offer sign-in rather than
require it. Whether a reviewer accepts that is unknowable from here and is
Scott's risk call against a blocked release.

### 8b. ⚠ The ungate alone has a GP-side cost, flagged before it ships

SS has ungated the Settings plan cards locally (`b6abe09`, not pushed) so a
signed-out purchase reaches StoreKit and queues verify-receipt. They confirm
they still do NOT set `appAccountToken` when signed out, so the dormant
fallback cannot fire for those purchases. Two consequences, read from
`apple_webhooks.py`:

1. **Our unmatched-purchase alert becomes routine.** A signed-out purchase
   produces an Apple notification whose `originalTransactionId` matches no
   user. `_alert_if_still_unmatched` waits `assn_unmatched_grace_seconds`
   (tuned because verify-receipt normally claims it in seconds, live-measured
   17s on 2026-07-27), re-checks, then opens an `assn_unmatched` incident and
   emails alerts@weirtech.com. Today that alert means something is wrong;
   after the ungate it fires on a NORMAL purchase and stays open until the
   buyer signs in. ⭐ That is the shape that gets an alert ignored and then
   trusted when it should not be ([[project_wip_stalled_alert_second_false_positive]]).
   **If the ungate ships ahead of the identity work, GP must widen the grace
   window or suppress the category first.** SS asked to tell us.
2. **The buyer who never signs in has no record here at all.** No user row, no
   tier, no otid; the queued verify-receipt never replays. They have paid
   Apple, renewals and cancellations keep arriving unmatched, and nothing can
   be delivered or revoked. Not a new bug, but the state the ungate makes
   REACHABLE, and the concrete form of "a signed-out buyer owns a plan that
   cannot do anything". **Argues for the anonymous identity landing WITH the
   ungate rather than after it.** Scott's call.

### 8c. ✅ RESOLVED 2026-09-24: Scott chose to DISPUTE, not build

Do not read 8 and 8b as open work. Scott ruled in the SS session (relayed by
`shouldersurf-19`, who confirmed he ruled there):

- **He replied to App Review at 14:37 on 09-24 arguing the plan IS account
  based** (per-account server-side allowance, Memory/Project Chat/reports
  stored only on our servers, sign-in optional for the rest of the app,
  deletion in app). **No new build; 1921 stands.**
- ⭐ The reviewer used **Settings > Account**, where Upgrade appears only
  after Sign in with Apple. SS's ungate had targeted a DIFFERENT page (AI
  Model > plan cards) the reviewer never saw. The rejection was about a
  screen neither team had looked at.
- **b6abe09 is REVERTED (`ebc8ef7`), neither pushed.** So 8b is moot: our
  `assn_unmatched` alert stays exceptional and GP has nothing to widen or
  suppress.
- **The anonymous identity is ON HOLD pending Apple's answer.** Scott has NOT
  ruled on the free allowance or the merge survivor rule, and they only matter
  if Apple rejects the argument. The findings in 8 (our restore nulls rather
  than moves; appAccountToken dormant; CQ is a third team) stay parked for
  that case.

⚠ **Incoming, lands on GP (severity DOWNGRADED 09-24 after SS confirmed the
default):** SS commit `0adfb91` (local) adds a "Usage statistics off" switch in
Settings > Privacy & About that ALSO stops AttributionClient. **It is NOT in
1921; it ships in the first weekly AFTER 1.18, and SS will name the exact
build.** ⭐ **The default stays ON**, so only users who actively flip it drop
out. That makes the effect a slow drift rather than a step change, which is
less alarming but HARDER to attribute, so knowing the build still matters. **A drop is indistinguishable from an
ingestion regression unless we know the release**, which is the
[[project_wip_stalled_alert_second_false_positive]] shape. Worse: Apple Search
Ads attribution has never been measured end to end
([[project_acquisition_attribution]], "nobody has counted"), so an opt-out
arriving first makes "nobody opted in" and "nobody arrived" indistinguishable.
Counting before it ships is the cheap move. SS asked to name the release.

**Device builds, settled from telemetry 2026-09-24, correcting BOTH sides:**
iPhone17,2 on 1.18 build **1947** (last seen 18:04Z); iPad16,8 on 1.18 build
**1940** (last seen 02:14Z). SS's handoff said 1947 for both; the iPad is seven
builds behind. My earlier 1926/1923 were a snapshot, already stale when
written. ⭐ Report device builds as "as of <time>", never as state.

**capturesImage (section 7) refined:** Scott has NO plans to enable it on the
existing five prompts. The open item is a proposed SIXTH built-in, "What's the
Answer?", with `capturesImage` true, appended at the END of the list. Pending
his go; SS will send the full entry for all four language files and GP runs the
scoped sync then reads the public body back.
