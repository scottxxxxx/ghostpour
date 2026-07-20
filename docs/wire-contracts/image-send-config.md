# Image send config — wire contract

GP-side per-tier directives for how ShoulderSurf prepares an attached
image before it rides `/v1/chat` as `images[]`, plus the user-facing
capture guidance the client renders before the shot. Served on the
`/v1/tiers` payload inside each tier's
`feature_definitions.images` block. SS reads the block for the user's
own tier at runtime, so a retune needs no SS build, only their next
config fetch.

Last updated: 2026-07-20.

## Why GP owns this

GP is the brains, the app is the view. The right downscale and encoding
depend on what the serving model can actually consume (its pixel
ceiling) and on cost, both of which move on the server as models and
volume change. Pinning them client-side would freeze a decision that
belongs on our side. Capture guidance lives here for the same reason:
it is advice grounded in what our read pipeline needs, so we author it
and the client renders it. BYOK is excluded, when the user brings their
own model, image handling is their call.

## The block

```json
"feature_definitions": {
  "images": {
    "max_long_edge": 1568,
    "jpeg_quality": 0.8,
    "capture_guidance": {
      "title": "For the sharpest read",
      "tips": [
        "Fill the frame with the sheet so the text is as large as possible",
        "Hold steady until the text looks sharp before you shoot",
        "Light the whole sheet evenly and avoid glare or shadows",
        "Line the camera up straight on rather than at an angle"
      ]
    }
  }
}
```

Same per-tier home as the search caps and the generation cap. Values are
uniform across tiers today but the block is per-tier structured, so free
can be throttled or pro raised independently later.

## Fields

- **`max_long_edge`** (int, px) — downscale cap on the longer edge. SS
  must treat it as a cap, never upscale below it. `1568` is the current
  serving model's (Sonnet 4.6) hard image ceiling: it internally
  clamps anything larger to 1568 before tokenizing, so sending more is
  wasted bytes for zero fidelity gain. Raising this above 1568 only
  helps paired with a higher-res vision model (Sonnet 5 / Opus, ~2576).
- **`jpeg_quality`** (float, 0..1) — JPEG encode quality for the
  downscaled image. `0.8` today.
- **`capture_guidance`** (object) — user-facing pre-capture hints the
  client renders (e.g. on the attach/camera affordance). Not a
  directive to the pipeline, purely display copy.
  - **`title`** (string) — short header for the hint surface.
  - **`tips`** (array of strings) — ordered, each a single actionable
    line. Render as a list. Phrased as what to do, never as a
    limitation, per the served-copy rule. Dash-free (no em/en dashes),
    same rule as all served strings, both because it is a house style
    and because copy the model later sees influences the punctuation it
    produces.

## Why capture guidance exists (the evidence)

A grounded resolution/model sweep on a dense Gantt (reproduce-the-sheet
task, scored cell by cell against ground truth) found that the single
biggest driver of read accuracy is not the wire resolution or the model,
it is the quality of the original capture. A 48MP phone photo downscaled
all the way to 1280px scored 1 wrong cell out of 150; a 1786px
screenshot at 1568px scored 4 to 5 wrong, and a higher-res model bought
no reliable improvement. A large, sharp, evenly-lit, square-on capture
supersamples down into a crisp small image; a weak capture cannot be
rescued by sending more pixels or spending a bigger model. So the
highest-leverage knob we have is steering the user's capture, which is
exactly what this field does.

## Runtime tunable

`PUT /webhooks/admin/tunable/tier-field` edits
`tiers.<tier>.feature_definitions.images.<field>` (value type accepts
the `jpeg_quality` float). `capture_guidance` is structured, so edit it
via the config bundle + sync-from-bundle path rather than the scalar
tunable. Lockstep across locale files (`.es`, `.ja`).

## Reactive capture-quality note (server-side, no client work)

Rather than only nudging up front, GP can surface the guidance
*reactively*, in the chat reply, when a document reproduction turn was
built from an image that reads as too soft. This needs no client render
work: it appends to the reply text the client already shows.

- **Trigger**: generation armed AND an artifact was produced AND an image
  was attached AND the served flag is on AND the image scores below the
  blur threshold. Scoped to the reproduction lane so casual "what is in
  this photo" queries are never touched.
- **Signal**: variance of a Laplacian-filtered grayscale of the submitted
  image (the standard sharpness metric). Resolution is deliberately *not*
  used, our sweep showed pixel count is a poor legibility proxy. Note the
  JPEG floor: block artifacts hold the metric around 160 even for a very
  blurred image, so the threshold sits above that.
- **Flag**: `client-config.image_quality_note = {enabled: bool,
  blur_threshold: number}`. Absent or `enabled:false` means the note
  never fires (ships dark). `blur_threshold` default 200, tunable without
  a build.
- **Copy**: reuses the same `capture_guidance` tips from the images
  block, so there is one source of truth for the advice. Framed as a
  partial-result nudge, not a scolding.

Fail-open throughout: any decode or config hiccup means no note, never a
blocked reply. Pillow only, no numpy.

## Client obligation

Read `tiers.<tier>.feature_definitions.images` at runtime. Honor
`max_long_edge` as a downscale cap and encode at `jpeg_quality` on both
the chat send and the generation re-attach, SS-AI tiers only. Render
`capture_guidance` on the capture/attach surface when present; if
absent (older config), fall back to no hint. The iOS decoder ignores
unknown keys, so serving this ahead of the client rendering it is safe
and additive.
