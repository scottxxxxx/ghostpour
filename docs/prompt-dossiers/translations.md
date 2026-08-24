# Translations engine prompt (ENGINE_VERSION 1)

Call type: `translation`. Route: `POST /v1/translations`. Serving code:
`app/services/translations.py` (`system_prompt()`); contract:
`docs/wire-contracts/meeting-translations.md`.

## Recipe

One system prompt, one artifact-conditional suffix:

- Base (all artifacts): translate a JSON array of {id, text} to the
  target language; every id exactly once, same order; faithful register,
  fillers, punctuation; never summarize/omit/add; personal, company, and
  product names never translated.
- Prose suffix (summary + report ONLY): the served no-dash rule.
  TRANSCRIPT is excluded on purpose, same carve-out as transcriptCleanup:
  the user's own words keep their punctuation.

User content: `Target language: {t}. Source language: {s}.\n` + the
segment-group JSON. Temperature 0.2 (verdict-stability rule). Model is
server-controlled and never on the wire.

## Output handling

The model may fence its JSON; `parse_model_output()` strips fences and
prose SERVER-side and re-emits clean JSON, validating every id exactly
once in order. One retry on round-trip failure, then 502
`translation_shape_error`. The client never sees fences and must not
strip them (contract).

## Measured baseline (2026-08-23, length-matched synthetic 1h es transcript)

148 segments / 9,555 words, groups of 25: 8 calls, 20,534 in / 15,832
out tokens, $0.0997 per meeting per target language; ~$0.015 per group;
cache hit $0. Details in the wire contract.

## Version history

- v1 (2026-08-24): initial recipe, as measured above.
