# Meeting report: the `sentiment` block

Served by `POST /v1/reports` (ShoulderSurf, call type `report`) inside the
report JSON. This page is the contract for the block; the client's decoder
is checked against it, not against a sample payload.

```json
"sentiment": {
  "score": 0,
  "label": "string",
  "detail": "string",
  "category": "positive | collaborative | informational | cautious | pressured | tense | disconnected | decisive",
  "category_evidence": "string",
  "category_also": [
    { "category": "decisive | disconnected", "evidence": "string" }
  ],
  "emoji_label": "string (derived)",
  "emoji": "string (derived)",
  "arc": [ { "value": 0, "mood": "confident | tense | concern | neutral" } ],
  "arc_narrative": "string"
}
```

## `category` (one value, the meeting's DOMINANT character)

Exactly one of the eight values, chosen by the model. The served prompt's
governing sentence is: "the category names the DOMINANT character of the
whole meeting, never its worst or best moment." `informational` is the
explicit default every other value must earn its way past with something
quotable. There is no precedence ladder between values beyond the contrast
rules in the prompt (deadline talk is pressured never tense; a blocker
without blame is cautious or pressured not tense; frustration at someone
outside the room is pressured or cautious not tense; repeated confusion is
disconnected not cautious). When two characters both apply, the model
names the dominant one; the other does not travel in this field.

## `category_evidence`

Required non-empty verbatim quote when `category` is `tense`, `pressured`
or `disconnected`; empty string otherwise. Keyed to `category` only.

## `category_also` (2026-08-21, additive)

Zero, one or two entries, each `{category, evidence}`, carrying the
OFF-LADDER readings that ALSO applied to the meeting alongside the
dominant `category`:

- `category` is `decisive` or `disconnected` only. The six on-ladder
  values never appear here; `category` already names the dominant one.
- Never equal to the top-level `category`. A purely disconnected meeting is
  `category: "disconnected"` with `category_also: []`.
- `evidence` is REQUIRED and non-empty on every entry: the decision being
  made (decisive) or the misalignment itself (disconnected). An accusation
  never arrives without its quote.
- No duplicates. Order is not meaningful.
- Always present (possibly `[]`) whenever `category` is present on a report
  generated after this contract shipped. Absent on reports stored before
  it; decoders treat absent as empty.

Enforced on the server, not only instructed: `normalize_category_also`
drops any entry that is on-ladder, repeats the chosen category, lacks a
quote, or duplicates another, and logs `sentiment_category_also_dropped`
with a reason. The wire therefore never carries a forbidden shape, even if
the model emits one.

Client semantics agreed with SS: old builds ignore the list and render
`category` exactly as today; new builds paint the filled ladder rung for
`category` and overlay a hollow outline per entry in `category_also`, with
severity nil so those entries never enter the trend slope.

## `emoji_label`, `emoji` (derived)

Derived on the server from `category` via a fixed table; never chosen by
the model. See `derive_sentiment_fields`.

## Proof it is emitted

A 200 on the config push is not proof. The field counts as shipped when a
real `POST /v1/reports` response, read from `usage_log`, carries
`category_also` as a list; that echo is recorded on the PR that ships it.
