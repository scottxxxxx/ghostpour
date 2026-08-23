# Recording start: `metadata.recording_started_at`

Added 2026-08-22. Until this key existed, the capture body GP POSTs to
CQ's `/v1/memory` carried **no timestamp of any kind**. That is why no
end of this lane has ever known when a meeting happened: CQ receives a
timestamp only in the sense of its own receive clock, spends it resolving
relative deadlines, and drops it, so every timestamp CQ serves is the
clock of when their importer ran. ShoulderSurf's `MeetingStore` is
currently the only place in the entire system where a real meeting date
exists.

Found by grepping GP for a bug shape CQ flagged: GP's project memory
dossier was heading each meeting with a bare date taken from
`patch.created_at`, which a model reads as the day the meeting happened
(fixed in #757). Live capture runs minutes after a meeting, so the stamp
was right by accident and wrong exactly on a backfill or a delayed send,
where every meeting collapses onto the importer's date.

## The field

`metadata.recording_started_at`: ISO 8601 **with a real UTC offset**,
e.g. `2026-08-21T23:30:00-07:00`. Sent by SS inside the `metadata` object
on `POST /v1/capture-transcript`. GP forwards it verbatim into the
`metadata` object of the CQ capture body. Optional; absent means unknown.

Named for what it IS. SS does not hold a meeting start: `MeetingRecord.date`
is when the **recording** began, after however long someone spent opening
the app and finding the button. SS sometimes holds a calendar event start,
but only when the prep matcher made a confident match, and putting that
into this field when it happens to exist would make one field mean two
different things depending on whether a match happened. If the calendar
start is ever wanted, it is a second, separately nullable field with its
own honest name.

Related and misnamed: `ReportRequest.meeting_start_iso` is fed from the
same `MeetingRecord.date` and is therefore also a recording start. The
precise name and the sloppy one coexist. They are the same quantity.

## The offset is load bearing

Not a formatting preference. A UTC normalisation names the same INSTANT
and a different **DAY** for anything near midnight, and a day is the only
thing a date on a meeting exists to say. `23:30-07:00` is `06:30Z` the
next day.

SS shipped exactly this bug on the report path: two of their call sites
disagreed about the format, one sending a local offset and one a bare
`ISO8601DateFormatter()`, which defaults to UTC. Both valid, both naming
the same instant, disagreeing about the date on the header. It survived
for an unknown length of time because it can only appear near midnight,
never in a fixture written at a round hour in the middle of the day.

So: send the offset form, and send the zone **as it was at the meeting**,
not as it is now, which differ across a DST boundary.

GP does not parse, reformat or re-emit this value. It is forwarded as the
string SS sent, character for character, and that is pinned by test.

## Absent vs null vs a guess

Absent and null are one state on this hop: the capture allowlist drops
`None`, so an explicit null arrives at CQ as absent. Both mean unknown.

GP does **not** fill this in when the client omits it. A fabricated
recording time is indistinguishable at CQ from a real one, which makes it
worse than the gap it fills: absent is a state that can be fixed later,
a guess is one nobody can detect. CQ's migration treats absent as unknown
and does not backfill, because the source is gone from the queue.

## Why the test for this is request-side

`/v1/capture-transcript` is a POST, so this is the case rule 3 actually
asks for. An unmodelled or unlisted field is dropped **silently**: SS
would see a correct send, CQ would see a complete request that merely
lacks a date, and neither endpoint would hold evidence that anything was
wrong. That is `to_name` exactly. The gate is
`CAPTURE_METADATA_ALLOWLIST` in `app/services/context_quilt.py`, and the
receipts are in `tests/test_capture_transcript_request_passthrough.py`.


---

## Also on this carrier: `metadata.speaker_identities` (CQ #318, 2026-08-23)

Same hop, same allowlist, same reason for a request-side test.

Scott's ruling: the "which Christina?" question is asked at LIVE label
time, and the only hop that can ask it is the client, from its cached
roster, while someone is still in the room to answer. The answer rides the
capture body.

```json
"speaker_identities": [
  {"label": "christina", "entity_id": "<uuid>"},
  {"label": "Speaker 2", "create_new": true, "name": "Christina Lopez"}
]
```

A list of objects. `label` always present; exactly one of `entity_id` or
(`create_new` + `name`) per entry. GP forwards it verbatim into CQ's
capture metadata and models none of it: CQ owns the shape, and a schema
here would be a second place to update and a new way to silently drop a
field they add later.

CQ's ingest (their main `3dcd5d6`, contract
`docs/architecture/16-people.md` 5.17) reads the map and rewrites
`[label]` to the canonical name **before extraction runs**. That is why a
dropped entry is worse than a dropped name: it does not degrade the
result, it silently reverts a user's explicit answer to guesswork, and the
output still reads as a plausible match.

Absent and empty are the same state on CQ's side, so the allowlist's
None-drop costs nothing and an empty array crossing is harmless. GP never
invents a map: a fabricated identity assignment is indistinguishable at CQ
from a real answer and would rewrite a name during extraction.

Sender: SS, from `MeetingRecord.speakerIdentities`, on the session-end
capture, both enrichment replays, and the replay driver.
