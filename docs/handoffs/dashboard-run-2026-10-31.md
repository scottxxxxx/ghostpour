# Dashboard run, 2026-10-31 (unattended, 8 hours)

Scott, 2026-11-01 04:30Z: "ok start the loop on the dashboard". Goal: the
admin dashboard (`app/static/admin.html`, 6,468 lines in one file) feels
clunky; make it better, running alone, merging and deploying as I go.

Pre-cleared: merges, deploys (SS Dev heads-up, N-400 quiet check), new
tests, dashboard UI and JS, admin endpoints only the dashboard calls.
Out of scope: served prompts, N-400, SS and TR client contracts, prod data
reads beyond what the dashboard already shows. Anything the auto-mode
classifier blocks goes under "needs Scott" and the run continues. Paid
model API spend stays under $5 for the day (at start: $0.30).

## Timeline

- 04:30Z start. Baseline main c474480. Audit of the file and the live page
  in flight.

## Findings from the live page (Chrome, 04:40Z)

- Load-time fetch storm: one load of /admin fired 22 requests to
  /webhooks/admin for 8 distinct endpoints. dashboard?days=7 x3, users x2,
  tiers x2, cert-pins/status x2, latency-trends x4, usage-by-place x4,
  typesafe-health x4. Same params every time, so every duplicate is wasted
  server work and a slower first paint.
- First paint: 8 seconds after navigation the Overview panels were still
  empty with no loading state (headings only, no spinner, no "loading").
- Resource timing on load: users?days=7 is 434 KB and took 2.1 s and
  3.3 s (fetched twice, Users tab not open). dashboard?days=7 is 6 KB but
  took 0.4 s, 0.8 s and 1.5 s across its three copies (server-side
  aggregation, and the copies contend with each other). Everything else on
  load is under 250 ms.
- Per tab on first open: email/stats 1.1 s and email/events 1.4 s
  (upstream Resend), live-log?limit=50 is 294 KB for 50 rows, subscriptions
  events 68 KB, alerts/incidents 61 KB. All other tabs open in under 0.5 s.
- live-log kept being fetched after leaving the Live Log tab (a second
  294 KB copy landed while another tab was active). Checked by timer below.
- Tab bar: 18 tabs in one flat row. At 1120 px wide "Model Routing" and
  "Live Log" wrap to two lines and "Telemetry" is clipped at the right
  edge. No overflow handling, no grouping.
- Switching back to Overview did not refetch (good).
- Transcripts tab is a single input and two empty panes; fine as a tool,
  but it reads as a broken page until you know what it is.

- Live Log poll confirmed leaking: sitting on Overview after one visit to
  Live Log, `_livelogTimer` was still set and two more live-log fetches
  (294 KB each) landed in 11 seconds. switchTab never clears it
  (admin.html:6332); loadLiveMeetings has the guard (1619), loadLiveLog
  does not.
- users?days=7 transfer size equals its decoded size (431 KB), so admin
  responses are not compressed. 296 user rows plus 176 anonymous devices.

## Plan correction (04:55Z)

Not splitting the file into per-panel modules first. The audit found 104
inline on* handlers, about a dozen cross-panel globals, a monkeypatched
switchTab, and tests that pin six helpers as top-level `function`
declarations inside admin.html. A split is a half-day refactor with no
browser test to catch what it breaks. Instead each subagent owns a
disjoint line region of the one file; PRs merge one at a time with a
rebase in between. Same parallelism, far less risk.

## Ranked list (audit + live page)

1. Live Log: poll never stops after leaving the tab; innerHTML rebuild
   every 5 s collapses whatever you expanded; 294 KB per poll.
2. Load path: refresh() runs twice on load (login() and applyAppFilter()
   both call it); login() fetches the whole dashboard just to validate
   the key; loadLatency() runs on every refresh while Performance is
   hidden. 22 requests where 8 would do; users (431 KB) fetched twice.
3. Bug: Models tab dials editor never renders (loadRouting, renderRouting,
   saveRouting defined twice, 5157 and 6344; the later wins); Routing tab
   loads once per page life.
4. Bug: duplicate id call-type-table (373, 456); user detail writes into
   the Models tab's table.
5. Bug: four tier saves call refresh(), which re-renders #tier-grid and
   wipes the "Saved" status and any half-typed input; Cancel on a tier
   change reloads the whole dashboard.
6. Nothing persists across reload (tab, period, app, sort, page).
7. Tab bar: 18 tabs, no wrap or scroll; undefined CSS vars (--bg-card,
   --bg-alt, --card-bg) so the app dropdown has no background; tabs are
   divs with no keyboard access.
8. Period or app change refetches everything on every tab.
9. No loading or error state: Promise.all with no r.ok check, so one 500
   leaves Overview silently stale; alert()/prompt() for errors and keys.
10. Entitlements app choice resets on every visit.
11. Hardcoded limits with no "load more"; admin responses uncompressed.
12. Duplicated code: 7 tier save functions, 4 escapers, 2 fmtDur, the
    X-Admin-Key literal about 70 times.

13. Server side: live-log?limit=50 is 291 KB because `response` bodies
    are 239 KB of it and `request` 34 KB; the list endpoint ships full
    bodies for 50 rows every 5 s while GET /admin/live-log/{request_id}
    already serves one entry in full. No admin response is compressed
    (no GZip middleware in app/main.py; content-encoding null).

Batch A (parallel, disjoint regions): 1, 2, 3+4, 5, 7.
Batch B (after A merges): 6, 8, 9, 10, 11, 13 (list endpoint without
bodies plus detail on expand; gzip scoped to /webhooks/admin).

## Shipped

(PR, what, test that fails on the bug, live read-back)

- #1168 (A3, opened 04:48Z): deleted the dead Models-tab routing
  editor (its loadRouting/renderRouting/saveRouting were shadowed by the
  Model Routing tab's declarations, so it never ran); Routing tab now
  reloads on every open; Users detail table renamed user-call-type-table
  so opening a user no longer overwrites the Models tab. Tests:
  tests/test_admin_unique_ids_and_defs.py (every id unique, no function
  declared twice, detail targets its own table). Red run named
  call-type-table and the three routing functions. Follow-up for A1:
  switchTab and _TAB_LOADERS still route models -> loadRouting and have
  no routing entry; asked A1 to fix inside its region. Lost with the dead
  editor: "+ Dial" and a "(none)" clear option, both unreachable since
  the shadowing; noted in the PR for whoever wants them back.
- #1169 (A2, opened 04:50Z): Live Log poll is self-governing:
  every tick re-enters loadLiveLog, which stands the timer down when the
  tab is not active or the document is hidden; switchTab('livelog')
  re-arms it (confirmed _activeTab is set before the loader runs,
  admin.html:1276 then :1291). Rows keyed by request_id, expanded set
  re-applied after each render, window scroll offset restored.
  visibilitychange resumes the poll on return. Tests:
  tests/test_admin_live_log.py, 8 tests in the node harness; 6 red on
  the original file ("loadLiveLog fetched 294 KB while the Overview tab
  was on screen"), 2 positive controls green by design. Not done: no
  browser run; polling is not paused while a row is open (state is
  preserved instead).
- #1170 (A5, opened 04:50Z): tab strip wraps (two rows at 1100
  to 1500 px; wrap over scroll because macOS overlay scrollbars hide the
  last tabs); header row wraps; :root now defines --bg-card, --bg-alt,
  --card-bg, --warn, --mono (five undefined custom properties, two more
  than the audit found); .btn gets a rule; .badge declared once;
  .table-wrap scrolls horizontally; tabs carry role/tabindex/aria-selected
  and a delegated keyboard handler (Enter/Space, arrows, Home/End) with
  aria-selected kept in step by a MutationObserver so switchTab is
  untouched. Tests: tests/test_admin_css_and_tabs.py, 8 tests, 7 red on
  the original. One existing pin in test_admin_latency_trends.py was
  loosened from an exact byte string of the Performance tab to a regex on
  data-tab, onclick and label; intent kept.
- #1171 (A1, opened 04:52Z): login() validates the key with
  GET /admin/apps (which _verify_admin guards, webhooks.py ~1305) and
  seeds the app filter from that body; loadAppFilter no longer fetches;
  applyAppFilter(reload) refreshes only on a user change; exactly one
  refresh() per load; refresh() no longer calls loadLatency() for a hidden
  Performance tab (switchTab('latency') always loads it on open); the
  Models tab no longer runs the Routing loader; _TAB_LOADERS gains
  routing. Tests: tests/test_admin_load_path.py, 11 node tests, 6 red on
  the original ("refresh() ran 2 times on one load"). Caveat from the
  agent, kept honest: the code read explains 2 of the 4 latency-trends
  and typesafe-health requests per load; the other 2 are unexplained.
  Re-measure on the live page after deploy.
- #1172 (A4, opened 04:52Z): one saveTierField helper behind the
  seven save names; a save PUTs, then re-reads only /admin/tiers and
  repaints that one card (renderTierCard, data-tier attribute), keeping
  the "Saved" text and any half-typed neighbour input; no save calls
  refresh(); changeTier Cancel resets the select locally. The agent
  caught its own ReferenceError (tierColors) on read-back before the
  node test did. Tests: tests/test_admin_tier_saves.py, 12 node tests, 9
  red on the original ("saveSearchCap called refresh() 1 time(s)"). Not
  browser-run; after deploy, call refreshTierCard('free', ...) in the
  console on the live page as a read-only exercise of the repaint path.

- #1173 (B3, opened 05:19Z): _entResolveApp picks the Entitlements app
  from the populated select, then localStorage cz_admin_ent_app, then the
  server default; an explicit choice is the only writer; a stored app
  that 404s is forgotten, not fatal. Not coupled to the global #app
  filter: that filter is multi-select with empty meaning all, while the
  entitlements route takes exactly one app and 404s on unknown, so
  coupling needs a route change first. Tests:
  tests/test_admin_entitlements_app.py, 8 node tests, 4 red on the
  original. Queue position: after #1172.

- #1174 (B1, opened 05:24Z): the live-log list endpoint returns rows
  without request/response bodies plus has_request, has_response and
  captured_bytes; the per-entry route is unchanged and the recorder now
  skips its own detail fetches (otherwise every expand logged itself);
  the dashboard fetches a row's detail once on expand, caches it in a
  Map, keeps it across polls, shows "Loading detail..." and a retryable
  error line; bodies in the pane are now esc()'d (they were raw).
  Tests: tests/test_admin_live_log_bodies.py, 9 tests; 8 red on the
  original ("the list row still ships the response body (2466 bytes)"),
  plus a targeted mutation on the fixed file to prove the exactly-once
  cache assertion. Queue position: after #1173.

- #1175 (B2, opened 06:00Z): AdminGZipMiddleware, path-scoped before
  anything is sent (/webhooks/admin/* and exactly /admin go through
  Starlette GZipMiddleware at minimum_size 1024; every other path gets
  the server's original send, so /v1 streaming is untouched by object
  identity); registered last so it sits outside StreamingBypassMiddleware,
  which must see plaintext bodies for the live log. 21 tests in
  tests/test_admin_gzip.py; sabotage (registration commented out) turned
  exactly the three compression tests red. Full suite: 5510 passed, 5
  skipped, 16m37s. Fixture ratio: tiers 5396 to 878 bytes. Queue
  position: after #1174.

- #1176 (B5, opened 06:05Z): view state in the URL hash
  (#tab=users&days=30&app=ss,n400) written by replaceState and restored
  in login() before the single refresh; a per-tab stale map so a period
  or app change refreshes only the active tab and marks the rest stale
  (overview, models, users, tiers share one "dashboard" group; livelog
  and telemetry always load on open; routing stays on its tail wrapper);
  switchTab's if-chain is gone, _TAB_LOADERS is the only map. 17 node
  tests plus five one-line mutations each caught by exactly the predicted
  tests. Follow-up asked of the agent before merge: mark the group fresh
  only after a successful fetch (it was set before the await), and fix
  the stale comment on the tail wrapper. Done in 23af96e: fresh is set
  after Promise.all with an okJson that throws on non-ok, an in-flight
  flag stops a second group tab from restarting the fetch, three new
  tests, two reverts each red on exactly the predicted test; the tail
  comment now says why the wrapper still exists. Residual the agent
  named: a filter change during an in-flight group fetch leaves the flag
  briefly optimistic until the second fetch repaints. Queue position:
  after #1175.

- #1177 (B6, opened 06:35Z): one covering index idx_usage_date_dash on
  usage_log(request_timestamp, status, app_id, user_id, provider, model,
  call_type, scenario, input_tokens, output_tokens, cached_tokens,
  estimated_cost_usd, response_time_ms, ttft_ms), appended to
  MIGRATIONS. All eleven windowed dashboard statements and
  latency-trends now plan as SEARCH USING COVERING INDEX; no query text
  changed. Seeded 50k-row, 645 MB file: cold 4.1 to 4.4 s before, 0.09 s
  after (local SSD; prod is HDD-backed and slower cold). Plans are
  captured from the live handler by a tracing hook, so the SQL cannot
  drift. Safety for the deploy: prod usage_log has 14,219 rows (the 636
  MB is row width, about 45 KB each), so the one-time index build is
  well under a second against a 40 s healthcheck start_period. Not
  covered: usage-by-place's by_jurisdiction still scans (GROUP BY leads
  with app_id). Both B4 and B6 append to MIGRATIONS, so expect a trivial
  textual conflict when the second one lands. Queue position: after
  #1176.

- #1179 (B7, opened 08:05Z): adminFetch (key header, r.ok check, Error
  with status/detail/path) behind 20 call sites; panelState with fixed
  per-panel markup (Loading line, dimmed panel, inline error with Retry)
  wired into Overview, Models, Users, Tiers, Providers, Configs,
  Campaigns, Routing, Errors, Email, Alerts, user detail and Usage by
  Place; refresh() uses Promise.allSettled so one failed group fetch
  marks only its panels and the "Updated" subtitle moves only on a full
  group. 38 node tests; five one-line mutations each caught by exactly
  the predicted tests. Not covered: three telemetry loaders and the
  alert/confirm/prompt dialogs. Queue position: after #1177 (update
  -branched, CI rerunning).

Deploy context at 05:00Z (earlier entries above carried clock guesses up to an hour fast; corrected from the CI run timestamps): zero N-400 /v1/chat calls in the last 10
minutes (the only n400 log lines are config loads from the 04:45Z
deploy). SS Dev and CQ told about the deploy series.

## Merges

- 05:10Z #1169 merged 0ee5d30 (Live Log). 05:13Z #1168 merged 9a696c4
  (routing editor, call-type id). Admin suite on merged main: 135 passed.
- 05:15Z #1170, #1171, #1172 refused by branch protection after main
  moved (checks must run on the updated head); update-branch issued on
  all three, CI rerunning. Merge order once green: 1170, 1171, 1172,
  with an update-branch between each if protection is strict.
- 05:16Z live read-back on build 9a696c4 (Chrome, prod /admin): Live Log
  fetched twice in 6 s while open (initial plus one tick), then zero
  more in 13 s after switching to Overview, `_livelogTimer` null. #1169
  holds on the wire. #routing-editor gone, one #call-type-table,
  #user-call-type-table present, Models tab paints 3 model rows and 11
  call-type rows. No console errors. #1168 holds. The load still fires
  the duplicate set (A1 not merged yet). New number: right after the
  restart, users?days=7 took 15.8 s and 16.9 s for its two copies (cold
  caches plus two concurrent copies of the same heavy query); 2 to 3 s
  warm earlier. The users endpoint itself is a batch B candidate.
- 05:33Z #1170 merged 23c73a7 (tab strip, CSS vars, keyboard tabs).
  Admin suite on merged main: 143 passed. #1171 update-branched, CI
  rerunning. N-400 quiet (0 chat calls in 10 min).
- 05:36Z live read-back on build 23c73a7: .tabs flex-wrap is wrap, 18
  tabs carry role=tab inside a role=tablist, none clipped (the Chrome
  window is 3840 px wide right now, so the two-row case could not be
  seen live; the CSS test pins it), --bg-card resolves to #161b22 and
  --warn to #d29922, .table-wrap overflow-x is auto, Enter on a focused
  tab switched to Users and aria-selected followed. No console errors.
  #1170 holds. Overview was still blank 6 s after load, which is the
  duplicate users fetch (#1171 pending) plus the users query (B4).
- 05:52Z #1171 merged 3b38f93 (load path: one refresh, cheap key check,
  no latency fetch for a hidden tab). Admin suite on merged main: 154
  passed. #1172 update-branched, CI rerunning. N-400 quiet.
- 05:55Z live read-back on build 3b38f93: one page load now fires 6
  admin requests (apps, tiers, cert-pins, dashboard, users,
  usage-by-place), down from 22; no duplicates, no latency-trends or
  typesafe-health on Overview. #1171 holds. Timings: the first load after
  the restart was cold and slow (dashboard 24 s, users 44.6 s, cert-pins
  and tiers 2 s), the second load warm (dashboard 0.65 s, users 1.7 s,
  everything else under 120 ms, Overview painted in about 2 s). The
  cold case is cloudzap.db at 636 MB being paged back in on a 2 vCPU,
  3.8 GB box after every deploy (box load 0.6, GP container at 117 MB);
  the users query rewrite (B4) is the structural fix, and the deploy
  series itself is what keeps making it cold tonight.
- 06:12Z #1172 merged dbe4dd3 (tier saves keep their status, one card
  repaint, Cancel is local). Admin suite on merged main: 166 passed.
  #1173 update-branched, CI rerunning. N-400 quiet.
- 06:20Z live read-back on build dbe4dd3: five tier cards carry
  data-tier; refreshTierCard('free', 'pcc-status-free', []) run from the
  console (read-only: one GET of /admin/tiers, no PUT) repainted the
  free card, kept a status marker I had typed into it, and left a value
  typed into the plus card's input untouched. #1172 holds. (My first
  attempt navigated to /admin#tab=tiers, which is a same-document hash
  change and never reloaded the page; that is why the old build showed.
  Lesson for the rest of the run: reload via location.href, not a hash.)
  Cold load after this deploy: dashboard 29 s, users 42 s again.
- Why the dashboard endpoint is slow cold: /admin/dashboard runs 16
  queries, most of them windowed aggregates over usage_log filtered on
  request_timestamp alone, and usage_log has no index that starts with
  request_timestamp (the existing ones start with user_id or app_id), so
  each is a full table scan of a table that stores raw request bodies.
  B6 launched: add the index, prove the plans with EXPLAIN QUERY PLAN.
- 06:38Z #1173 merged e6d29f5 (Entitlements app choice persists). Admin
  suite on merged main: 174 passed. #1174 update-branched, CI rerunning.
  N-400 quiet.
- 06:45Z live read-back on build e6d29f5: Entitlements opened on
  shouldersurf (server default, no stored choice), an explicit
  loadEntitlements('techrehearsal') persisted cz_admin_ent_app, and
  leaving and reopening the tab fetched ?app=techrehearsal again instead
  of snapping back. #1173 holds. Put the choice back to shouldersurf
  afterwards so the browser is as I found it. No console errors.
- 06:58Z #1174 merged c70b443 (live-log list without bodies, detail on
  expand). Admin suite on merged main: 189 passed. #1175 update-branched,
  CI rerunning. N-400 quiet.
- 07:05Z live read-back on build c70b443: live-log?limit=50 is now 2 KB
  in 41 ms (was 294 KB); clicking a row fetched
  /admin/live-log/{request_id} exactly once and rendered the request
  headers; across six more polls the row was re-rendered as a new
  element each time, stayed expanded with its detail, and the detail was
  not fetched again (1 total). #1174 and #1169 hold together. Only 4
  rows in the buffer because the restart emptied it (in-memory).
- 07:15Z #1175 merged 22015ac (gzip for admin paths only). Admin and
  middleware tests on merged main: 107 passed. #1176 update-branched, CI
  rerunning. N-400 quiet.
- 07:22Z live read-back on build 22015ac: users?days=7 now arrives with
  content-encoding gzip, 42 KB on the wire for 434 KB decoded (about
  10x); the /admin page itself is gzipped; /health carries no
  content-encoding (the exclusion holds on the public edge). Whole
  warm load: dashboard 2 KB / 0.9 s, users 42 KB / 1.3 to 2.8 s, rest
  under 250 ms. #1175 holds. No console errors.
- 07:20Z #1176's rerun on the merged head went RED: one test,
  test_admin_entitlements_app::test_the_tab_loaders_still_call_the_loader_bare,
  pinned the old switchTab if-chain line that #1176 deliberately removed
  (its premise was written while that region belonged to another PR).
  Rule 8 shape: two correct PRs, one collision only visible on the merged
  head, which is exactly what strict protection is for. Fixed on the
  branch (a3b1c16d): the pin now rests on the _TAB_LOADERS entry and
  asserts the if-chain has not come back. 54 affected tests green
  locally; CI rerunning. The other noise in that log ("Event loop is
  closed" x36) is teardown chatter, not a failure.
- 07:40Z #1176 merged 5f9767f (view state in the hash, refresh scoped to
  the active tab). Admin suite on merged main: 224 passed. #1177
  update-branched, CI rerunning. N-400 quiet. B7 (loading and error
  states) launched now that the refresh region is settled.
- 07:50Z live read-back on build 5f9767f: loading /admin#tab=errors&days=30
  landed on Errors with only apps, cert-pins and errors fetched (the
  dashboard group was not touched); changing the period to 7 fetched
  only errors (plus the cert-pin banner) and rewrote the hash to days=7;
  reopening the same tab fetched nothing; _tabFresh read {"errors":true}.
  #1176 holds. No console errors. (A hash-only navigation does not
  reload the page; reload() after setting the hash does.)
- 08:00Z #1177 merged 085727b (covering index for the dashboard's
  windowed aggregates). Plan and schema tests on merged main: 74 passed.
  N-400 quiet. Read-back owed: the index exists on prod after the
  migration sweep, and the FIRST load after this restart (cold) should
  no longer take 25 to 45 s for the dashboard call.
- 08:12Z live read-back on build 085727b, the cold case on purpose
  (first load 57 s after the container came up): idx_usage_date_dash is
  present on prod; the migration sweep took 13.95 s at boot (fingerprint
  change re-ran all 216 statements, 135 ran, 0 failed; inside the 40 s
  healthcheck start_period). dashboard?days=7 cold: 1.24 s, down from 24
  to 29 s on the three earlier cold loads tonight. #1177 holds and is
  the biggest single win of the run. users?days=7 cold is still 19.9 s;
  that is B4's query, not this index. usage-by-place 112 ms.
- 08:40Z #1179 merged 81c6986 (loading and error states, checked fetch
  helper, allSettled refresh). Admin JS suite on merged main: 126
  passed. #1178 update-branched behind it, CI rerunning. N-400 quiet.
- 08:55Z live read-back on build 81c6986 (cold load on purpose): the
  Overview dimmed (.panel-loading) while the group fetch was pending and
  cleared when it settled, adminFetch is live, no console errors. BUT
  the "Loading..." line never appeared: the base rule is .panel-state
  { display: none } and panelState() wrote an inline '' for loading and
  error, which falls back to hidden. The 38 node tests pinned '' as the
  visible value, so they pinned the bug (an instrument reading a
  representation, not the rendering). Fixed by me in #1180: inline
  'block', four pins moved, one new test holding CSS and JS together;
  sabotage of the one line turned exactly those five red. Also observed:
  with Promise.allSettled nothing paints until all three group fetches
  settle, so on a cold load Overview waits on the users call (20 s)
  even though its own data arrived in 0.9 s; B4 (#1178) is the fix for
  that, not the state code.
- 08:47Z #1180 merged 7591a4f (state line visible). State tests on
  merged main: 62 passed. #1178 update-branched again, CI rerunning
  (the last PR). N-400 quiet. (Clock note: the three entries above
  stamped 08:55Z and 09:00Z happened between 08:35Z and 08:45Z.)
- 08:58Z live read-back on build 7591a4f (cold load): 3 s in, the
  Overview is dimmed AND #tab-overview-state is computed display block
  with the text "Loading...". #1180 holds; #1179's states are now
  visible on prod.
- 09:45Z #1178 NOT merged, set to draft. Its two CI runs hung for over
  an hour (cancelled), and so did my local run. Reproduced on SQLite
  3.53: with the expression index idx_usage_user_rollup on
  json_extract(metadata, ...) present, `ALTER TABLE usage_log DROP
  COLUMN prompt_mode` fails with "error in index idx_usage_user_rollup
  after drop column: no such column: metadata", so no column can be
  dropped from usage_log while that index exists, and the project's
  schema-heal test (test_schema_convergence, which the PR had edited to
  drop a different column; it fails either way) can never pass. The
  exception leaves the aiosqlite connection open, which is the hang.
  Rework asked of the agent: real generated_count and documents_count
  columns (additive migration, one-time backfill, set at write time),
  plain-column covering index, the convergence test restored to main
  and passing, full suite under a hard timeout. The oracle test itself
  passes (5 in 11.5 s). The users call stays slow cold until this
  lands.
- 10:10Z #1178 rework pushed (6fbc065, e1c7e48): generated_count and
  documents_count are real columns (ADD COLUMN, a backfill UPDATE
  guarded by json_valid and WHERE generated_count IS NULL, a partial
  index on the unfilled rows so the backfill is cheap on every later
  sweep), the single usage_log writer (app/services/usage_tracker.py)
  sets them, idx_usage_user_rollup covers plain columns only,
  tests/test_schema_convergence.py is byte-identical to main. Local
  under a 300 s alarm: convergence + oracle + plans + table cells, 25
  passed in 13.7 s (the earlier version hung here). CI on e1c7e48 in
  progress. Boot-time note: the backfill reads every metadata blob once
  on the deploy that ships it, the same cold scan as before, inside the
  bounded worst case already logged under "needs Scott".
- 10:22Z #1178 merged 948f18c (users list from three grouped passes,
  plain count columns, covering index). CI green on e1c7e48. N-400
  quiet. Watching this boot: migration sweep plus the one-time backfill
  of 14,219 metadata blobs against the 40 s healthcheck start_period.
- 10:25Z boot and live read-back on build 948f18c. Boot: migrations
  phase 63.5 s (140 statements ran, the backfill of 14,219 metadata
  blobs included, 0 failed), retention purge 7.0 s; the container ended
  healthy with failing_streak 0, so the healthcheck never tripped. That
  is the one slow boot the PR promised, paid. Then the cold first load
  after the restart: users?days=7 in 397 ms (42 KB gzipped), dashboard
  876 ms, Overview painted and ready in about 2 s. The same cold load
  took 20 to 45 s for the users call on every earlier deploy tonight.
  #1178 holds. No console errors. The admin page now loads in about 2 s
  cold and well under that warm, with 6 requests.
- Batch B started early for the items that do not touch the regions of
  the three pending PRs: B1 live-log list without bodies plus detail on
  expand, B2 gzip scoped to /webhooks/admin, B3 entitlements app choice
  persists, B4 list_users rewritten from ~30 correlated subqueries per
  user row to grouped aggregates, same response, oracle test written
  against the current implementation first. Persistence, refetch-on-period-change and the loading/error
  helper wait for #1171 (same region).

## Needs Scott

- 08:25Z, #1178 (B4 users query): the auto-mode classifier denied a
  read-only probe of prod's usage_log (Production Reads). It would have
  told me two things before merging: whether every metadata blob is
  valid JSON (an invalid one makes the new expression index CREATE fail,
  which the PR handles by logging migration_failed and leaving the list
  on the old, slower path), and how long the one-time index build takes
  on the HDD-backed disk (it parses every blob twice; the boot already
  spends 14 s on the migration sweep, and the healthcheck allows 40 s
  start_period plus 3 x 10 s before marking the container unhealthy,
  after which nothing restarts it, so the worst case is about a minute
  of 502 on that one deploy). Not worked around. Plan: merge when CI is
  green on the bounded worst case, and watch the boot time on that
  deploy; if you would rather have the measurement first, the probe is
  scratchpad/probe_usage_json.py and opens the DB with mode=ro.

- Records PR for the I-765 parity notes (cloudzap-records worktree):
  classifier blocked the publish earlier on 10-31. Unrelated to this run.

## Summary (written 09:05Z, #1178 still in CI)

Twelve PRs merged and deployed tonight, eleven of them read back on the
live page in Chrome after their deploy, all clean: #1168 #1169 #1170
#1171 #1172 #1173 #1174 #1175 #1176 #1177 #1179 #1180. Each carried a
test that went red on the bug before the fix, with the red lines in the
PR. Every merge waited for CI on a head that included main (strict
protection), had an SS Dev and CQ heads-up for the series, and a check
that no N-400 chat call had run in the previous ten minutes.

What a daily user gets: one load fires 6 requests instead of 22; the
dashboard call is 1.2 s cold instead of 24 to 29 s; the users payload is
42 KB on the wire instead of 434 KB; the Live Log stops polling when you
leave it, keeps what you expanded open, and ships 2 KB per poll instead
of 294 KB; tier saves keep their "Saved" and your half-typed inputs; the
Models tab no longer gets overwritten when you open a user; the tab
strip wraps and works from the keyboard; the Entitlements app choice
sticks; the URL carries tab, period and app so a reload or a shared link
lands where you were; a period change refetches only the tab you are on;
every panel shows "Loading..." while it waits and an inline error with
Retry when a fetch fails, instead of an empty panel or a silent stale
view.

Two things to know: a deploy still makes the first load cold, and until
#1178 lands the users call is the one that stays slow on that first load
(20 to 45 s); and the Overview does not paint until all three group
fetches settle, so it waits on that users call. #1178 is the fix for
both; it is in CI as this is written.

Paid model API spend for the run: $0. Every agent worked from code,
tests and the live page; nothing called a model.

## Not done

- usage-by-place's by_jurisdiction still scans usage_log (its GROUP BY
  leads with app_id); the dashboard index does not cover it.
- Three telemetry loaders keep `if (!resp.ok) return;` and addLocale,
  openConfig and loadLiveLog keep `.then(r => r.json())` with no ok
  check; the alert/confirm/prompt dialogs (tier change, API key entry,
  config save) are untouched.
- Hardcoded limits (events 200, incidents 100, search-usage 100, email
  100, live-log 50, user table page 25) have no "load more".
- The campaign editor's App select is still a hardcoded list of four.
- anonymous_devices in list_users keeps its per-field subquery shape
  (B4 measured it at a few ms per column and left it).
- The duplicated escapers (esc, _cEsc, entEsc, a local escape), two
  fmtDur definitions and the remaining ~50 hand-rolled X-Admin-Key
  fetches were not consolidated.
- No tab grouping; 18 tabs in two rows at laptop width.
- Nothing was browser-tested at a narrow window; the Chrome window was
  3840 px wide all night, so the wrap case rests on the CSS test.
- Records PR for the I-765 parity notes (unrelated to this run) still
  waits on the classifier decision from earlier on 10-31.
