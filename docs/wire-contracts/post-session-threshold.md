# Post-session threshold: which artifact a finished meeting gets

`GET /v1/config/client-config` → `post_session.report_min_seconds`

```json
"post_session": {
  "report_min_seconds": 300
}
```

## What it means

A finished meeting whose **captured duration in seconds is greater than or
equal to this value** takes the report path. Below it, the meeting takes the
lighter post-session analysis path. The two are mutually exclusive by design:
the report already supplies sentiment, urgency, title and tags, so running
analysis alongside it would be a redundant paid call.

The number is compared against the same duration the client already reports
to us as `duration_seconds` on the `meeting_stop` telemetry ping. That is
deliberate: the value the decision is made on is the value we can see, so the
rule is verifiable from our side rather than only assertable from the app's.

## Why it moved to the server

It was a client constant (`SessionViewV2`, `skipPostSessionAnalysis`). Live
2026-07-31, two demo meetings an hour apart:

| meeting | captured | path taken |
|---|---|---|
| `608F2BF4` | **298s** | analysis, no report |
| `6212B679` | 419s | report, no analysis |

The first missed the report by **two seconds**, and the user reading the
result had no way to know why one meeting produced a report and the other
did not. Tuning a cliff like that should not require an App Store release,
which is the whole reason config lives here.

## Client contract

- Read it on the same config fetch the app already makes; fall back to the
  bundled value when the key is absent (add-only decoder rules apply).
- A value of `0` means every finished meeting takes the report path.
- Changing this value is a hot config change on our side. Expect it to move.

## Open, not decided here

Whether a meeting below the threshold should say so in the UI, and whether
short meetings should get a report anyway rather than the lighter analysis.
Both are product decisions; this contract only relocates the number.
