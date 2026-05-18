# SS handoff — meeting-report 404 recovery

**Date:** 2026-05-16
**From:** GhostPour (server) side
**Subject:** Validating SS's proposed on-404 transcript-replay recovery + clarifying GP retention semantics

## Why this doc

While SS was investigating a meeting report failing to generate (meeting
`0A3524D8-E57E-4CB3-A6A9-6F470ACDFD14`, 64 min, two attendees), the SS
engineer sketched an Option A recovery: on `404 + detail.code == "no_meeting_data"`,
re-send `capture-transcript` from the local `MeetingRecord.transcript`
and retry the report POST. Before SS commits to that design, this doc
confirms the GP-side facts the design depends on so we don't ship a
client behavior that disagrees with the server.

## Confirmed GP facts (current code, 2026-05-16)

Each claim is annotated with the GP source so SS reviewers can verify
independently.

### 1. Report POST body is metadata-only

`POST /v1/meetings/{meeting_id}/report` accepts:

| Field | Type |
|---|---|
| `duration_seconds` | int |
| `project` | str? |
| `attendees` | list[str]? |
| `tag_taxonomy` | list[str]? |
| `meeting_start_iso` | str? |
| `timezone_abbr` | str? |

**There is no `transcript` field.** GP fetches the transcript from its
own `meeting_transcripts` table via `gather_meeting_data` —
`SELECT transcript, project FROM meeting_transcripts WHERE meeting_id = ? AND user_id = ?`.

> Source: `app/routers/reports.py:36-42` (request model),
> `app/services/meeting_report.py:203` (transcript lookup).

This is by design — the wire contract has always been "capture writes
the transcript, report reads it." Don't try to push the transcript in
the report body; the server will ignore it and the 404 will still fire.

### 2. The 404 has a structured `detail` body

```json
{
  "code": "no_meeting_data",
  "message": "No transcript or summary found for meeting <id>. Ensure capture-transcript was called with this meeting_id."
}
```

Match on `detail.code == "no_meeting_data"` to drive recovery. Generic
404 (route not found, etc.) won't carry this shape.

> Source: `app/routers/reports.py:99-107`.

### 3. Transcript retention — answering the explicit clarifying question

> *"Does GP actually retain capture-transcript data indefinitely, or
> is there a server-side TTL on the transcript itself?"*

**No TTL on `meeting_transcripts` today. They are kept indefinitely.**

We grepped `app/` and `scripts/` — there is no `DELETE FROM meeting_transcripts`
anywhere. The only retention statement is for **reports**:

```sql
DELETE FROM meeting_reports WHERE created_at < datetime('now', '-30 days')
```

…run on startup. That's the 30-day cache `MeetingReportService:150`
already documents.

> Source: `app/database.py:216` (the report purge);
> `app/routers/reports.py:7` docstring: *"SS should persist the report
> locally once received — GP is not long-term storage."* — note this
> statement is about **reports**, not transcripts.

**What this means for the "two-weeks-later" scenario:** A meeting
captured today and retried in two weeks will succeed — the transcript
row is still there. A meeting whose `capture-transcript` *never landed
on GP* will 404 forever until SS re-sends. Both cases are handled by the
same recovery path.

### 4. Replay is idempotent-friendly

`capture-transcript` writes via `INSERT OR REPLACE INTO meeting_transcripts`
keyed on a fresh UUID primary key, so re-sending always creates a new
row (it does not overwrite by `meeting_id`). `gather_meeting_data` reads
`ORDER BY created_at DESC LIMIT 1`, so the latest send wins. SS can
re-send freely without coordination — last-write-wins, no client-side
dedup required.

> Source: `app/routers/cq_proxy.py:142` (insert),
> `app/services/meeting_report.py:203` (read).

## GP's read of Option A

We endorse the recovery design. It is net additive, requires no GP
schema changes, and self-heals every observed root cause class:

- Network failure mid-session (capture POST silently dropped)
- Auth race at session-end (JWT not in Keychain when capture fires)
- Meeting-ID drift (capture wrote under a different `meeting_id` than
  the report later asks for — SS can replay under the *current* id)
- GP retention TTL expiry — not a concern today, but the design
  remains correct if we ever add one

The 80-line scope SS sketched is in line with our estimate.

## Implementation notes for SS

A few specifics that came up while validating:

1. **Match on `detail.code`, not the message string.** The message text
   is not part of the contract — we may relocalize it later. The `code`
   is stable.

2. **Re-send the *full* transcript on replay.** GP does not support
   incremental/append. `capture-transcript` is one-shot per row;
   gather_meeting_data picks the latest row.

3. **Preserve speaker-identification metadata if present.** The replay
   should rebuild `metadata.user_label` and `metadata.identification_source`
   from local diarization so CQ extraction stays consistent (these are
   already forwarded to CQ per PR #75 / `app/routers/cq_proxy.py`).

4. **Don't retry the *generate* call before the replay completes.**
   Capture-transcript is currently fire-and-forget on the GP side too —
   we return as soon as the SQLite write commits, before CQ extraction
   finishes. SS should await GP's 200 from `capture-transcript`, *then*
   call `/meetings/{id}/report`. Empirically the write is sub-100ms.

5. **One-shot, not a loop.** If the immediate retry also 404s, surface
   the failure — don't loop. A persistent 404 after a fresh capture
   indicates either an auth/user-id mismatch (capture wrote under a
   different `user_id` than the report query reads) or a `meeting_id`
   normalization difference. Those need diagnosis, not retries.

6. **UI copy.** The progress UI SS proposed ("Uploading transcript…"
   then "Generating report…") is exactly the right level of disclosure.
   Generic spinner hides a meaningful state transition.

## Forward-looking notes (not blocking)

- **Transcript retention is not a contract.** Today's behavior
  (unbounded) is current state. The docstring positions GP as
  not-long-term-storage; if volume forces a `meeting_transcripts` TTL
  later, we'll signal SS ahead of time. The Option A recovery still
  works in that world — it shifts from belt-and-suspenders to
  load-bearing.

- **GP would also benefit from a server-side observability tag** for
  this recovery path so we can measure how often it fires (and per
  cause). Suggestion: SS includes a header like
  `X-CZ-Recovery: report-404-replay` on the replayed capture. We'll
  surface it in dashboards. Non-blocking.

- **The deeper fix on the SS side** (offline queue + retry for the
  original capture-transcript) is still worth doing eventually, but
  Option A makes it lower-priority — the user-visible symptom is gone
  either way.

## The single meeting that started this

`0A3524D8-E57E-4CB3-A6A9-6F470ACDFD14` (user `fa4d903c-24c0-45d5-9fdb-b5496e32501b`)
is unrecoverable from GP today — there is no transcript row anywhere
for that ID. The Option A recovery would have caught it. Once SS ships
that path, re-tap "Generate Report" on this meeting from the device
that holds the local transcript and it'll succeed.

Separately, no `/v1/capture-transcript` POST appears in GP's logs from
**any user** in the last 24h, which suggests the regression may be
broader than this one meeting. Worth a quick sanity check that the
capture path is firing in current builds before assuming Option A is
sufficient.

---

Questions? Ping the GP side and we'll dig in.
