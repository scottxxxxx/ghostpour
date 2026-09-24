# GhostPour, in flight at exit, 2026-09-24

Session `cloudzap-81`, written 22:25Z. **Read this instead of
`gp-in-flight-2026-09-23.md`**, which grew by accretion across two days and
carries corrections layered over earlier sections (its section 8 reads as an
open blocker and its 8c says Scott already resolved it). Everything still
true from that doc is restated here. It stays for the detail and the receipts.

## 1. State at exit

- **prod = `5a5e1c5`**, read from outside on `/health`. Main tip `ab65543`,
  docs only on top.
- **Zero PRs open. Nothing uncommitted. No background work running.**
- Shipped and each verified from OUTSIDE, individually: #1019 companion
  comment, #1020 labelled option mints, #1021 choice_fields consumers, #1022
  whale check off the request path, #1023 Jev panel marks and multiples,
  #1024 the sixth built-in prompt.

## 2. Owed, and by whom

**GP**
- **The Jev-question iteration for the N-400 evidence check. NOT STARTED.**
  The check catches **1 unsupported mint in 4** on the auditor's real
  labelled turns, while the synthetic eval said it was strong. Fixtures
  `qa/labelled-option-mints.json`, runner
  `qa/jev_labelled_option_mints_eval.py`. The lever is the question and
  criteria in `support_questions` (`n400_evidence_support.py`). ⭐ The
  auditor's note to carry in: two of the three outside-options marks are the
  lane INVENTING an id when unsure of an option.
- Small: the Jev panel's Haiku-latency query plans as `SCAN usage_log`
  (27 ms warm, unmeasured cold). Predates all of this.
- When SS names the release carrying their usage-statistics switch, decide
  whether to count Apple Ads attribution first (see 4).

**ShoulderSurf**
- **Device proof of their `c6c8f46`.** Needs a real meeting over 5 minutes
  **tagged from the saving screen**. Only Scott can produce it. Ask for the
  meeting id and confirm a POST report arrives in our log.
- The tags question (see 4).

**Scott**
- ⏰ **Comp codes expire 2026-10-01**, by hand in App Store Connect.
- Two rulings only if Apple rejects his dispute (see 3).
- Optional: what to do about two accounts on Plus that nobody bought (see 5).

## 3. The App Store rejection, RESOLVED as a dispute

iOS 1.18 (1921) was rejected under 5.1.1(v), registration required before an
IAP that is not account based. **Scott chose to dispute rather than build**,
and replied to App Review on 09-24 at 14:37 arguing the plan IS account based
(per-account server-side allowance, Memory, Project Chat and reports stored
only on our servers, sign-in optional for the rest of the app, deletion in
app). **No new build; 1921 stands.** SS's purchase ungate is REVERTED
(`ebc8ef7`), so our `assn_unmatched` alert stays exceptional.

⭐ The reviewer used **Settings > Account**, where Upgrade appears only after
Sign in with Apple. SS's ungate had targeted a different page the reviewer
never opened.

**Parked, only if Apple holds firm.** GP read its side and built nothing:
- No anonymous identity exists. `auth.py` has exactly `/apple` and
  `/refresh`; every "anonymous" hit in our tree is telemetry.
  `verify-receipt` requires a JWT, so signed out it cannot be called.
- ⭐ **A restore would NOT move the plan.** verify-receipt only does
  `UPDATE users SET original_transaction_id = NULL ... AND id != ?`, so a
  reinstall onto a fresh identity grants the new row the tier and leaves the
  old one marked paid with no Apple linkage. Every reinstall orphans a stale
  paid row.
- ✅ `apple_webhooks.py` already falls back to matching on `appAccountToken`.
  Built and dormant, waiting on SS to set it.
- **CQ is a third team.** Memory is keyed by `subject_for(app_id, user_id)`,
  a deterministic `app:user_id` string with no indirection, so a merge means
  moving data inside CQ.
- **UNRULED, Scott's word in his own session, a peer relay does not stand:**
  whether anonymous users get a free allowance, and the merge survivor rule.

## 4. Cross-team, live threads

**ShoulderSurf**
- ⚠ **Incoming:** their "Usage statistics off" switch (`0adfb91`) also stops
  AttributionClient. **Not in 1921; ships the first weekly after 1.18, and SS
  will name the build.** Default stays ON, so only active opt-outs drop, which
  makes it a slow drift rather than a step change: smaller, but HARDER to
  attribute, so the build number matters more not less. Apple Search Ads
  attribution has never been measured end to end, so counting before it ships
  is the cheap move.
- ⚠ **Unresolved, nobody can currently settle it:** Scott saw no tags on the
  09-23 meeting; SS says the tags were written. **GP stores no meeting tags at
  all** (`tag_taxonomy` in `reports.py` is only an INPUT naming allowed tags;
  AI tags come back inside the report JSON). If his manual tags are also
  absent, the tag write failed too and `c6c8f46` does not cover it.
- ✅ Closed: the meeting that produced no report (their save handler returned
  before the report code when tagged from the saving screen), the iPad query
  that reached us and succeeded but never rendered in build 1926, the
  `capturesImage` wire question, and the sixth prompt.

**N-400 auditor**
- Reachable only between turns; the socket changes. **Write to
  `docs/handoffs/gp-to-auditor-<date>.md` FIRST and send second.** A bounced
  send is fine, they read the file. Sections 10 to 18 of the 09-21 auditor doc
  hold this session, with the cold-turn proof closed at 18.

## 5. Prod ops performed

- Scott's `scott@weirtech.com` (`fa4d903c`) set to free for a UI test and back
  to pro at his word, through `POST /webhooks/admin/set-tier`, never
  hand-edited SQL. **Net: pro, unlimited limit, counters zeroed, window to
  2026-10-23.** Lauren's untouched.
- ⚠ **Neither tier tool holds on that device.** verify-receipt re-grants pro
  on the next launch, and that path clears `simulated_tier` too, so the
  simulate route is no escape. The only way to test free is to stop the device
  presenting a receipt.
- **Asked, answered, not acted on:** two accounts are on Plus with no purchase
  at all. `ethanguida@icloud.com` and `jblackburn@gmail.com` have zero
  subscription rows, zero events, no transaction id, and were both written at
  the identical microsecond `2026-08-26T20:40:38.430869`, so one statement set
  both. Neither has ever made an API call. Both also carry `ever_subscribed=1`,
  which is false and DOES affect promo targeting. A third user is on Plus via a
  genuine Apple free trial (active to 09-30, auto-renew off), which is correct.

## 6. Findings, none of them fixed

- **A per-app config silently shadows a translation.** `candidate_slugs()`
  puts `{app_dir}/{name}` before the flat `{name}`, and the resolver breaks on
  the base of the first candidate that exists, so a per-app file with no locale
  siblings beats the flat translation. **ShoulderSurf is PROVEN unaffected** in
  es, fr and ja (no per-app dir, no tester or build-pin variants).
  TechRehearsal serves English for `idle-tips` and `protected-prompts`.
  ⭐ Nothing caught it because the locale tests walk `config/remote` with a
  NON-recursive `glob`, and `test_per_app_config.py` never mentions locale.
- ⚠ **Receipt enforcement is off on a live App Store app.**
  `require_signed_transaction` reads false, so verify-receipt alerts on an
  unverifiable receipt and grants the tier anyway. **Raised once, Scott's call,
  do not re-raise.** The August note says it needs a build floor rather than a
  boolean, and the shipped gate is a plain boolean, which is why turning it on
  today would also refuse older builds.

## 7. Gotchas earned this session, all in memory too

- ⭐ **A bundle change does NOT reach prod when the pointer passes through a
  LIST.** `hydrate_overlay_additions()` says "lists are atomic". That is why
  #1024 needed four scoped `sync-from-bundle` calls, one per locale slug, and
  a read-back of the PUBLIC body from outside.
- ⭐ **A count can be a PROXY for the property you meant.** A test asserted
  `len(defaultPromptModes) == 5` to mean "Ask is a separate field, not a chip".
  A legitimate sixth made the count wrong while the property held. Rewritten to
  assert the property.
- ⭐ **journald survives a redeploy; `docker logs` does not.** The container
  logs to journald tag `ghostpour`.
- ⭐ **Correlate cross-device by `request_id`, never by time.** Scott's iPad
  clock runs ~57 s fast; the VM is NTP locked (chrony stratum 3, 1.5 µs).
  We return `x-request-id` and persist it at `usage_log.metadata.request_id`.
- ⭐ **Report device builds as "as of <time>", never as state.** Both SS and I
  quoted stale build numbers within hours.
- ⚠ **A CONFLICTING PR gets NO CI run at all, silently.** "No checks reported"
  after a push means `gh pr view --json mergeable` first.
- ⚠ **The pinned aiosqlite starts a NON-daemon thread per connection.** A
  background task whose loop closed mid-connect hung CI for 50 minutes AFTER
  printing "4445 passed". The local venv runs a newer aiosqlite and exits, so
  two local full runs lied.
