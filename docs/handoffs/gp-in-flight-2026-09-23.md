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
