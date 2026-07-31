# Post-session threshold: which artifact a finished meeting gets

`GET /v1/config/client-config` → `post_session.report_min_seconds`

```json
"post_session": {
  "report_min_seconds": 300,
  "allow_request_below_minimum": true,
  "request_min_seconds": 30
}
```

Three bands, by captured duration:

| duration | what happens |
|---|---|
| `>= report_min_seconds` (300s) | report generated automatically |
| `request_min_seconds` to `report_min_seconds` (30s to 300s) | no automatic report; the user may ask for one, when `allow_request_below_minimum` is true |
| `< request_min_seconds` (30s) | no report, and no offer to make one |

## What it means

`report_min_seconds` governs the **automatic** path only. A finished meeting
whose captured duration in seconds is greater than or equal to this value
gets a report without being asked; below it, the meeting gets the lighter
post-session analysis instead. The two automatic paths are mutually exclusive
by design: the report already supplies sentiment, urgency, title and tags, so
running analysis alongside it would be a redundant paid call.

`allow_request_below_minimum` answers the question the threshold creates:
when a meeting falls SHORT of `report_min_seconds` and therefore gets no
report automatically, may the user still ask for one? True says yes. Scott's
call, 2026-07-31, after a demo meeting missed the automatic threshold by two
seconds and the person had no way to get the artifact they expected.

`request_min_seconds` is the floor under the whole thing: below it a session
is a mis-tap, not a meeting, and offering to build a report from it would be
offering nonsense.

**30 seconds, chosen from the data rather than picked.** Across 106 completed
meetings, 42% ran under the 300 second automatic threshold, so the on-demand
path carries real weight and the floor should not eat into it. A 30 second
floor excludes 10 sessions, every one of them 29 seconds or shorter, and the
three shortest in the whole fleet are 9, 10 and 11 seconds. A 60 second floor
would have excluded 24, which starts taking real short huddles with it.

These three keys are one decision in three parts, and all of them are ours:
what earns a report without being asked, whether falling under that bar puts
the report out of reach, and where a session stops being a meeting at all.
None should become a client constant again. Check with us before changing any
of them, or before changing the behavior around them.

The server has never gated reports on duration and does not now: the route
requires a tier, quota, and a stored transcript or summary, and that is all.
Verified live 2026-07-31 by generating a report for `608F2BF4`, the
298-second meeting the automatic rule had skipped: HTTP 200 in 33.7s, a real
19.9KB report, not the canned fallback.

So the flag is about what the app OFFERS, not about what the server permits.
Setting it false hides the affordance on short meetings; it does not close an
endpoint, and it is not an authorization check.

### Consequences worth knowing

- A meeting can end up with **both** an analysis and a report, since the
  analysis already ran automatically before the user asked. That is a second
  paid call, roughly $0.03 on a Pro account. The report supersedes the
  analysis for display purposes: everything the analysis gives, the report
  gives too.
- On-demand is bounded by transcript retention, not by duration. Stored
  transcripts purge at 30 days, after which the app must re-send the
  transcript via `capture-transcript` before asking for the report. The
  route answers 404 `no_meeting_data` when there is nothing to work from.
- An exhausted allocation returns the canned report, exactly as it does on
  the automatic path. On-demand does not bypass the budget gate.

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

Whether a below-threshold meeting should say anything about why it did not get
a report automatically. With `allow_request_below_minimum` the artifact is never
unavailable, only unrequested, so the honest surface is an action the user can
take rather than a notice about what they did not get.
