# GhostPour session close, 2026-09-09

⚠ **READ THIS FIRST.** It supersedes `gp-in-flight-2026-09-07.md` for
everything it covers. That file is still correct on the N-400 lane's prompt
history and the three-team protocol; this one owns current state.

**prod = main = `afcf12d`, read off `/health` rather than off the merge.
Zero PRs open. Ten merged, #940 through #949. Working tree clean.**

---

## ⚠⚠ OWED BY SCOTT, in the order it bites

1. **The served N-400 budget dial.** The only thing blocking another team.
   The lane is exhausted and every N-400 turn returns HTTP 200 with empty
   text and `budget_exhausted`. Set `monthly_cost_limit_usd` to `-1` in the
   dashboard Configs tab, slug `n400/budget`. **Served value WINS over
   everything in the repo**, and every route to it needs the admin key.
   Two sessions (GP and bifrost) were refused that credential shape
   repeatedly; this is his browser and nobody else's.
2. **`com.weirtech.n400helper` into `CZ_APPLE_BUNDLE_ID`.** ⚠⚠ **NOT
   TOGETHER WITH ITEM 1. IN ORDER.** A sane per-user cap must be live
   BEFORE the bundle id lands. See "the ordering rule" below. Bifrost
   prepared it (backup `.env.prod.bak-20260909-n400`) and was blocked by
   their own classifier, so prod is still on the old value and the safe
   state held by accident.
3. **The limited-ads split**, before the ad campaign's first rows land.
   Command in the ✅ acquisition section.
4. **The App Privacy label**, before ad spend. Social checked the live
   product page 2026-09-08: no advertising or marketing purpose declared,
   while the app collects AdServices attribution and GP links it to an
   account. App Store Connect only, no build.
5. **The Apple Ads API key.** ⚠ An Account Admin must designate an API user
   in Account Settings, User Management, BEFORE any credential exists.
   Collect in one sitting: client id, team id, key id, private key, org id.
   Build against the Platform API `api.ads.apple.com/v1`; Campaign
   Management v4/v5 sunsets 2027-01-26.
6. **Whether to authorise the receipt-verification build floor at 1296.**
7. **The ContextQuilt hairpin**, a config decision. See below.
8. **Item F**: whether he already has the admin key for `/admin` in his
   browser. If yes, most of the relaying above stops.

---

## ⚠⚠ THE ORDERING RULE, and why "together" was the wrong word

GP wrote "these two must be decided together" into a config comment. The
auditor corrected it and they were right: **together is the one instruction
that protects nothing.**

    SAFE TODAY      cap 250, bundle id absent, nothing reachable
    DANGEROUS       cap -1 AND bundle id present
    THE RULE        a sane PER-USER cap goes back BEFORE the bundle id

⚠⚠ **The cap is PER USER, not a shared pot.** `app_month_spend_usd` sums
`WHERE user_id = ? AND app_id = ?`. With one QA identity that reads as
harness headroom. The day real users can authenticate it is an allowance for
EVERY SIGNUP, and `own_account_meter: true` means the shared account meter is
not there to catch the overflow.

⚠ **The apps.yml FLOOR is now `-1` (#945).** That inverts the rule that used
to be written there, that an unreadable config must never mean unlimited.
Deliberate: unlimited is the intent for a dev lane, and the old floor's only
remaining effect would be to silently re-impose $5 and halt the lane again.
**Consequence: after the bundle id lands, a config read failure means
unlimited per signup rather than a $5 halt.** That failure mode used to be
self-limiting and is not any more.

✅ **`audit_uncapped_reachable_apps` now guards it**, at startup AND on every
served-config write, raising an incident rather than logging. The write path
matters most: the bundle-id half needs a container recreate (caught at boot),
but setting a cap to -1 is a browser save that hot-reloads with NO restart.

⚠ **Do NOT use `budget.enabled: false` as a shortcut to uncap.** It makes the
gate not run, ships through a normal deploy with no admin key, and silently
puts N-400 back ON the shared account meter, so every QA turn drains
ShoulderSurf's allowance. That is #850's bug from the other direction,
landing on the app that has real users. The auditor caught this before it was
tried.

---

## ✅ SHIPPED THIS SESSION

**Tiers and versions**
- Plus generation cap verified serving at 50 in all four locales, checked
  from OUTSIDE the container over the public host. A device holding the old
  catalog does refetch (`X-Config-Version: 60` returns the full payload).
- ⚠ **Two tier endpoints, only one honours the version header.**
  `/v1/config/tiers` short-circuits; `/v1/tiers` (`chat.py:1381`) never reads
  it and reassembles per request. Consequence that inverts a stated rule: "a
  value change without a version bump is invisible to every device" is TRUE
  of `/v1/config/{slug}` and FALSE of `/v1/tiers`, which is the endpoint the
  app actually calls, 248 to 1.
- ✅ **App Store is live on 1.17 build 1659** (#944). BOTH blocks moved.
  ⚠ The appstore channel had been serving 1.0/803, so App Store users were
  told 1.0 was current and saw NO update prompt at all. A MISSING toast, not
  a false one, which is why it sat unnoticed. The `latest` fallback had a
  TestFlight invite as its upgrade URL.

**Acquisition / Apple Search Ads** (#942, #946, #948, #949)
- Subscribers split into `started` / `trialing` / `paid` / `paid_unconfirmed`.
  ⚠⚠ **`paid` keys on a RENEWAL, and Scott confirmed Pro has NO introductory
  offer, so NO Pro buyer reaches `paid` inside a short test.** Every Pro
  purchase sits in `paid_unconfirmed` for a full billing period. A zero in
  `paid` is not a real zero. Fix needs a different signal for a non-trial
  first purchase and is NOT BUILT.
- Per-country breakdown, **crossed with campaign** rather than rolled up: one
  campaign spans five LatAm storefronts, one country is served by two US
  campaigns, and a rollup answers neither.
- Sweep liveness alerting, `scripts/attribution_status.sh`, and the alert fix
  below.
- ⚠ `activated` is telemetry-derived and purges at 30 days.
- ⚠ A fourth campaign, SS US Sprint, starts midnight America/Chicago
  2026-09-10 and ends 09-12. **First real ad-driven rows land then**, before
  the 15th.

**Read the pre-campaign floor:**

    ssh -i ~/.ssh/gcp_deploy_key scottguida@35.239.227.192 'bash -s' <<'SH'
    K=$(sudo grep -m1 '^CZ_ADMIN_KEY=' /opt/ghostpour/.env.prod | cut -d= -f2-)
    curl -s "https://cz.shouldersurf.com/webhooks/admin/acquisition?days=365" \
      -H "X-Admin-Key: $K" | python3 -c "
    import sys, json
    k = json.load(sys.stdin)['kpis']
    for f in ('total','attributed','attributed_limited','organic','linked','started','trialing','paid','paid_unconfirmed'):
        print(f.ljust(18), k.get(f))"
    SH

**Other**
- Memory upsell suppressed during live meetings (#943). ⚠ SS proposed keying
  on `call_type` alone; our own dial map says a legacy client sends
  `call_type=query` INSIDE ProjectChat, so that rule would have silenced the
  nudge on the surface it belongs on most, invisibly, on the oldest installs.
  Checks `prompt_mode` too.
- `_freshness.cached` fixed (#947). See the defect shapes below.
- N-400 dev lane uncapped in the shipped config (#945).

---

## ⭐⭐ THE ONE THING WORTH CARRYING: four defects that all passed a green suite

Every one of these shipped or nearly shipped with 3900+ tests green, and each
was found by DELETING something and seeing whether anything noticed, or by a
peer reading the thing against real behaviour. None was findable by review.

1. **A guard that was correct and never called.** The memory-nudge gate had
   unit tests on its pure discriminator. Deleting the guard ENTIRELY, so the
   nudge was suppressed on every surface, left the full suite green: the
   emission site had no coverage at all. A correct function nobody calls is
   indistinguishable from a correct one that is called.
2. **A test that did not test what its name claimed.** "No pending rows"
   built its state through the endpoint, and the liveness check runs on EVERY
   ingest, so a fresh pending row existed during setup. It caught a sabotage
   through a path its own docstring denied. **This bit three separate tests
   of mine.** Rule: build state in SQL, use exactly ONE request as the trigger.
3. **A default argument that made a status field lie.** `_woven_meta` took
   `cached: bool = True` and only the degraded branch overrode it, so every
   success claimed to be cached, including a cold miss computed a microsecond
   earlier. ⭐⭐ CQ's framing is the one to keep: **a check that passes by
   construction.** The field could not fail the check it existed to serve,
   and that check is "did the eviction work". Ask of any status field: is
   there an input for which it reports the bad state?
4. **An alert that could not tell its two causes apart.** `attribution_sweep_
   stalled` measured the age of the oldest pending row, which is identical
   for a dead sweep and for a healthy one holding a token Apple has no record
   for (404s forever BY DESIGN). It fired on a healthy system: **1 stuck row
   against 232 exchanged, most recent exchange 19 SECONDS after arrival**, and
   nearly gated a campaign launch. Fixed the SIGNAL, not the threshold: it now
   needs waiting work AND no recent exchange, which makes the existing name
   true. ⚠ The old sweep logged nothing on a pass that was all retries, so
   healthy and dead produced IDENTICAL output: none.

⭐ **And the process defect underneath #4.** The query that settled it had
lived only in chat messages for a week as "one command on Scott's side", so
nobody ever ran it, while the answer it held was the open gate on a campaign
launch. It is now `scripts/attribution_status.sh` (#948). **Its first real run
found a bug in its own last query.** Written down is necessary and not
sufficient. It has to be RUN.

⚠ **Almost nothing this session was found by reading our own code first.**
Same conclusion as 2026-09-07, now with four more instances. Three of the
corrections were to GP, from SS, CQ and the auditor, and each cost the other
team something to volunteer.

---

## Open questions and watches

- ⚠ **Nobody has recorded the pre-campaign attributed/limited split.** Once
  ad installs land it is separable by `created_at` but no longer free.
- ⚠ **The ContextQuilt hairpin.** GP calls `cq.shouldersurf.com` by the
  PUBLIC hostname and routes back through Bifrost to a container beside us.
  It works. It costs a round trip and it is why every request arriving at CQ
  carries Bifrost's address, which cost CQ real visibility while diagnosing.
  One env var (`cq_base_url`), conditional on a shared docker network.
  CQ has NO preference; Scott's call.
- **The N-400 prompt work is queued and not started.** The auditor brought
  hand-labelled evidence: 25 not_told against 16 told, so ~60% of deferrals
  leave the applicant unable to know she must come back. Mechanism is always
  the same, an acknowledgement that never returns to the doubt ("Anotado",
  "Entendido", "Perfecto anotado"). Their four asks: make a deferral audible;
  never acknowledge-and-defer with the hedge on the wrong field; ambiguity
  produces a QUESTION not a guess; and the counterweight, DERIVE when the
  evidence is already there. ⚠ Do NOT hand a judge the prompt rule text, and
  do NOT ship a regex detector (~40% false positives, measured).
  ⚠ They also asked what happens on model selection for unparseable input,
  unanswered.
- **The 1.15 pending row expires ~23:22Z 2026-09-09**, taking
  `attribution_tokens_expired` to a count of 1. Costs nothing: Apple never
  had a record for it.

---

## Gotchas

- ⚠⚠ **A green LOCAL suite is not a check on a served-config change.**
  `data/remote-config` shadows `config/remote/`, CI has no overlay. The
  uncap's CI failure was invisible locally because the local overlay serves
  5.0. Move it aside and re-run, or treat CI as the only honest check.
- ⚠ **The admin config PUT is a FULL DOCUMENT REPLACE.** A partial body takes
  the number and DELETES the copy, and `_FALLBACK_COPY` is in the right shape
  so the client still renders a card. Seeing a card is not evidence the copy
  survived. Use the dashboard editor, which does GET-whole/PUT-whole.
- ⚠ The woven digest cache is an in-process dict, day-stable (the day is on
  the ENTRY, not the key, so a roll is a stale HIT that self-heals on the
  first request). **A deploy is the ONLY eviction we have.**
- ⚠ Scott's tier flipping between Free and Pro was ShoulderSurf installing
  builds: every relaunch re-posts the synthetic StoreKit transaction to
  `/v1/verify-receipt`, which still grants a tier on the client's word.
  Enforcement is off by design; the fix shape is a build floor at **1296**
  (the first build that can send the JWS), not the boolean, because 1.15
  subscribers and part of 1.16 cannot send it.
- ⚠ `report_incident` is idempotent per (category, subject) while OPEN, so a
  sustained outage emails once. Repeated emails mean repeated open/resolve
  cycles, not a loop.
