# Meeting translations: `POST /v1/translations`

Drafted 2026-08-23. One engine, two client uses: the post-meeting toggle
(view summary / transcript / report in another language, originals
preserved) and the share-import offer (bundle language differs from the
device locale). Never the audio. GP owns the engine and the prompt
recipe; the client owns storage, the toggle, and the import offer.
Status: contract published for client build; the endpoint ships behind
this document. Tier gating is Scott's ruling and arrives as served
config, not as a client change.

## Request

One POST per SEGMENT GROUP, client-chunked. A 1h transcript on flaky
wifi WILL be interrupted, so the unit of retry is the group, not the
meeting. Group size comes from served config
(`client-config.translations.group_size`, hydrated like every dial);
the client never hardcodes it.

```json
POST /v1/translations
{
  "source_language": "es",        // REQUIRED. BCP-47, from MeetingRecord.transcriptLanguage.
                                   // We never detect. nil-language meetings never offer.
  "target_language": "en",        // REQUIRED. BCP-47.
  "artifact": "transcript",       // transcript | summary | report | title
  "segments": [                    // 1..group_size items
    {"id": "seg-0041", "text": "Bueno, retomando lo de la semana pasada..."}
  ]
}
```

`id` is an opaque client key. Speaker labels, timestamps, and any other
segment structure NEVER ride this wire: the client sends only `{id,
text}` and reassembles by id, so the `[Label]` structure cannot be
corrupted by construction. For `report`, the client sends the narrative
fields it wants translated as segments (it knows the field names; the
schema is GP's); enum values never leave the device and stay
wire-English.

**Which report fields are enums, stated exactly, because the report JSON
has two different fields named `category` with opposite rules** (2026-08-31,
after the STATUS UPDATE label stayed English through a Spanish
translation). Wire-English enums: `stoplight.color`,
`sentiment.category`, `sentiment.category_also[].category`,
`*.priority`, `*.severity`, `*.mood`. NARRATIVE and translatable like any
other prose field: `header.title`, `header.category` (the short type
label, our own schema's examples are 'Technical Working Session',
'Sprint Planning', 'Status Update'), `stoplight.label`,
`stoplight.detail`, `sentiment.label`, `sentiment.detail`. An earlier
draft of this paragraph said only "sentiment category", which reads as
"anything called category" to someone scanning the schema. Confirmed
with SS the same day: `MeetingCardView.swift:104` renders
`decodedReport?.header.category`, so that free-text label IS the
"STATUS UPDATE" text a user sees on the card, and it was translatable
through `report` the whole time. Same family as `total_available`
meaning opposite things on two routes: one name, two rules, and the
reader cannot see the collision from either side alone.

## Response

```json
{
  "segments": [{"id": "seg-0041", "text": "Well, picking up from last week..."}],
  "source_language": "es",
  "target_language": "en",
  "engine_version": 1,             // GP prompt dossier version. Store it on the rendition.
  "cached": false                  // true: served from the GP-side cache, zero model tokens
}
```

Same ids, same order, every id exactly once. The engine uses structured
output; fenced or prose-wrapped JSON never exists on this wire, and the
client MUST NOT strip fences (there are none to strip; stripping is how
a real bracket in translated text gets eaten).

## The `title` artifact (added 2026-08-31)

The meeting card headline. It is NOT `header.title` from the report:
that one is the report's own headline and rides the `report` artifact
like every other narrative field. This one is the title GP serves as
`suggested_title` on the summary response (`app/services/meeting_title.py`),
which the client stores on the meeting record and renders on the card.

It was missing from the artifact list until 2026-08-31, so a translated
meeting kept its English headline forever while transcript, summary and
report all swapped. That is worse than translating nothing: **a control
that works invisibly is indistinguishable from one that does not work**,
and three of four visible fields swapping reads as broken where zero
reads as not implemented. Scott concluded the button was broken.

- One segment per title; `id` is opaque like everywhere else.
- The prompt is its own branch, not the prose one. A title is a NAME:
  no invented article, no trailing clause, no turning it into a
  sentence, and never MORE GENERIC than the title it was given, since a
  title that no longer says which meeting this was has failed even when
  it reads well. It carries the no-dash rule.
- Budget is 60 characters, read from `meeting_title.MAX_TITLE_CHARS` at
  call time so the two cannot drift.
- **Over budget is NOT an error.** GP logs a count and returns the title
  whole. Truncate for display if you need to. Rejecting would drop the
  client back to the English title on a Spanish card, which is the exact
  defect this artifact exists to remove, so the asymmetry with
  generation is deliberate: `meeting_title.py` refuses a bad GENERATED
  title because absent beats generic, and neither half of that reasoning
  survives when we are carrying an already-accepted name across.

## Semantics

- **Idempotent by content.** The GP cache key is
  sha256(canonical segment-group JSON) + source + target +
  engine_version. Retrying a failed group re-serves the cached result
  if it completed, or re-runs one group (~$0.015) if it did not. Five
  recipients translating the same shared meeting cost one translation.
- **Renditions are immutable client-side.** The client stores what this
  returns as a named rendition {lang, engine_version, created_at}; the
  original is never mutated. A future engine_version may translate
  differently; stored renditions do not silently change.
- **Retention.** GP-cached TRANSCRIPT translations inherit transcript
  retention (30-day purge, retroactive): the cache never outlives its
  source. Summary and report translations follow report retention.
- **Faithfulness split.** Summary and report translation prompts carry
  the served no-dash rule like every GP prompt; transcript translation
  is EXCLUDED from it (like transcriptCleanup): the user's own words
  keep their punctuation.
- **Names.** Personal, company, and product names are never translated.
  This is a prompt rule for inline mentions; labels are structural and
  never sent at all (above).
- **Managed-only at launch.** BYOK traffic is excluded in v1. The
  client sends `model: "auto"` semantics as everywhere else: no model
  choice exists on this wire.
- **Errors.** Non-2xx leaves the group untranslated; the client retries
  the group. Absent/malformed `source_language` is a 422, not a guess.

## Measured cost (2026-08-23, real engine run, synthetic length-matched 1h es transcript)

148 segments / 9,555 words / 61 min at 156 wpm, chunked 25 segments per
group: 6 transcript groups + summary + 9 report narrative fields =
8 calls, 20,534 tokens in, 15,832 out, 100 s sequential. First run
$0.0997 per meeting per target language ($0.015 per group = the retry
price); cache hit $0.00 and zero model tokens. Real-transcript
confirmation pending a data-access ruling; movement expected within a
few percent, since cost is token mass.
