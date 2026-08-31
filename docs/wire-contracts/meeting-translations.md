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
  "artifact": "transcript",       // transcript | summary | report
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
schema is GP's); enum values (stoplight, sentiment category, priority,
severity, mood) never leave the device and stay wire-English.

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
