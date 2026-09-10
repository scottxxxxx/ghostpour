# GhostPour session close, 2026-09-10

⚠ **READ THIS FIRST.** It supersedes `gp-in-flight-2026-09-09.md` for
everything it covers. That file still owns the "four defects that passed a
green suite" section and the N-400 ordering rule; this one owns current
state.

**prod = main = `963a44d`, read off `/health` and `docker inspect`, not off
the merge. Zero PRs open. Six merged and deployed, #951 through #956, plus
the ContextQuilt network switch, an ops change with no PR. Working tree
clean.**

Session ran from the 09-09 afternoon pickup to 04:20Z on 09-10, one GP
session (cloudzap-16), with CQ, SS and Bifrost sessions all live and
answering in real time.

---

## ⚠⚠ OWED BY SCOTT, in the order it bites

1. **The imported-meeting ingest gap.** SS read it off their client code:
   a LIVE meeting's end-of-session upload is a durable debt (written at
   session start, removed only on 2xx, replayed on next foreground with
   `X-CZ-Recovery`), so a deploy window delays it and never loses it. An
   IMPORTED meeting's upload is a single POST with no ledger; a 502 there
   is a manual retry from the meeting's menu, and since CQ's 09-09 change
   the ingest stream is the ONLY copy of a raw transcript. Two fixes, pick
   one: SS writes the import path into the same ledger (small client
   build), or the edge retry on Bifrost's NPM proxy host
   (`proxy_next_upstream`, Advanced tab, a click) which covers every deploy
   with no build. Neither is started.
2. **`com.weirtech.n400helper` into `CZ_APPLE_BUNDLE_ID`.** Unchanged from
   09-09. Bifrost's permission layer refused the write three times; backup
   `.env.prod.bak-20260909-n400` exists; file unchanged. ⚠ The served N-400
   cap is STILL 20.00 (read off prod 09-09 afternoon), so the ordering rule
   from the 09-09 file holds by accident: capped AND unreachable. The main
   QA identity sits at 19.98 of 20.00 and every turn blocks on the
   pre-check estimate. Item 1 of the 09-09 list (uncap via dashboard) is
   also still undone.
3. **The rest of the 09-09 list is untouched:** limited-ads split, App
   Privacy label, Apple Ads API key, receipt-verification build floor at
   1296.

---

## ✅ SHIPPED THIS SESSION

**#951, the stalled-sweep alert, third signal.** #949's signal fired AGAIN
at 20:02:57Z on 09-09, thirty minutes after its own deploy, at the instant
the next organic token arrived. It measured time since the last successful
exchange, which only moves when a NEW token arrives; no install landed for
14.6 hours, so the check read 14.6 hours of dead sweep while the log showed
the sweep retrying the stuck 1.15 row every 60 seconds. Now every answer
from Apple for a row that stays pending stamps `last_attempt_at`, and
stalled means a row older than 15 minutes whose stamp is missing or older
than 15 minutes. Transport failures do NOT stamp, deliberately: an alert
named "exchange has stopped" should fire when Apple is unreachable too.
`scripts/attribution_status.sh` section 3 prints the stamp.
⚠ The 1.15 row expired at 23:22Z before the deploy, so **no pending row
has been stamped yet.** That receipt arrives with the next token Apple has
no record for.

**#952, the Subscriptions tab.** Scott's screenshot: `Subscriptions error:
Can't find variable: d`. `_renderSubscriptions(rep, events)` read
`d.mrr_trend`. Broken since the MRR chart landed in #553 on 07-28, six
weeks, unnoticed. One token. ⚠ Scott's own tab kept showing the error AFTER
the deploy because the page's JavaScript was loaded before it and the build
badge is set once at page load; the Refresh button only refetches data. A
page reload fixed it. Verified rendering in a fresh tab on the new build.

**#953, connect retry on all three ContextQuilt clients.** Bifrost's edge
logs: on the public hostname nginx re-proxied a failed upstream connect for
~49 seconds, absorbing every CQ container swap (113 hairpin 502s in three
weeks, none reached a phone). GP had NO retry of its own. Now
`httpx.AsyncHTTPTransport(retries=3)` on the shared client and both ad hoc
ones, connection establishment only so POST stays safe, and it covers
NXDOMAIN during a container attach (verified in httpcore's source: OSError
maps to ConnectError, the retry loop catches it). ⚠ **Never exercised.**
CQ's 04:16:32Z restart happened with zero GP traffic. CQ will announce
their next deploy in advance; read GP's log across it.

**#954, `X-CZ-Recovery` crosses the capture hop.** CQ found the header SS
sets on a replayed upload appeared nowhere in their source or logs. GP read
it, logged it, and never put it on the outbound request. The `to_name`
shape exactly. Forwarded verbatim when present, never invented when absent;
the absent-means-absent sabotage is the one CQ said they were most exposed
to. CQ's half (#476, stamps the durable stream entry) went live 04:16Z, so
**both halves are in for the first time.** ⚠ What CQ's 61 byte-identical
repeated ingests ARE stays OPEN on both sides. Their count spans April to
09-03 in two bursts (86 in May, 33 in September); GP's 48-hour zero of
recovery-tagged captures never overlapped it. The first labelled replay
settles it. Do not let "probably re-ingest" harden.

**#955, `output_language` echo.** Top-level on the `/v1/chat` JSON body and
on the streaming `done` event: the primary subtag of the directive GP sent,
or null. SS's translation redesign stamps every generated artifact with it.

**#956, English is a directive; `report_language` on generate.** Scott's
ruling, given in-session: the phone's language wins in every case. Until
now `locale: en` appended NOTHING and the served summary/analysis recipes
say "write in the language the participants speak in the transcript", so
an English phone with a Spanish meeting got a Spanish summary (and report:
the report lane had its own `locale != "en"` exclusion). Now `en` is
directed in both lanes, `output_language` echoes `en`, null means no
locale anywhere, and a bare `Accept-Language: en-US` is directed English
through a directive-side reader (`accept_language_primary`) because
config's `_parse_accept_language` maps English to None on purpose for its
fifteen bundle-lookup callers. The report generate body now accepts
`report_language`, precedence report_language, transcript_language,
Accept-Language, echoing what was used. SS's build already sends it.
⚠ Report cache is one row per meeting (`UNIQUE(meeting_id)`, INSERT OR
REPLACE); a regeneration REPLACES the prior language and GET returns the
last one generated. SS built around it client-side; a (meeting_id,
language) key is their phase 4 ask.

**The ContextQuilt network switch, an ops change.** `CZ_CQ_BASE_URL` in
`/opt/ghostpour/.env.prod` from `https://cq.shouldersurf.com` to
`http://contextquilt:8000`, backup `.env.prod.bak-20260910-cqinternal`,
recreated at 00:54:59Z with the deploy's own compose command. **Confirmed
end to end by all three sides:** GP's log dials `contextquilt:8000`; CQ saw
twelve app-level requests from 172.18.0.10 and zero from Bifrost's
172.18.0.9; Bifrost saw zero hairpin hits while the phone made ten woven
calls. Plain HTTP on that hop; edge rate limit and fail2ban no longer see
it (they never touched it: 7352 x 200, 0 x 429 in three weeks). ⚠ Proxy
host 5 stays for the dashboard's `/api/dashboard/*`, which will arrive at CQ
from Bifrost forever; do not read that as a leak.

**Recorded, not built:** the pre-campaign acquisition floor, read 09-09
afternoon over 365 days: total 238, attributed 0, limited 32, organic 189,
linked 132, started 23, trialing 4, paid 6, paid_unconfirmed 10. That was
the 09-09 file's first open question.

---

## ⭐⭐ THE THINGS WORTH CARRYING

1. **A health signal must read the work itself.** Two alert signals in two
   days inferred the sweep's health from something other than the sweep's
   own work (age of a row, then age of an exchange) and both fired on a
   healthy system. The third reads the footprint the sweep leaves. Ask of
   any liveness check: what does the thing being watched WRITE when it
   works, and am I reading that?

2. **A sabotage that stays green is a coverage gap until proven
   otherwise.** Removing #951's migration left the whole file green because
   every test starts from a fresh database whose CREATE TABLE already
   carries the column. Prod has the old table. The test that builds the
   old shape and runs the migrations is the only one that can see it.

3. **"Merged" is a claim; the PR page is the fact.** Scott said "merged"
   twice and GitHub had no merge or close event either time (likely the
   two-step confirm button). Both times I read the PR state before saying
   "landed" and both times that was the difference between a true report
   and a false one. Read `gh pr view --json state,mergeCommit`, then
   origin/main, then the deploy run, then `/health`.

4. **A relayed ruling is not the ruling.** CQ relayed "Scott said switch
   to internal"; SS relayed "Scott ruled English is a directive"; SS then
   relayed "Scott said it to you directly in your session" when he had
   not. All three were accurate in substance, and I built each one and
   held the cut or the merge until Scott said it here. Two questions cost
   one line each. The prep was never wasted. CQ said they refused a relayed
   go-ahead from SS twice the same week for the same reason.

5. **Silence is not success.** CQ's restart was "the first real exercise
   of #953" until GP's log showed zero requests in the window. A test that
   did not run is not a test that passed, and CQ now announces deploys in
   advance so the next one is a test under known conditions.

6. **A zero and a count that do not overlap say nothing about each
   other.** I tied GP's 48-hour zero of recovery captures to CQ's 61
   repeats; CQ's most recent repeat was six days before my window opened.
   CQ's correction: "verifying the instrument does not fix a window that
   does not contain the event." Ask for the DATES on any lifetime count.

7. **The absent direction is the one people skip.** For a forwarded flag,
   absent-in must mean absent-out. A hop that INVENTS the label on a first
   send makes the READER's instrument lie with an authoritative-looking
   mark, which is worse than the unlabelled state (CQ, on
   `X-CZ-Recovery`). Same family as `cached=True`.

8. **Corrections I made out loud this session, kept here so they do not
   silently un-correct:** the 9-second deploy window was 25 seconds for
   #951 because the migration fingerprint moved (fast path back to 3.5s
   for #956); my commit message claimed I had moved an ALTER out of the
   schema list when it was already in MIGRATIONS; I told SS the device
   header "already parses to en" when it maps to None; I called
   `report_language` "dropped" when SS had never sent it on generate
   before that night; and the 48-hour zero above.

9. **A tool that prints nothing is not a tool that found nothing.** My
   sabotage runner passed `pytest $FILES` with an unquoted variable; zsh
   does not word-split, pytest got one bogus path, and the summary line
   never printed. Four "runs" of silence read as four passes until one
   was re-run by hand. Spell the files out or use `${=FILES}`.

---

## Open questions and watches

- ⚠ **#953's retry has never run for real.** CQ announces their next
  deploy; read GP's log across it: outbound `contextquilt:8000` calls
  succeeding with no `cq_recall_degraded` in the window is the pass; a
  `cq_recall_degraded reason=error` with a connect error is the fail; no
  phone traffic is "did not run", record nothing.
- ⚠ **No pending attribution row has been stamped yet.** The next token
  Apple has no record for is the receipt that #951's signal reads real
  work. `scripts/attribution_status.sh` section 3.
- ⚠ **The 61.** Open on both sides with dates. First labelled replay.
- ⚠ **Bifrost re-checks proxy host 5 after the morning's traffic.** A
  single hairpin request from the VM would mean something still holds the
  public base URL. None expected; the only place the hostname lived was
  the env var.
- **SS's next build** sends `report_language` on every regeneration and
  compares it to the echo; the report half of Scott's Spanish-phone test
  lights up then. If the echo ever disagrees with the request on 963a44d
  that is a GP bug and SS will send both values with the request id.
- **Queries stay translated, never regenerated** (Scott's reasoning via
  SS: a live query is a record of a moment). `/v1/translate` untouched.
- **SS phase 2:** a regeneration in another language mints a new
  `turn_id` per (meeting, language), because a reused completed turn id
  replays the stored body in the original language.

---

## Gotchas

- ⚠ **Reloading the admin dashboard makes NO GP-to-CQ call.** It reads
  `/webhooks/admin/*` only. Every GP route that reaches CQ's `/v1/quilt`
  needs a user bearer, so a token-plus-quilt pair in CQ's log is always a
  phone. Do not reload the dashboard to "generate traffic" and then read
  the silence as a broken switch. Do not mint a session to fake it either.
- ⚠ **The admin page's build badge is set once at page load.** A tab open
  across a deploy runs the OLD JavaScript and shows the OLD sha until the
  page itself is reloaded. Refresh refetches data, not code.
- ⚠ **Config's `_parse_accept_language` maps English to None on purpose**
  (fifteen callers use None as "default bundle"). For a language DIRECTIVE
  use `language_directive.accept_language_primary`, which keeps `en`.
- ⚠ **Each merge to main recreates the container** and opens a 502 window:
  ~3.5s when the migration fingerprint is unchanged, ~25s when it moves.
  Merge when Scott is not mid-recording; an imported meeting's upload in
  that window is lost to a manual retry until item 1 above is decided.
- ⚠ `gh pr merge` was refused by the auto-mode classifier once this
  session and allowed later on Scott's explicit "you merge them". Expect
  a prompt.
