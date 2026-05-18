# SS handoff back — meeting-report 404 recovery shipped

**Date:** 2026-05-16
**From:** Shoulder Surf (iOS) side
**Subject:** Option A recovery shipped + verified end-to-end + bonus capture-side lease fix
**Replies to:** `ss-report-404-recovery.md` (your 2026-05-16 handoff)

## TL;DR

- Recovery is live in iOS as of today's build.
- End-to-end verified on the meeting that started this (`0A3524D8-E57E-4CB3-A6A9-6F470ACDFD14`) — recovered cleanly, report rendered.
- `X-CZ-Recovery: report-404-replay` header is now in flight on replay captures. Your dashboards should start seeing it.
- **Bonus:** we also fixed what almost certainly caused your "no captures from any user in 24h" observation — see § *Bonus capture-side fix* below.

## What shipped

Five files, ~140 lines, all additive. Wire-side this is purely the recovery; no client-side schema change beyond the new header.

- `CloudZapProvider.captureTranscript` — now returns the HTTP status (was `Void`), and accepts a `recoverySource: String?` param that sets `X-CZ-Recovery` per your guidance.
- `MeetingReportService.errorForStatus` — parses 404 bodies and routes `detail.code == "no_meeting_data"` to a new `.transcriptMissingOnServer` `ReportError` case. Matches on `code` only (not message text) per your handoff.
- `MeetingEnrichmentCoordinator.attemptReportRecovery` — owns the recovery flow. Reads local transcript → applies the `[name] → [name (you)]` owner-marker transformation (mirrors the session-end capture path so CQ extraction stays consistent) → calls `captureTranscript` with `recoverySource: "report-404-replay"` → awaits 2xx → retries the report POST once. One-shot, surfaces failure on a second 404 per your guidance.
- `ShoulderSurfApp.init` — wires `enrichment.ownerSpeakerLabelProvider = { session?.speakerEngine.ownerProfileName }` so the coordinator doesn't take a direct SpeakerEngine dependency.

## Verification

Re-tapped *Generate Report* on `0A3524D8` on a fresh device build. Console timeline:

```
15:51:12.122  Report request: meeting=0A3524D8 ...
15:51:12.229  Report FAILED: HTTP 404 ... no_meeting_data
15:51:12.229  Report 404 (no_meeting_data) — attempting transcript replay recovery
15:51:12.402  CQ transcript capture → 200
15:51:12.441  Recovery: transcript replay OK — retrying report
15:51:12.448  Report request: meeting=0A3524D8 ...  (retry)
15:51:43.395  Report OK: model=unknown generation=30633ms cost=$0.0575 html=19828 chars
15:51:44.313  Recovery: report retry succeeded
```

- **Recovery overhead:** ~325ms wall-clock (12.122 → 12.448) between the original 404 and the retry POST. Well under noise relative to the 30.6s LLM generation.
- **Capture replay status:** 200 OK first try.
- **Report retry status:** 200 OK first try (no second-404 fallback exercised).
- **Report payload:** 19,828 chars HTML, $0.0575 — looks correct.

The 200 capture at 15:51:12.402 should carry `X-CZ-Recovery: report-404-replay`. Please verify it landed on your end and confirm the dashboard tag works.

## Bonus capture-side fix (likely root cause of your fleet-wide observation)

While tracing the failed meeting, we found that `SessionManager.swift`'s session-end `capture-transcript` Task had **no `beginBackgroundTask` lease**. The Task fires AFTER `isSessionActive = false`, so the existing autosave lease (which guards on `isSessionActive`) doesn't apply. When a user taps Stop and immediately backgrounds the app — typical post-meeting behavior — iOS suspends the process while the HTTPS POST is in flight. Silent loss, no error log, no `CQ transcript capture →` line ever fires.

We added a separate `captureTranscriptBgTaskID` lease that wraps the capture Task and ends in both the success path and the iOS expiration handler. Pattern mirrors the existing autosave lease (`SessionManager.swift:2579`).

This means:
- Your observation that *"no `/v1/capture-transcript` POST appears in GP's logs from any user in the last 24h"* should resolve itself as users update.
- The Option A recovery now functions as a safety net rather than the primary code path. Long-meeting POSTs will more reliably land on first try.

You should see capture volume climb back to expected levels over the next few days as builds propagate.

## What we did NOT do

Per your "forward-looking notes":

- **Offline queue for the original capture-transcript** — deferred. The background-task lease is the high-leverage half of that fix; a true persistent offline queue would only help when the user *never* foregrounds again before the lease expires (rare). We'll revisit if metrics show residual loss after the lease ships.

## Anything you need from us

Drop a note in this doc (or ping back) if:
- The `X-CZ-Recovery` header isn't landing as expected
- Your fleet capture-volume doesn't recover within ~7 days post-TestFlight
- You want us to add additional `recoverySource` values for future recovery paths

Otherwise we're closing this out from the iOS side.

— SS
