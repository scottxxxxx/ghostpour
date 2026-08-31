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
- Prose suffix (summary + report + title): the served no-dash rule.
  TRANSCRIPT is excluded on purpose, same carve-out as transcriptCleanup:
  the user's own words keep their punctuation.
- Title suffix (`title` ONLY, added 2026-08-31, ON TOP of the prose
  suffix): each text is the name of ONE meeting, not a sentence. No
  invented article, no trailing clause, no trailing period. Never more
  generic than the title it was given, since a title that no longer says
  WHICH meeting this was has failed even when it reads well. 60
  characters or under where the language allows, choosing the shorter
  faithful wording over padding or a mid-word cut.

**ENGINE_VERSION stayed at 1 when `title` was added, deliberately.** The
three existing prompts are byte-identical before and after, so nothing
cached under version 1 changed meaning, and a bump would have
re-translated every stored transcript rendition at real cost for no
semantic change. `title` is new, so nothing was cached under it either.
`test_engine_version_1_prompts_are_byte_frozen` pins the three legacy
prompts by hash: editing one of them now fails that test, which forces
the bump decision to be made on purpose instead of by accident. If you
change a frozen prompt, bump ENGINE_VERSION and update the hash in the
same commit. `title` is deliberately NOT pinned; it is new and expected
to be tuned.

**Server-side after the model answers:** over-budget titles are counted
and logged (`translation_title_over_budget`, counts only, a title is
user content) and then returned WHOLE. This is the opposite of
`meeting_title.py`, which rejects a bad generated title because absent
beats generic. Neither half of that reasoning survives here: we are not
inventing a name, and the fallback would be the English title on a
Spanish card, which is the defect the artifact exists to remove.

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
