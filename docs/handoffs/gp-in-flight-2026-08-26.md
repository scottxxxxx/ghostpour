# GhostPour: what is still in flight (written 2026-08-26 by cloudzap-d1)

Prod and main are both at e2be54b (healthy). Everything from #780 through
#805 is merged, deployed and wire-verified. This file lists what is NOT
finished, who holds it, and what it is waiting on. Nothing below is
half-applied in the repo: there are no open GP PRs except #736, the old
routing eval harness.

## GP work still owned by a live session

- Share-page language follow-ups (cloudzap-c6 owns the share lane:
  app/routers/shares.py, share_bundle.py, share_card.py, client-config
  share block). #804 shipped the Original-toggle fix (h1, chrome, date
  line swap back). Flagged, not built, Scott's call: the grey meta line
  and date do not re-translate on toggle beyond what #804 did; the
  sentiment word on translated cards stays English (comes from the
  report's sentiment field, which the app generates in English); the
  card insight line is blank when a summary is only a bare heading.
- Nothing else is in a GP session's hands. cloudzap-54 and cloudzap-d1
  are idle. Four GP sessions were alive at once on 08-25; on resume,
  run ListAgents and announce before editing anything.

## Waiting on Scott

1. Test-account revocation (asked twice by SS): delete ddc3df33's
   refresh_tokens. My SSH DB writes are classifier-blocked. Run on the VM:
   sudo docker exec ghostpour python3 -c "import sqlite3;c=sqlite3.connect('/app/data/cloudzap.db');print('before',c.execute(\"SELECT COUNT(*) FROM refresh_tokens WHERE user_id LIKE 'ddc3df33%'\").fetchone()[0]);c.execute(\"DELETE FROM refresh_tokens WHERE user_id LIKE 'ddc3df33%'\");c.commit();print('after',c.execute(\"SELECT COUNT(*) FROM refresh_tokens WHERE user_id LIKE 'ddc3df33%'\").fetchone()[0])"
   Signs out every device on the test account; access JWTs survive up
   to 60 minutes. Follow-up worth a PR: an admin revoke endpoint so the
   next rotation is an ops call, not raw SQL.
2. English-to-Spanish re-translate retest on the clean 40-minute
   Spanish meeting, then a fresh share. The 08-25 re-translate was a
   Spanish meeting into French (clean on the wire, ids echoed exactly,
   zero 502s); it does not prove the "real Spanish" case. SS's client
   now refuses to store a rendition byte-identical to its source.
3. Research-notes cap: the meeting report keeps only the first paragraph
   of each in-meeting query answer, hard-capped at 300 characters, before
   the model summarizes it (app/services/meeting_report.py,
   _extract_first_paragraph). Keep or raise: one-line change.
4. enforce_admins is OFF on branch protection; as admin, gh pr merge
   succeeds on a failed required check. Habit in force: merge only on a
   completed-success run for the current head sha.
5. Release archive 1.16 (1212) upload: SS's App Store hand-off and the
   Mac badge resolve when it goes live.
6. iPad universal links on a dev-signed build need Settings > Developer
   > Associated Domains Development ON; the archive GET on our wire is
   the signal an import began.
7. Small rulings offered, not made: es Pro card says "el modelo mas
   potente" (superlative) where English deliberately says "the stronger
   model"; the 58 untranslated Japanese llm-providers strings; the
   translated card's English sentiment word.

## Waiting on other teams (nothing blocks GP)

- SS: welcome_back pill templates still unconsumed on their side; the
  two-device iCloud sync verification (also proves transcriptLanguage
  survives sync); first real speaker_identities capture; paywall
  render-served flip is done (849236f) but bundled strings remain as the
  offline floor by design; devices were held at a76eff7 on 08-24 with
  the stamp/rendition fixes (f318ab0, 7f0af90) pushed but not installed,
  since superseded by 1257 runs, confirm current device build with SS.
- Social: the ledger card's footer CTA words. Served in
  client-config.share.card_cta_text / card_pill_text; the placeholder
  copy renders until they arrive.
- CQ: nothing. Their #325 (excluded block) and #328 (Free people-lane
  fix) are deployed and proved through the proxied path; both memory
  nudges are live. CQ's decision list for the lost-phone mandate had one
  empirical row left: unauthorize-and-re-authorize with a test Apple ID
  to confirm the same user_id returns.
- Bifrost: nothing. Edge allowlist on share.shouldersurf.com:
  /s/{token}, /archive, /audio/[0-9]{1,3}, /card.png, /image/[0-9]{1,3},
  AASA, /share-assets/*.png. /transcript was removed with #793.

## Config that is live and worth knowing on resume

- client-config.share.dynamic_card = true (per-share og:image card).
- client-config.translations.group_size = 12 (all four locales).
- entitlements.matrix.context_quilt.free = teaser (Free nudge live).
- tiers free card carries a Meeting Memory row in the teaser state.
- Value changes need sync-from-bundle from the VM after deploy;
  additions auto-hydrate. There is no watchtower on the VM: CI builds
  and pushes :latest, then a poll deploys; to force, pull and
  `docker compose up -d ghostpour` in /opt/ghostpour.
- Card renders are cached as `<archive>.card.png` sidecars in a
  root-owned dir on the VM; after a renderer change, delete them as
  root or the old card keeps serving.

## Scratchpad worktrees

`git worktree list` shows wt-card2, wt-globallang, wt-h1, wt-md,
wt-report under the session scratchpad, all for branches already merged.
Safe to `git worktree remove` once no session is sitting in them.
