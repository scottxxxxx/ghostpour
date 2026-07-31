# Client constants we now serve

`GET /v1/config/client-config`

SS offered these in response to the standing direction that anything tunable
in the app should be read from our config rather than compiled in (2026-07-31).
Every value below **matches their existing constant exactly**, so reading it is
a no-op flip rather than a behavior change. Add-only, so older builds ignore
the keys and nothing breaks while they wire them up.

The reason this matters is not theoretical. SS's report threshold was a client
constant at 300 seconds; a demo meeting captured 298 and silently produced
nothing, and neither side could move the number without an App Store release.

## post_session

```json
"post_session": {
  "report_min_seconds": 300,
  "allow_request_below_minimum": true,
  "request_min_seconds": 30,
  "analysis_min_seconds": 120,
  "analysis_min_words": 300
}
```

The first three govern reports; see `post-session-threshold.md`.

`analysis_min_seconds` (was `MeetingAnalysisEngine.minimumDurationSeconds`,
client-side and only overridable via UserDefaults) is the analysis twin of
`report_min_seconds`: below it the automatic analysis path does not run.

`analysis_min_words` (was `MeetingEnrichmentCoordinator.minimumTranscriptWords`)
is a floor on transcript length for the automatic analysis path. Worth
recording how this one surfaced: the comment above it claimed "GP spec" for
months and **nothing on our side ever enforced or expected it**. It also gated
reports until 2026-07-31, so a meeting under 300 words silently produced
neither artifact regardless of duration. It applies to the automatic path only
and never to a report a user explicitly asks for.

Neither analysis value is enforced server-side. We serve the policy; the app
decides whether to start the call. The one exception in this whole family is
`request_min_seconds`, which we do enforce, because it is the one that spends
a user's allocation.

## enrichment

```json
"enrichment": {
  "max_auto_attempts": 5,
  "retry_interval_seconds": 86400,
  "in_flight_stale_seconds": 300,
  "foreground_sweep_recovery_cap": 5
}
```

The automatic retry policy for post-meeting enrichment: how many times it
retries on its own, how long it waits between attempts, when an in-flight
attempt is considered stale and recoverable, and how many stalled meetings one
foreground sweep will pick up.

These are the values most likely to need tuning against real failure rates,
which is exactly why they should not need a release to change.

## session

```json
"session": {
  "resumable_window_seconds": 3600,
  "min_post_processing_seconds": 120
}
```

`resumable_window_seconds` (was `SessionManager.resumableSessionWindow`): how
long after a session ends it can still be resumed.
`min_post_processing_seconds` (was `minimumPostProcessingDuration`).

## Images: already served, and the client disagrees with us

SS listed chat image wire values (1024px long edge, JPEG 0.7) as constants to
move. **We already serve these**, per tier, in `GET /v1/tiers` under
`tiers.<tier>.feature_definitions.images`:

```json
"images": { "max_long_edge": 1568, "jpeg_quality": 0.8 }
```

So there is nothing to add, but there is something to fix: the served values
are 1568 and 0.8, the client constants are 1024 and 0.7. Images sent today are
smaller and lossier than we intend. Either that path never read the served
values or it reads them somewhere else and this constant shadows them.

## Deliberately not taken

Anything in `EnrollmentVAD` or `SpeakerEngine`. SS's reasoning is right and we
agree: a bad served value would silently wreck diarization with no way to
correlate the damage back to a config change. The golden audio pipeline stays
compiled in.
